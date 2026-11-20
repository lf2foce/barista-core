"""MCP của NHÂN VIÊN (quyết định 0093, 0228): ứng dụng AI của họ (Claude, ChatGPT…) ĐỌC Barista bằng chính danh tính + quyền người đó, và — khi
người đó đồng ý phạm vi ``propose`` — SOẠN ĐỀ XUẤT thành thẻ chờ trong Barista. Không có đường ghi: lệnh chỉ chạy khi người bấm Xác nhận trong app.

* Đăng nhập OAuth (``kernel/oauth.py`` — Barista tự cấp quyền, người dùng đồng ý trên web, token gắn người + công ty).
* Client ngoài là một TRỢ LÝ nữa của đúng người đó (0011): chạy bằng ``p.as_agent()`` qua executor — rào chế độ trợ lý áp như trong app; danh
  mục báo cáo lấy từ CÙNG ``chat.catalog`` với trợ lý trong app (không rộng hơn).
* ĐỌC: chỉ thao tác loại truy vấn. Kết quả lớn bị cắt (bảng ≤ 200 dòng, ≤ 60k ký tự).
* SOẠN ĐỀ XUẤT (0228, token có phạm vi ``propose``): ``propose_action`` chạy thử lệnh bằng quyền trợ lý của người đó rồi bỏ (``executor.propose``) và
  để lại một thẻ trong ``core.proposals`` — cùng bảng, cùng đường Xác nhận / Bỏ với đề xuất buổi sáng. Lệnh ghi KHÔNG có đường chạy thật qua đây.
* Vết ``mcp:<ứng dụng>`` cho nhật ký (0086).
* Lõi không nêu tên loại dữ liệu của ngành nào: lời dặn máy chủ + mô tả công cụ ``find`` dựng từ sổ đăng ký (``instructions`` / ``find_doc``) — tên các
  nguồn tìm (``Module.search``) và tên gói. Máy chủ dùng chung nhiều công ty ⇒ bản TĨNH (lời dặn lúc ``initialize``) chỉ có phần của gói LUÔN BẬT; mô tả
  ``find`` dựng RIÊNG cho công ty của token ở mỗi ``tools/list`` (``_Server.list_tools``) từ gói công ty đó đang bật — công ty chưa bật gói BĐS không đọc
  thấy "căn" / "giao dịch" / tên gói ấy.
"""

import json
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

import barista.modules  # noqa: F401  — đăng ký mọi module trước khi dựng lời dặn từ sổ đăng ký
from barista.config import settings
from barista.db import db
from barista.kernel import auth, executor, oauth, packs, ratelimit, registry, vocab
from barista.kernel.principal import Principal

MAX_ROWS = 200
MAX_CHARS = 60_000
MAX_INFLIGHT = 4                     # mỗi người × ứng dụng tối đa 4 lượt chạy cùng lúc (trần tần suất: ratelimit "mcp")
_inflight: dict[str, int] = {}
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
# soạn đề xuất: không ghi dữ liệu nghiệp vụ (chạy thử rồi bỏ) — chỉ thêm một thẻ chờ cho chính người dùng; lệnh thật do người bấm trong Barista (0228)
DRAFT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
DRAFT_TOOLS = frozenset({"list_actions", "propose_action", "proposal_status"})   # chỉ token có phạm vi ``propose`` thấy và gọi được



def search_kinds(on: frozenset[str] | set[str]) -> list[str]:
    """Tên các loại dữ liệu ``find`` tìm được khi bật các gói ``on`` — lấy từ nguồn tìm mà module khai (``Module.search``), theo thứ tự xếp hạng."""
    found = [s for s in registry.SEARCHES.values() if registry.MODULE_PACK.get(s.op.split(".")[0], "base") in on]
    return list(dict.fromkeys(s.label.lower() for s in sorted(found, key=lambda s: (s.rank, s.label))))


def instructions(on: frozenset[str] | set[str]) -> str:
    """Lời dặn của máy chủ MCP cho bản cài bật các gói ``on``: câu chung của lõi + tên phân hệ (``Pack.title``) và loại dữ liệu tìm được từ sổ đăng ký."""
    titles = [pk.title.lower() for pk in registry.PACKS.values() if pk.key in on and pk.key != "base"]
    scope = f" ({', '.join(titles)})" if titles else ""
    kinds = ", ".join(search_kinds(on))
    return (f"Barista — hệ thống vận hành doanh nghiệp{scope}, trong đúng quyền của người đã đăng nhập. Bắt đầu: find (tìm nhanh theo tên, mã, "
            f"số điện thoại hoặc từ khoá{f' — {kinds}' if kinds else ''}) hoặc list_reports (báo cáo người này được xem) rồi run_report. Số liệu chỉ lấy "
            "từ kết quả công cụ — không tự tính, không đoán. Không ghi trực tiếp được gì: kết nối có propose_action thì soạn đề xuất (list_actions → propose_action) để người dùng bấm Xác nhận trong app Barista; không có thì người dùng tự làm trong app.")


def find_doc(on: frozenset[str] | set[str]) -> str:
    """Mô tả công cụ ``find`` (model đọc) — loại dữ liệu dựng từ sổ đăng ký."""
    kinds = ", ".join(search_kinds(on))
    return (f"Tìm nhanh trong phần bạn được xem{f': {kinds}' if kinds else ''} — mỗi dòng kèm ``open`` (báo cáo + tham số) để gọi run_report xem chi tiết.")


_static = registry.always_on()   # bản tĩnh: chỉ gói LUÔN BẬT (mọi công ty đều có) — phần còn lại dựng theo công ty ở ``_Server.list_tools``
_base = settings.public_api_url.rstrip("/")


async def _company_packs() -> frozenset[str]:
    """Gói công ty của token đang gọi đang bật; không đọc được ⇒ chỉ gói luôn bật (đóng khi không rõ)."""
    tok = get_access_token()
    if tok is None or not tok.subject:
        return _static
    try:
        _, tid = oauth.parse_subject(tok.subject)
        async with db.owner.connection() as conn:
            return await packs.enabled(conn, tid)
    except Exception:
        return _static


class _Server(MCPServer):
    async def list_tools(self) -> list[Any]:
        """Danh sách công cụ: mô tả ``find`` nêu loại dữ liệu của CHÍNH các gói công ty đang bật (không lộ gói công ty khác / chưa mua)."""
        tok = get_access_token()
        drafts = tok is not None and oauth.PROPOSE in (tok.scopes or [])
        tools = [t for t in await super().list_tools() if drafts or t.name not in DRAFT_TOOLS]
        on = await _company_packs()
        return [t.model_copy(update={"description": find_doc(on)}) if t.name == "find" else t for t in tools]


server = _Server(
    "Barista",
    instructions=instructions(_static),
    auth_server_provider=oauth.BaristaOAuth(),
    # validate_token_resource=False: hiện chỉ MỘT tài nguyên (/mcp) nên token nào của máy chủ này cũng là cho nó — có tài nguyên thứ hai
    # (vd MCP ghi) thì bật lại để token không dùng chéo.
    auth=AuthSettings(issuer_url=AnyHttpUrl(_base), resource_server_url=AnyHttpUrl(f"{_base}/mcp"), validate_token_resource=False,
                      required_scopes=[oauth.SCOPE],
                      client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=list(oauth.AI_SCOPES),
                                                                           default_scopes=[oauth.SCOPE]),
                      revocation_options=RevocationOptions(enabled=True)),
)


async def _principal() -> tuple[Principal, str]:
    """Người + công ty của token đã kiểm (nạp lại quyền MỖI lần — khoá thành viên có hiệu lực ngay); client ngoài = trợ lý của người đó."""
    p, client = await _person()
    return p.as_agent(), client


async def _person(scope: str = "") -> tuple[Principal, str]:
    """NGƯỜI của token (chưa đổi sang trợ lý) — ``scope``: token phải mang phạm vi đó (soạn đề xuất cần ``propose`` — 0228)."""
    tok = get_access_token()
    if tok is None or not tok.subject:
        raise PermissionError("Chưa đăng nhập")
    if not ratelimit.allow(f"{tok.subject}:{tok.client_id}", "mcp"):
        raise PermissionError("Gọi quá nhiều — chờ vài phút rồi thử lại, hoặc gộp câu hỏi")
    uid, tid = oauth.parse_subject(tok.subject)
    async with db.owner.connection() as conn:
        p = await auth.load(conn, tid, user_id=uid)
    if p is None:
        raise PermissionError("Không còn là thành viên đang hoạt động của công ty này")
    if scope and scope not in (tok.scopes or []):
        raise PermissionError("Kết nối này chỉ được đọc — kết nối lại và đồng ý cho ứng dụng soạn đề xuất nếu muốn")
    return p, tok.client_id


def _clip(out: Any) -> str:
    """Kết quả gọn cho model: bảng cắt dòng (ghi rõ), toàn bộ ≤ MAX_CHARS."""
    if isinstance(out, dict) and out.get("kind") == "table" and len(out.get("rows") or []) > MAX_ROWS:
        out = {**out, "rows": out["rows"][:MAX_ROWS], "note": f"{out.get('note') or ''} Chỉ trả {MAX_ROWS} dòng đầu — lọc hẹp hơn.".strip()}
    text = json.dumps(executor.jsonable(out), ensure_ascii=False, default=str)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "… (cắt bớt — lọc hẹp hơn)"


class _Slot:
    """Trần lượt chạy đồng thời theo người × ứng dụng (một app AI gọi dồn không chiếm hết kết nối DB)."""

    def __init__(self) -> None:
        tok = get_access_token()
        self.key = f"{tok.subject}:{tok.client_id}" if tok else ""

    async def __aenter__(self) -> None:
        if _inflight.get(self.key, 0) >= MAX_INFLIGHT:
            raise PermissionError(f"Đang chạy {MAX_INFLIGHT} yêu cầu cùng lúc — chờ xong rồi gọi tiếp")
        _inflight[self.key] = _inflight.get(self.key, 0) + 1

    async def __aexit__(self, *exc: object) -> None:
        n = _inflight.get(self.key, 1) - 1
        if n <= 0:
            _inflight.pop(self.key, None)
        else:
            _inflight[self.key] = n


async def _query(name: str, inp: dict[str, Any]) -> str:
    async with _Slot():
        return await _query_now(name, inp)


async def _query_now(name: str, inp: dict[str, Any]) -> str:
    p, client = await _principal()
    from barista.agent import chat

    ops = {op.name: op for op, _mode in await chat.catalog(p)}
    op = ops.get(name)
    if op is None or op.kind != "query":
        return f"Không có báo cáo “{name}” trong quyền của bạn (chỉ đọc) — xem list_reports."
    with executor.tracing(f"mcp:{client}"):
        r = await executor.query(p, name, inp)
    return _clip(r.output) if r.ok else f"{r.status}: {r.error}"


@server.tool(annotations=READ, description=find_doc(_static))
async def find(q: str) -> str:
    return await _query("core.find", {"q": q})


@server.tool(annotations=READ)
async def list_reports() -> str:
    """Các báo cáo / trang bạn được xem (chỉ đọc): tên để gọi run_report, tiêu đề, mô tả, tham số (JSON schema)."""
    p, _ = await _principal()
    from barista.agent import chat

    out = [{"name": op.name, "title": op.title, "module": registry.MODULES.get(op.module, op.module),
            "description": " ".join((op.description or "").split())[:400], "input": op.input.model_json_schema()}
           for op, _mode in await chat.catalog(p) if op.kind == "query"]
    out = vocab.apply(sorted(out, key=lambda x: x["name"]), p.vocab)   # từ vựng công ty (0124)
    if len(json.dumps(executor.jsonable(out), ensure_ascii=False, default=str)) > MAX_CHARS:
        # danh mục dài (vai nhiều quyền): bỏ schema tham số thay vì cắt giữa chừng thành JSON hỏng — run_report báo tham số thiếu / sai khi gọi
        out = [{k: v for k, v in x.items() if k != "input"} for x in out]
    return _clip(out)


@server.tool(annotations=READ)
async def run_report(name: str, input: dict[str, Any] | None = None) -> str:
    """Chạy MỘT báo cáo / trang (tên lấy từ list_reports hoặc ``open`` của find) với tham số — kết quả trong đúng quyền của bạn."""
    return await _query(name, input or {})


async def _client_name(client_id: str) -> str:
    """Tên hiện trên thẻ: tên ứng dụng TỰ KHAI kèm tên miền quay về (không giả được) — như trang đồng ý; tên tự khai một mình có thể giả danh."""
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT info->>'client_name' AS name, info->'redirect_uris'->>0 AS uri FROM core.mcp_clients WHERE client_id = %s",
                                 (client_id,))
        row = await cur.fetchone()
    host = oauth.redirect_host(row["uri"]) if row and row["uri"] else ""
    name = f"“{row['name']}”" if row and row["name"] else ""
    return " ".join(x for x in ("ứng dụng AI", name, f"({host})" if host else "") if x)


async def _draftable(human: Principal) -> dict[str, registry.Op]:
    """Lệnh ứng dụng ngoài SOẠN được cho người này — MỘT danh sách cho ``list_actions`` lẫn ``propose_action``: danh mục trợ lý của họ (đã trừ lệnh cấm
    và chính sách công ty), bỏ lệnh thiết lập và mọi lệnh rủi ro QUYỀN (phân quyền, mời người, cấu hình lõi — 0144): những việc đó người tự làm trong app."""
    from barista.agent import chat
    from barista.kernel.agent_policy import risk_of

    return {op.name: op for op, _mode in await chat.catalog(human.as_agent())
            if op.kind == "command" and registry.tool_kind(op) != "setup" and risk_of(op) != "authority"}


@server.tool(annotations=READ)
async def list_actions() -> str:
    """Các việc bạn SOẠN ĐỀ XUẤT được cho người dùng (tạo, sửa, ghi nhận…): tên để gọi propose_action, tiêu đề, mô tả, tham số (JSON schema). Không
    việc nào chạy ngay — mỗi đề xuất thành một thẻ trong Barista, người dùng bấm Xác nhận mới chạy. Việc duyệt, phân quyền, mời người và thiết lập không có ở đây."""
    human, _ = await _person(oauth.PROPOSE)
    from barista.agent import chat

    out = [{"name": op.name, "title": op.title, "module": registry.MODULES.get(op.module, op.module),
            "description": " ".join((op.description or "").split())[:400], "input": chat.tool_schema(op, human.vocab, human.fields)}
           for op in (await _draftable(human)).values()]
    out = vocab.apply(sorted(out, key=lambda x: x["name"]), human.vocab)
    if len(json.dumps(executor.jsonable(out), ensure_ascii=False, default=str)) > MAX_CHARS:
        out = [{k: v for k, v in x.items() if k != "input"} for x in out]   # danh mục dài: bỏ schema — propose_action báo tham số thiếu / sai khi gọi
    return _clip(out)


@server.tool(annotations=DRAFT)
async def propose_action(name: str, input: dict[str, Any] | None = None, note: str = "") -> str:
    """Soạn MỘT đề xuất (tên lấy từ list_actions) — Barista chạy thử ngay bằng quyền người dùng rồi bỏ: sai tham số / không đủ quyền / dữ liệu không
    cho phép thì báo lỗi luôn; chạy thử được thì thành thẻ chờ trong Barista. CHƯA ghi gì: nói với người dùng mở Barista bấm Xác nhận. ``note``: một
    câu vì sao nên làm (hiện trên thẻ). Trả ``id`` để hỏi lại bằng proposal_status."""
    async with _Slot():
        human, client = await _person(oauth.PROPOSE)
        from barista.agent import morning

        if name not in await _draftable(human):   # cùng danh sách với list_actions — gọi thẳng tên lệnh ngoài danh sách cũng không soạn được
            return json.dumps({"status": "not_found", "error": f"Không soạn được “{name}” từ ứng dụng ngoài — xem list_actions."}, ensure_ascii=False)
        out = await morning.file_outside(human, client, await _client_name(client), name, input or {}, note)
        if out["status"] == "proposed":
            out["message"] = "Đã soạn đề xuất — CHƯA ghi gì. Người dùng mở Barista (màn đầu, mục đề xuất) bấm Xác nhận hoặc Bỏ."
        return json.dumps(out, ensure_ascii=False)


@server.tool(annotations=READ)
async def proposal_status(id: str) -> str:
    """Đề xuất đã soạn giờ ra sao: ``open`` (người dùng chưa bấm), ``done`` (đã xác nhận và chạy), ``dismissed`` (người dùng bỏ), ``expired`` (qua ngày)."""
    human, client = await _person(oauth.PROPOSE)
    from uuid import UUID

    from barista.agent import morning

    try:
        row = await morning.outside_status(human, client, UUID(id))
    except ValueError:
        row = None
    if row is None:
        return "Không có đề xuất này (không phải do ứng dụng này soạn cho bạn, hoặc mã sai)."
    return json.dumps(executor.jsonable(dict(row)), ensure_ascii=False, default=str)


def app() -> Any:
    """Ứng dụng ASGI: điểm MCP ở ``/mcp`` + các đường OAuth / metadata ở GỐC (RFC 8414 / 9728 — đường dò quyền phải ở gốc tên miền). Gắn ở CUỐI
    API (sau mọi đường /v1). Không trạng thái (stateless) ⇒ nhiều bản server chạy song song được."""
    hosts = [h.strip() for h in settings.mcp_allowed_hosts.split(",") if h.strip()]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=bool(hosts), allowed_hosts=hosts,
                                         allowed_origins=[f"https://{h}" for h in hosts])
    return server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=security)
