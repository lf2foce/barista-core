"""VIỆC THEO DÕI — việc sống nhiều ngày của trợ lý (quyết định 0021), năng lực của gói NỀN: module này chỉ có bộ máy (giao, mở, đánh thức,
kiểm mỗi ngày, ngoại lệ ⇒ trợ lý / lời báo mẫu). LOẠI VIỆC (thu phí CĐT, khách đóng tiền, khách trả tiền…) do gói khai bằng ``m.work_kind``
— gói kiểm điều kiện xong bằng code và viết lời dặn trợ lý; gói tắt ⇒ việc của loại đó dừng êm (0076).

* Mỗi việc thuộc trợ lý của MỘT người (người phụ trách) và chạy bằng quyền người đó: nạp lại quyền mỗi lần chạy — người
  nghỉ / mất quyền xem đối tượng ⇒ việc dừng, không chạy tiếp bằng quyền hệ thống.
* Điều kiện xong KIỂM BẰNG CODE (hoá đơn thu đủ, đợt đóng đủ), không do model khai.
* Model chỉ chạy khi có NGOẠI LỆ (quá hạn), bằng trợ lý của người phụ trách với bộ công cụ hẹp của loại việc, có hạn mức số
  lần. Không có model ⇒ làm phần chắc chắn bằng code (soạn dự thảo) + lời báo mẫu.
* Bảng ``work.items`` là nguồn sự thật. DBOS (``barista/work_runtime.py``) giữ mỗi việc một workflow bền: chạy ``step`` → ngủ
  tới lần kiểm sau hoặc tới khi bị đánh thức (phiếu thu mới…) → chạy lại. ``step`` chạy được không cần DBOS (test, chạy tay).
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Action as A
from barista.api import (
    Denied,
    Desk,
    Input,
    Invalid,
    Line,
    Module,
    NoInput,
    NotFound,
    Open,
    Principal,
    Screen,
    field,
    kpi,
    line,
    num,
    today,
    ui,
    zone,
)
from barista.db import db
from barista.kernel import auth, executor, packs, registry
from barista.kernel.executor import Ctx, core_ctx  # gói nền dựng Ctx cho bước DBOS (chỉ lõi / gói nền dựng — 0130)
from barista.kernel.principal import system_principal

log = logging.getLogger("barista.work")

m = Module("work", "Việc theo dõi", pack="base", resources={"work": "Việc trợ lý theo dõi"})

STATUS = {"active": "Đang theo", "waiting": "Cần bạn xem", "done": "Xong", "failed": "Dừng (lỗi)", "cancelled": "Đã dừng"}
TONES = {"active": "blue", "waiting": "orange", "done": "green", "failed": "red", "cancelled": "gray"}
EVENT_LABEL = {"created": "Mở việc", "check": "Kiểm tra", "woke": "Có tin mới", "exception": "Cần người", "human": "Người xử lý",
               "done": "Xong", "failed": "Dừng", "cancelled": "Đã dừng"}
FINAL = ("done", "failed", "cancelled")
GRACE_DAYS = 3    # quá hạn quá số ngày này mới là ngoại lệ
REPEAT_DAYS = 7   # ngoại lệ còn nguyên ⇒ báo lại sau ngần này ngày (không gọi model mỗi ngày)
# Lời báo hiện ở bảng / dòng thời gian dạng chữ thường — không markdown; mã nội bộ (uuid) vô nghĩa với người đọc.
NOTE_STYLE = "Viết chữ thường, không dùng markdown (không *, `, #), không ghi mã id nội bộ; tên tài liệu ghi trong ngoặc kép."

# Móc để work_runtime (DBOS) gắn vào. Không gắn (test / tắt DBOS) ⇒ không làm gì; việc vẫn chạy được bằng ``step``.
on_start: Callable[[UUID, UUID], Awaitable[None]] | None = None
on_wake: Callable[[UUID], Awaitable[None]] | None = None

type Facts = dict[str, Any]


def _label(kind: str) -> str:
    k = registry.WORK_KINDS.get(kind)
    return k.label if k else kind


async def _kind_off(conn: Any, tenant_id: UUID, kind: str) -> str | None:
    """Tên gói khai loại việc này nếu công ty đang TẮT gói đó (hoặc loại việc không còn) — None = chạy được."""
    k = registry.WORK_KINDS.get(kind)
    if k is None:
        return "không còn loại việc này"
    pack = registry.MODULE_PACK[k.module]
    return None if pack in await packs.enabled(conn, tenant_id) else registry.pack_title(pack)


async def _event(ctx: Ctx, item_id: UUID, kind: str, text: str, data: dict[str, Any] | None = None) -> None:
    await ctx.exec("INSERT INTO work.events (item_id, kind, text, data) VALUES (%s, %s, %s, %s)",
                   (item_id, kind, text, Jsonb(data or {})))


def _signal_start(ctx: Ctx, item_id: UUID) -> None:
    if (start := on_start) is not None:
        tenant = ctx.p.tenant_id
        ctx.after_commit(lambda: start(tenant, item_id))


def _signal_wake(ctx: Ctx, item_id: UUID) -> None:
    if (wake := on_wake) is not None:
        ctx.after_commit(lambda: wake(item_id))


async def _open(ctx: Ctx, *, kind: str, title: str, owner: UUID, subject_ref: str, note: str) -> UUID | None:
    if ctx.historical:   # nạp lịch sử từ hệ khác (0185): dữ liệu tham chiếu không mở việc theo dõi — không việc, không nhịp DBOS, không lượt trợ lý
        return None
    team = await ctx.home_team(owner)
    item_id = await ctx.val(
        """INSERT INTO work.items (kind, title, owner_id, team_id, subject_ref, created_by) VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, kind, subject_ref) WHERE status IN ('active', 'waiting') DO NOTHING RETURNING id""",
        (kind, title, owner, team, subject_ref, ctx.p.user_id))
    if item_id is None:
        return None
    await _event(ctx, item_id, "created", note)
    _signal_start(ctx, item_id)
    return item_id


# ── Mở việc: người giao (start) hoặc gói mở khi có sự kiện (open) ──

@m.query("kind_options", resource="work", title="Loại việc theo dõi giao được")
async def kind_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    on = await packs.enabled(ctx.conn, ctx.p.tenant_id)
    return [{"value": k.key, "label": k.label, "help": k.subject_help} for k in registry.WORK_KINDS.values()
            if registry.MODULE_PACK[k.module] in on]


class Start(Input):
    kind: str = field("Loại việc", options_query="work.kind_options", max_length=40)
    subject: str = field("Theo dõi gì", example="HD-0001#2", max_length=200,
                         help="Theo loại việc: số hoá đơn, mã hợp đồng#số đợt, mã thanh toán… (xem gợi ý của loại việc)")


@m.command("start", resource="work", title="Giao việc theo dõi", agent="allow", shows=("work.view", "item_id"))
async def start(ctx: Ctx, inp: Start) -> dict[str, Any]:
    """Giao cho trợ lý của CHÍNH người giao một việc theo dõi nhiều ngày: kiểm mỗi ngày; quá hạn thì báo + soạn sẵn việc
    cần làm; xong (theo dữ liệu) thì tự đóng. Chỉ giao được việc về thứ người giao đang xem được (gói kiểm bằng quyền người giao)."""
    if ctx.p.user_id is None:
        raise Invalid("Cần người giao việc")
    if ctx.historical:
        raise Invalid("Đang nạp lịch sử từ hệ khác — không giao việc theo dõi trong lần nạp")
    k = registry.WORK_KINDS.get(inp.kind)
    if k is None:
        raise Invalid("Loại việc không có — chọn trong danh sách")
    if off := await _kind_off(ctx.conn, ctx.p.tenant_id, inp.kind):
        raise Invalid(f"Loại việc “{k.label}” cần gói {off} — công ty chưa bật")
    got = await ctx.call_as(k.op, ctx.p, subject=inp.subject.strip())
    if got["state"] == "done":
        raise Invalid(f"{got['title']}: đã xong")
    if got["state"] == "cancelled":
        raise Invalid(f"{got['title']}: đã huỷ")
    item_id = await _open(ctx, kind=k.key, title=got["title"], owner=ctx.p.user_id, subject_ref=got["ref"],
                          note=f"{ctx.p.name} giao: {got['summary']}")
    if item_id is None:
        raise Invalid("Đã có việc đang theo dõi đúng đối tượng này")
    return {"item_id": item_id, "title": got["title"]}


class OpenIn(Input):
    kind: str
    owner_id: UUID
    subject_ref: str = field("Theo dõi gì", max_length=200)
    title: str = field("Tên việc", max_length=300)
    note: str = field("Ghi chú mở việc", max_length=1000)


@m.command("open", resource="work", title="Mở việc theo dõi (cho gói)", internal=True, agent="deny", callers=("acct", "flow"), family="follow_up",
           why="Loại việc phải do chính module gọi khai (ctx.owned_by). Bên nghe sự kiện (acct: khoản quá hạn; "
               "flow: hồ sơ mở theo mẫu có “trợ lý theo dõi” — 0142; gói khai consumes(\"follow_up\"): đối tượng của gói tới mốc cần theo (vd báo cáo mới đã đối chiếu ⇒ "
               "theo tới khi phát hành bản cập nhật), "
               "người phụ trách = người phụ trách hồ sơ) — không người bấm, bên gọi không kiểm thêm; người phụ trách do bên "
               "gọi chọn (người phụ trách khoản / members_with_grant). Ghi work.items + nhật ký, khởi nhịp theo dõi; mỗi lần kiểm chạy bằng "
               "QUYỀN NGƯỜI PHỤ TRÁCH (không thấy ⇒ việc dừng). Chỉ kiểm loại việc đã khai. Trả {item_id} (None nếu trùng việc đang mở)."
               " [id: module gọi tự có — owner_id do bên gọi chọn từ dữ liệu của mình (người phụ trách khoản, members_with_grant), không do người dùng gõ]")
async def open_item(ctx: Ctx, inp: OpenIn) -> dict[str, Any]:
    """Hợp đồng HẸP cho gói mở việc khi có sự kiện (ký hợp đồng ⇒ thu tiền, quá hạn ⇒ nhắc trả…): người phụ trách do gói chọn theo ma trận quyền.
    Trùng việc đang mở, hoặc đang nạp lịch sử từ hệ khác (0185) ⇒ không mở (``item_id`` None)."""
    if inp.kind not in registry.WORK_KINDS:
        raise Invalid("Loại việc chưa khai")
    ctx.owned_by(registry.WORK_KINDS[inp.kind].module, f"Loại việc {inp.kind}")
    return {"item_id": await _open(ctx, kind=inp.kind, title=inp.title, owner=inp.owner_id, subject_ref=inp.subject_ref, note=inp.note)}


class WakeIn(Input):
    kind: str
    subject_ref: str = field("Theo dõi gì", max_length=200)
    text: str = field("Tin mới", max_length=500)


@m.command("wake", resource="work", title="Đánh thức việc theo dõi (cho gói)", internal=True, agent="deny",
           callers=("acct", "flow"), family="follow_up",
           why="Loại việc phải do chính module gọi khai (ctx.owned_by). Bên nghe sự kiện tiền về (acct: acct.payment_received; "
               "flow: hồ sơ đổi chặng / đóng / huỷ — 0142; gói khai consumes(\"follow_up\"): đối tượng của gói đổi trạng thái) — không người bấm, bên gọi "
               "không kiểm thêm. Đặt kiểm ngay + ghi nhật ký (chữ bên gọi đưa) cho việc đang mở đúng (loại, đối tượng); kiểm vẫn bằng quyền "
               "người phụ trách. Trả CHỈ {woke: số việc}.")
async def wake(ctx: Ctx, inp: WakeIn) -> dict[str, Any]:
    """Có dữ kiện mới (phiếu thu, khách đóng tiền…) ⇒ kiểm ngay, không đợi tới lần kiểm hằng ngày."""
    if kind := registry.WORK_KINDS.get(inp.kind):
        ctx.owned_by(kind.module, f"Loại việc {inp.kind}")
    rows = await ctx.all("SELECT id FROM work.items WHERE kind = %s AND subject_ref = %s AND status IN ('active', 'waiting')",
                         (inp.kind, inp.subject_ref))
    for r in rows:
        await ctx.exec("UPDATE work.items SET next_check_at = now() WHERE id = %s", (r["id"],))
        await _event(ctx, r["id"], "woke", inp.text)
        _signal_wake(ctx, r["id"])
    return {"woke": len(rows)}


class HandoffIn(Input):
    kind: str
    owner_id: UUID
    subject_ref: str = field("Theo dõi gì", max_length=200)
    title: str = field("Tên việc", max_length=300)
    note: str = field("Ghi chú bàn giao", max_length=1000)


@m.command("handoff", resource="work", title="Bàn giao việc theo dõi (cho gói)", internal=True, agent="deny", callers=("acct", "flow"),
           why="Loại việc phải do chính module gọi khai (ctx.owned_by). acct: quản lý Kế toán giao lại hồ sơ khoản lệch cho người khác (0223; người nhận do "
               "người có quyền quản lý chọn trong danh sách thành viên). flow: người phụ trách hồ sơ đổi (0142) — việc đang mở của (loại, đối tượng) dừng "
               "kèm nhật ký bàn giao, mở việc mới cho người mới; không việc nào tiếp tục chạy bằng quyền người không còn giữ hồ sơ. Ghi work.items + "
               "nhật ký của đúng (loại, đối tượng). Trả {closed: số việc dừng, item_id: việc mới hoặc None}."
               " [id: module gọi tự có — owner_id là người phụ trách hồ sơ do flow đọc từ đối tượng (registry.SUBJECTS), hoặc (acct) thành viên đang hoạt động do người có quyền "
               "quản lý Kế toán chọn và acct đã kiểm tư cách thành viên trước khi gọi, không phải mã người dùng gõ thẳng]")
async def handoff(ctx: Ctx, inp: HandoffIn) -> dict[str, Any]:
    """Đổi người phụ trách: dừng việc cũ (ghi lý do), mở việc mới cho người mới — cùng đối tượng, dòng thời gian mới."""
    if inp.kind not in registry.WORK_KINDS:
        raise Invalid("Loại việc chưa khai")
    ctx.owned_by(registry.WORK_KINDS[inp.kind].module, f"Loại việc {inp.kind}")
    rows = await ctx.all("SELECT id, owner_id FROM work.items WHERE kind = %s AND subject_ref = %s AND status IN ('active', 'waiting')",
                         (inp.kind, inp.subject_ref))
    if any(r["owner_id"] == inp.owner_id for r in rows):   # người mới đã đang cầm ⇒ không có gì để bàn giao
        return {"closed": 0, "item_id": None}
    for r in rows:
        await ctx.exec("UPDATE work.items SET status = 'cancelled', last_note = %s, closed_at = now(), updated_at = now() WHERE id = %s",
                       (inp.note, r["id"]))
        await _event(ctx, r["id"], "cancelled", inp.note)
        _signal_wake(ctx, r["id"])
    item_id = await _open(ctx, kind=inp.kind, title=inp.title, owner=inp.owner_id, subject_ref=inp.subject_ref, note=inp.note)
    return {"closed": len(rows), "item_id": item_id}


# ── Một lần chạy (DBOS gọi trong một step; test gọi thẳng) ──

async def _check(ctx: Ctx, item: dict[str, Any], owner: Principal) -> dict[str, Any]:
    """Tình trạng theo DỮ LIỆU do gói của loại việc tính, bằng QUYỀN NGƯỜI PHỤ TRÁCH — không thấy ⇒ ``Denied`` ⇒ việc dừng (không có đường
    nào để việc đọc thứ người phụ trách không đọc được)."""
    k = registry.WORK_KINDS[item["kind"]]
    try:
        who = await ctx.principal_of(owner.user_id) if owner.user_id else None   # truy vấn của loại việc ⇒ hỏi hộ (DELEGATE — 0144)
        if who is None:
            raise Denied("Người phụ trách không còn hoạt động")
        return await ctx.call_as(k.op, who, subject=item["subject_ref"])
    except NotFound as e:
        raise Denied(f"Người phụ trách không còn thấy {k.label.lower()} này") from e


def _keep(card: dict[str, Any]) -> dict[str, Any]:
    """Thẻ lưu vào dòng thời gian: chỉ phần trỏ (truy vấn + tham số) — mở lại thì tính lại bằng quyền người xem."""
    return {k: card[k] for k in ("kind", "query", "command", "input", "title", "subtitle") if k in card}


async def _agent_exception(owner: Principal, item: dict[str, Any], res: dict[str, Any], model: Any) -> tuple[str, list[dict[str, Any]]]:
    """Trợ lý CỦA NGƯỜI PHỤ TRÁCH xử lý ngoại lệ: quyền người đó, bộ công cụ hẹp, tối đa vài bước. Trả (lời báo, thẻ)."""
    from pydantic_ai import Agent, UsageLimits
    from pydantic_ai.usage import RunUsage

    from barista.agent import chat
    from barista.agent import usage as metering
    from barista.agent.llm import CHAT_SETTINGS, model_for

    async def emit(_: dict[str, Any]) -> None:
        return None

    # cùng việc + cùng ngày ⇒ cùng khoá chống lặp cho các lệnh ghi (DBOS chạy lại bước sau sự cố không soạn trùng)
    tools = set(registry.WORK_KINDS[item["kind"]].tools)
    # bộ công cụ HẸP do loại việc chọn sẵn ⇒ luôn hiện với model (không để lớp "tìm công cụ theo câu hỏi" cắt — ở đây không có câu hỏi)
    run = chat.ToolRun(owner, emit, f"work:{item['id']}:{today()}", recent_modules=tuple(sorted({t.split(".")[0] for t in tools})))
    ops = [(op, mode) for op, mode in await chat.catalog(owner) if op.name in tools]
    task = f"[Việc theo dõi tự chạy — không có ai đang chat] {res['task']} {NOTE_STYLE}"
    m = model_for(owner, model)
    agent = Agent(m, instructions=await chat.instructions(owner), tools=run.tools(ops), model_settings=CHAT_SETTINGS)
    used = RunUsage()   # đo token (0049) — kể cả khi lỗi / hết hạn mức (lời báo mẫu thay thế, nhưng token đã tốn)
    ok, msgs = False, []
    try:
        with executor.tracing(f"work:{item['id']}"):   # lệnh trợ lý của việc này ghi vết việc (nhật ký thao tác ↔ việc theo dõi)
            # người phụ trách + ngày giờ ở đầu tin nhắn (chat.turn_facts) — lời dặn hệ thống chỉ còn phần cố định của công ty (bộ đệm prompt, 0218)
            result = await agent.run(chat.with_facts(task, await chat.turn_facts(owner)), usage_limits=UsageLimits(request_limit=6), usage=used)
        ok, msgs = True, result.new_messages()
        return str(result.output).strip(), [_keep(c) for c in run.cards]
    finally:
        await metering.record(owner, surface="work", category=f"work:{item['kind']}", modules=metering.category_of(run.steps)[1],
                              usage=used, model=getattr(m, "model_name", ""), messages=msgs, ok=ok, steps=run.steps)


async def _fallback_exception(owner: Principal, item: dict[str, Any], res: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Không có model / model lỗi / hết hạn mức: làm phần chắc chắn bằng code (lệnh soạn sẵn gói khai, bằng trợ lý của người phụ trách —
    cùng chế độ trợ lý, cùng quyền) + lời báo mẫu của gói."""
    cards: list[dict[str, Any]] = []
    if d := res.get("draft"):
        with executor.tracing(f"work:{item['id']}"):
            r = await executor.run(owner.as_agent(), d["command"], d["input"], key=f"work:{item['id']}:{today()}:draft")
        if r.status == "ok" and isinstance(r.output, dict) and r.output.get("paper_id"):
            cards.append({"kind": "page", "query": "papers.view", "input": {"paper_id": str(r.output["paper_id"])}, "title": d["title"]})
    return res["fallback"] if cards or not res.get("fallback_no_draft") else res["fallback_no_draft"], cards


async def _file_proposals(owner: Principal, item: dict[str, Any], cards: list[dict[str, Any]]) -> int:
    """Thẻ ĐỀ XUẤT trợ lý soạn trong việc theo dõi ⇒ kho đề xuất của người phụ trách (0142) — cùng bảng, cùng đường Xác nhận / Bỏ với đề xuất buổi
    sáng (0074 A: xác nhận chạy bằng quyền NGƯỜI, kiểm căn cứ 0071). Khoá theo (việc, thứ tự) trong ngày ⇒ DBOS chạy lại bước không nhân đôi."""
    from barista.agent import morning

    return await morning.file_cards(owner, f"work:{item['id']}", cards, f"Việc theo dõi: {item['title']}")


def _has_model() -> bool:
    from barista.agent.llm import resolve_api_key
    from barista.config import settings

    return bool(settings.llm_model) and bool(resolve_api_key())


async def _close(ctx: Ctx, item_id: UUID, status: str, text: str, f: Facts | None = None) -> dict[str, Any]:
    await _event(ctx, item_id, status, text)
    await ctx.exec("UPDATE work.items SET status = %s, last_note = %s, facts = coalesce(%s, facts), closed_at = now(),"
                   " updated_at = now() WHERE id = %s", (status, text, Jsonb(f) if f is not None else None, item_id))
    return {"final": True, "status": status, "sleep_s": 0}


async def step(tenant_id: UUID, item_id: UUID, *, model: Any = None, use_model: bool | None = None) -> dict[str, Any]:
    """Một lần chạy: nạp lại người phụ trách → kiểm điều kiện bằng code → xong / ngoại lệ (gọi trợ lý) / hẹn lần sau.
    Trả ``{"final", "status", "sleep_s"}``. Mỗi việc chỉ một workflow DBOS (ID theo việc) ⇒ không chạy chồng."""
    sysp = system_principal(tenant_id)
    async with db.tenant_tx(sysp) as conn:
        ctx = core_ctx(conn, sysp, module=m.key)
        item = await ctx.one("SELECT * FROM work.items WHERE id = %s FOR UPDATE SKIP LOCKED", (item_id,))
        if item is None:
            gone = await ctx.val("SELECT NOT EXISTS (SELECT 1 FROM work.items WHERE id = %s)", (item_id,))
            return {"final": True, "status": "gone", "sleep_s": 0} if gone else {"final": False, "status": "busy", "sleep_s": 30}
        if item["status"] in FINAL:
            return {"final": True, "status": item["status"], "sleep_s": 0}
        if off := await _kind_off(conn, tenant_id, item["kind"]):
            return await _close(ctx, item_id, "cancelled", f"Công ty đã tắt gói {off} — dừng theo dõi.")
        owner = await auth.load(conn, tenant_id, user_id=item["owner_id"])
        if owner is None:
            return await _close(ctx, item_id, "failed", "Người phụ trách không còn hoạt động trong công ty — việc dừng, cần giao lại.")
        try:
            res = await _check(ctx, item, owner)
        except Denied as e:
            return await _close(ctx, item_id, "failed", f"Dừng vì quyền: {e}. Việc không chạy bằng quyền cao hơn người phụ trách.")
        f, text = res["facts"], res["summary"]
        if res["state"] == "done":
            return await _close(ctx, item_id, "done", res.get("closing") or "Đã xong theo dữ liệu — đóng việc.", f)
        if res["state"] == "cancelled":
            return await _close(ctx, item_id, "cancelled", res.get("closing") or "Đối tượng đã huỷ — dừng theo dõi.", f)
        state = "wait" if res["state"] == "wait" else ("late" if int(f.get("late_days") or 0) > GRACE_DAYS else "ok")
        if {k: v for k, v in f.items() if k != "late_days"} != {k: v for k, v in item["facts"].items() if k != "late_days"}:
            await _event(ctx, item_id, "check", text, {"facts": f})
        # ngoại lệ có HAI lý do (0142 lát 2): "late" — quá hạn > GRACE_DAYS; "attention" — gói báo cần người SỚM (``res["attention"]``: sắp tới hạn
        # mà còn thiếu, bên ngoài vừa nộp chờ nhận…). Nhịp nhắc lại tính RIÊNG từng lý do — nhắc sớm không chặn lời báo quá hạn mấy ngày sau
        reason = "late" if state == "late" else ("attention" if res.get("attention") else None)
        last_exc = await ctx.val("""SELECT max(at) FROM work.events WHERE item_id = %s AND kind = 'exception'
                                    AND coalesce(data->>'reason', 'late') = %s""", (item_id, reason or "late"))
        need_exception = reason is not None and (last_exc is None or (today() - last_exc.astimezone(zone()).date()).days >= REPEAT_DAYS)
        due = date.fromisoformat(f["due"]) if "due" in f else None
        await ctx.exec(
            """UPDATE work.items SET facts = %s, due_on = coalesce(%s, due_on), updated_at = now(),
                   last_note = CASE WHEN status = 'waiting' THEN last_note ELSE %s END,
                   next_check_at = now() + make_interval(secs => check_every_s) WHERE id = %s""", (Jsonb(f), due, text, item_id))
    sleep_s = min(item["check_every_s"], 60) if state == "wait" else item["check_every_s"]
    if not need_exception:
        return {"final": False, "status": item["status"], "sleep_s": sleep_s}
    # Ngoài giao dịch giữ khoá: gọi model mất vài giây tới vài chục giây. Kết quả ghi trong giao dịch riêng.
    use = (owner.ai and (use_model if use_model is not None else (model is not None or _has_model()))   # công ty tắt AI ⇒ lời báo mẫu
           and item["agent_runs"] < item["max_agent_runs"])
    try:
        note, cards = await (_agent_exception(owner, item, res, model) if use else _fallback_exception(owner, item, res))
    except Exception as e:   # trợ lý lỗi ⇒ log rồi dùng lời báo mẫu — việc vẫn được báo
        log.warning("trợ lý của việc %s lỗi: %r — dùng lời báo mẫu", item_id, e)
        use = False
        note, cards = await _fallback_exception(owner, item, res)
    filed = await _file_proposals(owner, item, cards) if use else 0   # thẻ đề xuất ⇒ người bấm được ở "Trợ lý soạn sẵn" (0142)
    async with db.tenant_tx(sysp) as conn:
        ctx = core_ctx(conn, sysp, module=m.key)
        await _event(ctx, item_id, "exception", note, {"cards": cards, "agent": use, "proposals": filed, "reason": reason})
        await ctx.exec("UPDATE work.items SET status = 'waiting', last_note = %s, agent_runs = agent_runs + %s, updated_at = now()"
                       " WHERE id = %s AND status IN ('active', 'waiting')", (note, int(use), item_id))
    return {"final": False, "status": "waiting", "sleep_s": sleep_s}


# ── Người xử lý / dừng ──

class ItemRef(Input):
    item_id: UUID = field("Việc", hidden=True)


class Ack(Input):
    item_id: UUID = field("Việc", hidden=True)
    note: str = field("Đã làm gì", "", kind="textarea", max_length=1000, example="Đã gọi bên trả, hẹn chuyển thứ Sáu")


@m.command("ack", resource="work", title="Tôi đã xử lý", agent="deny")
async def ack(ctx: Ctx, inp: Ack) -> dict[str, Any]:
    """Người phụ trách xác nhận đã xử lý ngoại lệ ⇒ việc tiếp tục theo dõi; còn quá hạn thì REPEAT_DAYS ngày sau báo lại."""
    n = await ctx.exec("UPDATE work.items SET status = 'active', next_check_at = now(), updated_at = now()"
                       " WHERE id = %s AND status = 'waiting'", (inp.item_id,))
    if not n:
        raise NotFound("Không thấy việc đang chờ bạn")
    await _event(ctx, inp.item_id, "human", f"{ctx.p.name}: {inp.note.strip() or 'đã xử lý'}")
    _signal_wake(ctx, inp.item_id)
    return {"item_id": inp.item_id}


@m.command("cancel",
           purpose="Việc không cần trợ lý theo dõi tiếp — dừng theo dõi.", irreversible=True, effect="Dừng theo dõi và đóng việc này: trợ lý thôi kiểm tra và báo; muốn theo dõi tiếp phải mở việc mới.",
           resource="work", title="Dừng theo dõi", agent="propose")
async def cancel(ctx: Ctx, inp: ItemRef) -> dict[str, Any]:
    n = await ctx.exec("UPDATE work.items SET status = 'cancelled', closed_at = now(), updated_at = now()"
                       " WHERE id = %s AND status IN ('active', 'waiting')", (inp.item_id,))
    if not n:
        raise NotFound("Không thấy việc đang theo")
    await _event(ctx, inp.item_id, "cancelled", f"{ctx.p.name} dừng theo dõi.")
    _signal_wake(ctx, inp.item_id)
    title = await ctx.val("SELECT title FROM work.items WHERE id = %s", (inp.item_id,))
    return {"item_id": inp.item_id, "subject": {"label": f"Việc theo dõi “{title}”", "open": None} if title else None}


# ── Xem ──

class InboxFilter(Input):
    status: str = field("Trạng thái", "open", options={"open": "Đang theo + cần bạn", "all": "Tất cả", **STATUS})


@m.query("inbox", resource="work", title="Việc trợ lý đang theo")
async def inbox(ctx: Ctx, inp: InboxFilter) -> dict[str, Any]:
    """Việc nhiều ngày trợ lý đang theo dõi (của mình; trưởng nhóm thấy của đội). "Cần bạn xem" lên đầu."""
    rows = await ctx.all(
        """SELECT i.id, i.kind, i.title, i.status, i.last_note, i.due_on, i.next_check_at, i.updated_at, u.name AS owner
           FROM work.items i JOIN core.users u ON u.id = i.owner_id
           WHERE %(s)s = 'all' OR (%(s)s = 'open' AND i.status IN ('active', 'waiting')) OR i.status = %(s)s
           ORDER BY (i.status = 'waiting') DESC, i.due_on NULLS LAST, i.updated_at DESC LIMIT 500""", {"s": inp.status})
    waiting = sum(r["status"] == "waiting" for r in rows)
    # hôm nay trợ lý đã làm gì (0142): việc đã kiểm (bước chạy ghi updated_at), đã khép theo dữ liệu, đề xuất đã soạn (chỉ của người xem — RLS)
    t = await ctx.one("""SELECT count(*) FILTER (WHERE (updated_at AT TIME ZONE core.tz())::date = core.today()) AS checked,
                                count(*) FILTER (WHERE status = 'done' AND (closed_at AT TIME ZONE core.tz())::date = core.today()) AS closed
                         FROM work.items""")
    from barista.agent import morning

    proposed = await morning.count_today(ctx.p, "work") if ctx.p.user_id else 0
    return ui.table(rows, [
        ui.col("status", "", "badge", labels=STATUS, tones=TONES), ui.col("title", "Việc", width=230),
        ui.col("last_note", "Tình hình", width=420), ui.col("due_on", "Hạn", "date"), ui.col("owner", "Phụ trách"),
        ui.col("next_check_at", "Kiểm lần tới", "datetime"),
    ], title="Việc trợ lý đang theo", limit=500,
        kpis=[ui.kpi("Cần bạn xem", waiting, tone="orange" if waiting else None),
              ui.kpi("Đang theo", sum(r["status"] == "active" for r in rows)),
              ui.kpi("Hôm nay đã kiểm", int(t["checked"]) if t else 0), ui.kpi("Hôm nay đã khép", int(t["closed"]) if t else 0),
              ui.kpi("Đề xuất đã soạn hôm nay", int(proposed or 0))],
        empty="Chưa có việc nào. Việc tự mở khi có sự kiện của gói đang bật (ký hợp đồng, quá hạn…); hoặc nhờ trợ lý: “theo dõi … trễ thì báo tôi”.")


@m.query("blockers", resource="work", title="Việc trợ lý đang chờ người")
async def blockers(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    """Cho màn "Cần xử lý" (flow): việc theo dõi đang CHỜ NGƯỜI trong phạm vi người hỏi xem (RLS) — chỉ chữ tình hình, không số riêng."""
    return await ctx.all("""SELECT i.id, i.title, coalesce(i.last_note, '') AS note, u.name AS owner FROM work.items i
                            JOIN core.users u ON u.id = i.owner_id WHERE i.status = 'waiting' ORDER BY i.due_on NULLS LAST LIMIT 50""")


@m.query("view", resource="work", title="Việc theo dõi")
async def view(ctx: Ctx, inp: ItemRef) -> dict[str, Any]:
    i = await ctx.one("SELECT i.*, u.name AS owner FROM work.items i JOIN core.users u ON u.id = i.owner_id WHERE i.id = %s",
                      (inp.item_id,))
    if i is None:
        raise NotFound("Không thấy việc")
    evs = await ctx.all("SELECT at, kind, text, data FROM work.events WHERE item_id = %s ORDER BY at, id", (inp.item_id,))
    timeline = "\n".join(f"{e['at'].astimezone(zone()):%d/%m %H:%M} · {EVENT_LABEL[e['kind']]} — {e['text']}" for e in evs)
    # đề xuất trợ lý soạn trong việc này còn chờ NGƯỜI XEM (kho đề xuất: chỉ chủ nhân thấy) — bấm ở "Trợ lý soạn sẵn" (0142)
    from barista.agent import morning

    props = await morning.open_titles(ctx.p, f"work:{i['id']}") if ctx.p.user_id else []
    page = ui.detail(i["title"], [
        ui.section("Mới nhất", text=i["last_note"] or "—"),
        *([ui.section("Đề xuất trợ lý đã soạn — chờ bạn bấm ở “Trợ lý soạn sẵn”", text="\n".join(f"• {p['title']}" for p in props))] if props else []),
        ui.section("Tình hình", [
            ui.field("Trạng thái", STATUS[i["status"]]), ui.field("Loại", _label(i["kind"])), ui.field("Theo dõi", i["subject_ref"]),
            ui.field("Phụ trách", i["owner"]), ui.field("Hạn", i["due_on"], "date"),
            ui.field("Kiểm lần tới", i["next_check_at"] if i["status"] in ("active", "waiting") else None, "datetime"),
            ui.field("Trợ lý đã được gọi", f"{i['agent_runs']}/{i['max_agent_runs']} lần"),
        ]),
        ui.section("Dòng thời gian", text=timeline or "—"),
    ], subtitle=f"{_label(i['kind'])} · {STATUS[i['status']]}")
    drafts = [c["input"]["paper_id"] for e in evs for c in (e["data"] or {}).get("cards", [])
              if c.get("query") == "papers.view" and (c.get("input") or {}).get("paper_id")]
    if drafts:
        page["links"] = [{"title": "Tải dự thảo .docx", "path": f"/v1/papers/{drafts[-1]}/docx",
                          "filename": "Du-thao.docx"}]
    actions = []
    if i["status"] == "waiting":
        actions.append({"command": "work.ack", "title": "Tôi đã xử lý", "style": "primary", "bind": {},
                        "preset": {"item_id": str(i["id"])}, "when": {}, "importable": False})
    if i["status"] in ("active", "waiting"):
        actions.append({"command": "work.cancel", "title": "Dừng theo dõi", "style": "destructive", "bind": {},
                        "preset": {"item_id": str(i["id"])}, "when": {}, "importable": False})
    if actions:
        page["actions"] = actions
    return page


async def open_items() -> list[tuple[UUID, UUID, bool]]:
    """(công ty, việc, trễ nhịp?) đang mở của MỌI công ty — owner, chỉ để vòng đối soát DBOS bảo đảm mỗi việc có workflow
    sống. "Trễ nhịp" = quá giờ kiểm hẹn 10 phút mà chưa kiểm (lỡ tín hiệu / workflow kẹt)."""
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT tenant_id, id, next_check_at < now() - interval '10 minutes' AS overdue FROM work.items"
                                 " WHERE status IN ('active', 'waiting') ORDER BY next_check_at")
        return [(r["tenant_id"], r["id"], r["overdue"]) for r in await cur.fetchall()]


# ---- Câu tín hiệu của bàn việc (0094): khai trên màn (desk=Desk(..., say=...)); agent/desk.py chạy bằng quyền người xem

def _attn_inbox(o: Any) -> list[Line | None]:
    checked, closed, proposed = kpi(o, "Hôm nay đã kiểm"), kpi(o, "Hôm nay đã khép"), kpi(o, "Đề xuất đã soạn hôm nay")
    return [
        line("warn", f"{num(kpi(o, 'Cần bạn xem'))} việc trợ lý theo dõi đang chờ người quyết",
             "Trợ lý đã soạn sẵn phần chắc chắn (dự thảo, lời nhắc) — xem rồi bấm",
             "Việc theo dõi nào đang chờ người quyết, và nên làm gì?") if kpi(o, "Cần bạn xem") else None,
        # trợ lý đã làm gì hôm nay (0142) — chỉ nói khi có việc, không nói "0"
        line("accent", f"Hôm nay trợ lý đã kiểm {num(checked)} việc, khép {num(closed)}, soạn {num(proposed)} đề xuất",
             "Việc khép là theo dữ liệu (code kiểm), đề xuất chờ bạn bấm", "Hôm nay trợ lý đã làm gì cho tôi?") if checked else None,
    ]


OUTCOME_PERIODS = {"30": "30 ngày qua", "90": "90 ngày qua", "365": "12 tháng qua"}


class OutcomeIn(Input):
    days: str = field("Kỳ", "90", options=OUTCOME_PERIODS)


@m.query("outcomes", resource="work", title="Kết quả việc theo dõi", min_scope="all")
async def outcomes(ctx: Ctx, inp: OutcomeIn) -> dict[str, Any]:
    """BẢNG ĐO "BÁN THEO KẾT QUẢ" (ROADMAP — 05/11): mỗi loại việc trong kỳ — mở bao nhiêu, KHÉP bao nhiêu (điều kiện xong do CODE kiểm, không
    do model khai), tự khép không người can thiệp bao nhiêu, mất bao lâu, người phải vào mấy lần, AI tốn bao nhiêu mỗi việc khép. Toàn công ty
    (``min_scope="all"``) — người phạm vi hẹp sẽ thấy số thiếu mà tưởng đủ."""
    if inp.days not in OUTCOME_PERIODS:
        raise Invalid("Kỳ không hợp lệ")
    since = await ctx.val("SELECT now() - make_interval(days => %s)", (int(inp.days),))
    rows = await ctx.all(
        """WITH x AS (
               SELECT i.kind, i.status, i.created_at, i.closed_at, i.due_on, i.agent_runs,
                      EXISTS (SELECT 1 FROM work.events e WHERE e.item_id = i.id AND e.kind IN ('human', 'cancelled')) AS touched,
                      EXISTS (SELECT 1 FROM work.events e WHERE e.item_id = i.id AND e.kind = 'exception') AS exception
               FROM work.items i WHERE i.created_at >= %(s)s OR i.closed_at >= %(s)s OR i.status IN ('active', 'waiting'))
           SELECT kind, count(*) FILTER (WHERE created_at >= %(s)s) AS opened,
                  count(*) FILTER (WHERE status = 'done' AND closed_at >= %(s)s) AS done,
                  count(*) FILTER (WHERE status = 'done' AND closed_at >= %(s)s AND NOT touched) AS self_closed,
                  count(*) FILTER (WHERE status = 'cancelled' AND closed_at >= %(s)s) AS cancelled,
                  count(*) FILTER (WHERE status IN ('active', 'waiting')) AS open,
                  count(*) FILTER (WHERE status IN ('active', 'waiting') AND due_on < core.today()) AS late,
                  count(*) FILTER (WHERE status = 'waiting') AS waiting,
                  count(*) FILTER (WHERE touched AND created_at >= %(s)s) AS touched,
                  count(*) FILTER (WHERE exception AND created_at >= %(s)s) AS exceptions,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM closed_at - created_at) / 86400)
                      FILTER (WHERE status = 'done' AND closed_at >= %(s)s) AS days_p50,
                  percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM closed_at - created_at) / 86400)
                      FILTER (WHERE status = 'done' AND closed_at >= %(s)s) AS days_p90
           FROM x GROUP BY kind ORDER BY kind""", {"s": since})
    runs: dict[str, int] | None = None
    if ctx.p.can("core.ai_usage", "read"):   # lượt AI chỉ hiện cho người xem được lượt dùng trợ lý (RLS của core.ai_usage quyết phần thấy)
        who = await ctx.principal_of(ctx.p.user_id) if ctx.p.user_id else None
        runs = await ctx.call_as("core.ai_runs", who, prefix="work:", since=since) if who else None

    def per_done(kind: str, done: int) -> str:
        """Lượt AI / việc khép (0193: khách thấy LƯỢT, không tiền / token)."""
        if runs is None or not done:
            return "—"
        return f"{runs.get(f'work:{kind}', 0) / done:.1f} lượt".replace(".", ",")

    def days(v: Any) -> str:
        return "—" if v is None else f"{float(v):.1f} ngày".replace(".", ",")

    out = []
    for r in rows:
        k = registry.WORK_KINDS.get(r["kind"])
        out.append({"id": r["kind"], "kind": k.label if k else r["kind"], "opened": r["opened"], "done": r["done"],
                    "self_rate": f"{r['self_closed'] * 100 // r['done']}%" if r["done"] else "—", "cancelled": r["cancelled"],
                    "open": r["open"], "late": r["late"], "waiting": r["waiting"], "touched": r["touched"], "exceptions": r["exceptions"],
                    "p50": days(r["days_p50"]), "p90": days(r["days_p90"]), "cost": per_done(r["kind"], r["done"])})
    done, self_closed = sum(r["done"] for r in rows), sum(r["self_closed"] for r in rows)
    return ui.table(out, [
        ui.col("kind", "Loại việc", width=200), ui.col("opened", "Mở", "number"), ui.col("done", "Khép (code kiểm)", "number"),
        ui.col("self_rate", "Khép, không ai vào việc"), ui.col("p50", "Thời gian khép (giữa)"), ui.col("p90", "90% khép trong"),
        ui.col("touched", "Người phải vào", "number"), ui.col("exceptions", "Trợ lý xử lý ngoại lệ", "number"),
        ui.col("cancelled", "Dừng", "number"), ui.col("open", "Đang mở", "number"), ui.col("late", "Quá hạn", "number"),
        ui.col("waiting", "Chờ người", "number"), ui.col("cost", "AI / việc khép"),
    ], title="Kết quả việc theo dõi", kpis=[
        ui.kpi("Đã khép", done), ui.kpi("Khép, không ai vào việc", f"{self_closed * 100 // done}%" if done else "—", "text",
                                        hint="không ai sửa / dừng VIỆC THEO DÕI — người vẫn có thể đã làm phần nghiệp vụ ở màn khác"),
        ui.kpi("Đang chờ người", sum(r["waiting"] for r in rows), tone="orange" if any(r["waiting"] for r in rows) else None),
        ui.kpi("Quá hạn", sum(r["late"] for r in rows), tone="red" if any(r["late"] for r in rows) else None),
    ], note=f"{OUTCOME_PERIODS[inp.days]}. Khép = điều kiện xong do code của gói kiểm (khoản đã thu đủ, đợt đã đóng…), không do trợ lý tự khai. "
            "AI / việc khép = số lượt trợ lý cả kỳ chia số việc khép trong kỳ (ước lượng).", empty="Chưa có việc theo dõi nào trong kỳ.")


# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.screens("work", (
    Screen("followups", "Việc trợ lý theo dõi", "clock.badge.checkmark.fill", "work.inbox", tint="purple", desk=Desk("decide", say=_attn_inbox, rank=100),
           actions=(A("work.start", style="primary"),),
           row_actions=(A("work.ack", bind={"item_id": "id"}, when={"status": ["waiting"]}, style="primary"),
                        A("work.cancel", bind={"item_id": "id"}, when={"status": ["active", "waiting"]}, style="destructive")),
           row_click="open", open=Open("work.view", {"item_id": "id"}, "Xem việc")),
    Screen("work_outcomes", "Kết quả việc theo dõi", "chart.bar.doc.horizontal", "work.outcomes", tint="purple", min_scope="all"),
), rank=30)
