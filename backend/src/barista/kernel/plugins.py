"""KẾT NỐI & KỸ NĂNG (ADR 0261) — MỘT màn gom mọi thứ "cắm thêm" vào Barista, kiểu Plugins / Skills của ChatGPT. Màn chỉ GOM và chia tầng theo người xem; mỗi
loại vẫn đi cơ chế + luật duyệt riêng của nó (không cơ chế mới):

* ỨNG DỤNG (gói nghiệp vụ: Hoa hồng, Kế toán, Văn bản Word…): quản trị thấy gói đang dùng + gói có thể thêm (bằng chính trang Store của Quản trị, bấm thẻ ⇒
  trang gói có nút bật); người khác thấy gói công ty đang dùng. Gói lõi không hiện.
* KỸ NĂNG (``core.ai_skills``, 0198): cách trợ lý làm một việc — người xem thấy kỹ năng đang dùng cho vai mình.
* KẾT NỐI CỦA TÔI: "Dùng Barista trong ChatGPT / Claude" (MCP của nhân viên, 0093) — địa chỉ để nối, số ứng dụng AI đang nối, nút ngắt.
* KẾT NỐI CỦA CÔNG TY (chỉ ai xem được): nguồn dữ liệu ngoài (``connectors``, 0244) và app bên ngoài gọi vào (``connect``, 0140) — đọc bằng CHÍNH truy vấn của
  module đó và quyền người xem; không có quyền ⇒ phần đó không hiện.
Tầng hệ thống (gói, Store) không ở đây."""

from typing import Any

from barista.config import settings
from barista.kernel import ai_skills, executor, registry, ui
from barista.kernel.core_ops import m
from barista.kernel.errors import Denied, NotFound
from barista.kernel.executor import Ctx
from barista.kernel.fields import NoInput
from barista.kernel.screens import Screen, Workspace

HEALTH = {"ok": ("Đang chạy", "green"), "error": ("Lỗi", "red"), "new": ("Chưa chạy", "gray")}


def _mcp_address() -> str:
    return settings.public_api_url.rstrip("/") + "/mcp"


async def _my_ai_apps(ctx: Ctx) -> int:
    return int(await ctx.val("""SELECT count(DISTINCT client_id) FROM core.mcp_tokens
                                WHERE tenant_id = core.tenant() AND kind IN ('access', 'refresh') AND revoked_at IS NULL AND expires_at > now()""") or 0)


async def _rows(ctx: Ctx, query: str) -> list[dict[str, Any]] | None:
    """Dòng của truy vấn danh sách một module, bằng quyền người xem; không xem được ⇒ None (phần đó không hiện)."""
    try:
        page = await ctx.call(query)
    except (Denied, NotFound):
        return None
    return list((page or {}).get("rows") or [])


@m.query("plugin_apps", resource=None, title="Ứng dụng", category="core")
async def plugin_apps(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    return await _apps(ctx)


@m.query("my_skills", resource=None, title="Kỹ năng", category="core")
async def my_skills(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    skills = await ai_skills.for_role(ctx.conn, ctx.p.role or "")
    tiles = [{"title": s["title"], "subtitle": s["when_to_use"], "badge": "Đang dùng", "tone": "green"} for s in skills]
    return ui.card("Kỹ năng", [ui.b_tiles(tiles, "Trợ lý của bạn biết làm") if tiles else
                                ui.b_callout("info", "Chưa có kỹ năng nào cho vai của bạn",
                                             "Kỹ năng là cách trợ lý làm một việc hay gặp (soạn báo giá, đối soát…). Quản trị soạn hoặc bật thêm.")],
                   subtitle="Cách trợ lý làm những việc hay gặp của công ty", actions=[ui.act("core.save_skill", "Soạn kỹ năng")])


@m.query("connections", resource=None, title="Kết nối", category="core")
async def connections(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    n = await _my_ai_apps(ctx)
    blocks.append(ui.b_tiles([{
        "title": "ChatGPT, Claude…", "badge": f"Đã nối {n}" if n else "Chưa nối", "tone": "green" if n else "gray",
        "subtitle": "Dùng trợ lý AI quen thuộc của bạn để hỏi số liệu và soạn đề xuất trong Barista, bằng đúng quyền của bạn",
        "open": {"query": "core.ai_link", "input": {}}}], "Của tôi"))
    company: list[dict[str, Any]] = []
    for r in await _rows(ctx, "connectors.list") or []:
        label, tone = HEALTH.get(str(r.get("health") or "new"), HEALTH["new"])
        company.append({"title": r.get("label") or r.get("kind") or "", "subtitle": f"Nguồn dữ liệu · {r.get('kind') or ''}".rstrip(" ·"),
                        "badge": label if r.get("status") == "active" else "Tạm dừng", "tone": tone if r.get("status") == "active" else "gray",
                        "open": {"query": "connectors.list", "input": {}}})
    for r in await _rows(ctx, "connect.apps") or []:
        active = r.get("status") == "active"
        company.append({"title": r.get("name") or "", "subtitle": "App bên ngoài gửi / nhận dữ liệu với Barista",
                        "badge": "Đang dùng" if active else "Đã thu hồi", "tone": "green" if active else "gray",
                        "open": {"query": "connect.apps", "input": {}}})
    if company:
        blocks.append(ui.b_tiles(company, "Của công ty"))
    return ui.card("Kết nối", blocks, subtitle="Những gì đang nối với Barista — của bạn và của công ty",
                   actions=[ui.act("connect.install", "Thêm app bên ngoài")])


@m.query("ai_link", resource=None, title="Dùng Barista trong ChatGPT / Claude", category="core")
async def ai_link(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    n = await _my_ai_apps(ctx)
    how = (f"1. Mở phần kết nối (connector / MCP) của ChatGPT hoặc Claude.\n2. Thêm máy chủ MCP với địa chỉ `{_mcp_address()}`.\n"
           "3. Đăng nhập Barista và đồng ý. Ứng dụng AI chỉ đọc được những gì bạn đọc được; muốn ghi gì cũng chỉ thành đề xuất chờ bạn xác nhận trong Barista.")
    return ui.card("Dùng Barista trong ChatGPT / Claude", [
        ui.b_kpis([ui.kpi("Ứng dụng AI đang nối", n)]),
        ui.b_text(how, "Cách nối"),
    ], subtitle="Trợ lý AI của bạn đọc Barista bằng chính quyền của bạn",
        actions=[ui.act("core.disconnect_ai_apps", "Ngắt tất cả", style="destructive")] if n else [])


@m.command("disconnect_ai_apps", resource=None, title="Ngắt ChatGPT / Claude khỏi tài khoản", agent="deny", category="core",
           purpose="Thu hồi quyền của mọi ứng dụng AI (ChatGPT, Claude…) đang nối vào tài khoản của bạn ở công ty này.",
           irreversible=True, effect="Ứng dụng AI đã nối mất quyền ngay; muốn dùng lại phải nối và đồng ý lại.")
async def disconnect_ai_apps(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    gone = await ctx.exec("""UPDATE core.mcp_tokens SET revoked_at = now()
                             WHERE user_id = core.me() AND tenant_id = core.tenant() AND revoked_at IS NULL""")
    return {"revoked": gone, "summary": "Đã ngắt các ứng dụng AI khỏi tài khoản" if gone else "Không có ứng dụng AI nào đang nối"}


async def _apps(ctx: Ctx) -> dict[str, Any]:
    if "admin.pack_store" in registry.OPS and executor.allowed(ctx.p, registry.OPS["admin.pack_store"]):
        store = await ctx.call("admin.pack_store")
        tiles = [t for b in store.get("blocks", []) if b.get("type") == "tiles" for t in b.get("items", []) if t.get("state") and t.get("kind") != "core"]
        groups = [([t for t in tiles if t["state"] == "on"], "Công ty đang dùng"), ([t for t in tiles if t["state"] == "available"], "Có thể thêm"),
                  ([t for t in tiles if t["state"] == "locked"], "Cần mở thêm")]
        hint = "Bấm một ứng dụng để xem gồm gì và bật — bật kèm phần nó cần, cài sẵn vai, quyền, quy trình mặc định."
    else:
        on = [(k, pk) for k, pk in registry.PACKS.items() if pk.kind != "core" and (k in registry.always_on() or ctx.p.packs is None or k in ctx.p.packs)]
        groups = [([{"title": pk.title, "subtitle": pk.description, "badge": "Đang dùng", "tone": "ok", "module": registry.pack_module(k)} for k, pk in on],
                   "Công ty đang dùng")]
        hint = "Muốn thêm ứng dụng, nhờ quản trị bật."
    blocks = [ui.b_tiles(items, title) for items, title in groups if items]
    return ui.card("Ứng dụng", blocks or [ui.b_callout("info", "Chưa có ứng dụng nào", hint)], subtitle=hint, icon="square.grid.2x2")


# PHÂN HỆ CHÍNH, luôn trên thanh bên cho MỌI vai (``fixed``), menu con như ChatGPT. Màn nào trống với người xem thì tự nói vì sao; phần của công ty chỉ hiện theo quyền.
m.workspace(Workspace("plugins", "Kết nối & kỹ năng", "puzzlepiece.extension", (
    Screen("plugin_apps", "Ứng dụng", "square.grid.2x2", "core.plugin_apps", tint="purple", pinnable=False,
           about="Các ứng dụng nghiệp vụ công ty đang dùng (Hoa hồng, Kế toán, Văn bản Word…); quản trị bấm vào để xem gồm gì và bật thêm."),
    Screen("connections", "Kết nối", "link", "core.connections", tint="teal", pinnable=False,
           about="ChatGPT / Claude của bạn nối vào Barista, và nguồn dữ liệu, app bên ngoài của công ty (theo quyền)."),
    Screen("my_skills", "Kỹ năng", "sparkles", "core.my_skills", tint="orange", pinnable=False,
           about="Những việc trợ lý của bạn biết làm theo cách của công ty."),
), order=95, fixed=True))
