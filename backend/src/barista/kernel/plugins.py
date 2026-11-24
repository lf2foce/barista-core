"""KẾT NỐI & KỸ NĂNG (ADR 0261) — MỘT màn gom mọi thứ "cắm thêm" vào Barista, kiểu Plugins / Skills của ChatGPT. Màn chỉ GOM và chia tầng theo người xem; mỗi
loại vẫn đi cơ chế + luật duyệt riêng của nó (không cơ chế mới):

* ỨNG DỤNG — hàng kiểu cửa hàng ứng dụng, ba nhóm: CÓ SẴN (gói năng lực: Khách hàng, Tài chính…), GÓI NGÀNH (Bất động sản, Hoa hồng, Đào tạo…) và
  APP BÊN NGOÀI — thứ ở ngoài NỐI VÀO Barista: ChatGPT / Claude của chính người xem (MCP, 0093) và app của công ty gọi vào bằng khoá riêng (``connect``, 0140).
  Quản trị thấy cả gói có thể thêm (dữ liệu của trang Store, bấm ⇒ trang gói có nút bật); người khác thấy gói công ty đang dùng. Gói lõi không hiện.
* KẾT NỐI — chiều ngược lại, Barista NỐI RA lấy dữ liệu: nguồn dữ liệu của công ty (``connectors``, 0244); tài khoản cá nhân (Gmail, Lịch) chưa có.
* KỸ NĂNG (``core.ai_skills``, 0198): cách trợ lý làm một việc — người xem thấy kỹ năng đang dùng cho vai mình.
Phần của công ty đọc bằng CHÍNH truy vấn của module đó và quyền người xem; không có quyền ⇒ phần đó không hiện.
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
    sources = await _rows(ctx, "connectors.list")
    tiles = []
    for r in sources or []:
        label, tone = HEALTH.get(str(r.get("health") or "new"), HEALTH["new"])
        active = r.get("status") == "active"
        tiles.append({"title": r.get("label") or r.get("kind") or "", "subtitle": f"Nguồn dữ liệu · {r.get('kind') or ''}".rstrip(" ·"),
                      "badge": label if active else "Tạm dừng", "tone": tone if active else "gray", "module": "connectors",
                      "open": {"query": "connectors.list", "input": {}}})
    if tiles:
        blocks.append(ui.b_tiles(tiles, "Nguồn dữ liệu của công ty", rows=True))
    elif sources is not None:
        blocks.append(ui.b_callout("info", "Công ty chưa nối nguồn dữ liệu nào",
                                   "Barista tự kéo dữ liệu từ sheet, CRM cũ, tổng đài… về đây theo lịch. App bên ngoài gọi VÀO Barista nằm ở Ứng dụng."))
    blocks.append(ui.b_callout("muted", "Gmail, Lịch Google của bạn — chưa có",
                               "Khi có, bạn tự nối tài khoản của mình để trợ lý đọc thư và lịch bằng đúng quyền của bạn."))
    return ui.card("Kết nối", blocks, subtitle="Barista lấy dữ liệu từ đâu — của công ty và của bạn")


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


ROW_BADGE = {"on": ("Đang dùng", "green"), "available": ("Thêm", "accent")}   # nhãn nút bên phải theo trạng thái; "locked" giữ nhãn của Store (vd "Cần …", "Liên hệ")
GROUPS = (("capability", "Có sẵn trong Barista"), ("industry", "Gói ngành"), ("company", "Riêng của công ty"))


async def _apps(ctx: Ctx) -> dict[str, Any]:
    admin = "admin.pack_store" in registry.OPS and executor.allowed(ctx.p, registry.OPS["admin.pack_store"])
    if admin:
        store = await ctx.call("admin.pack_store")
        tiles = [t for b in store.get("blocks", []) if b.get("type") == "tiles" for t in b.get("items", []) if t.get("state") and t.get("kind") != "core"]
        for t in tiles:
            t["badge"], t["tone"] = ROW_BADGE.get(t["state"], (t.get("badge"), "gray"))
        hint = "Bấm một ứng dụng để xem gồm gì và bật — bật kèm phần nó cần, cài sẵn vai, quyền, quy trình mặc định."
    else:
        tiles = [{"title": pk.title, "subtitle": pk.description, "badge": "Đang dùng", "tone": "green", "module": registry.pack_module(k), "state": "on", "kind": pk.kind}
                 for k, pk in registry.PACKS.items() if pk.kind != "core" and (k in registry.always_on() or ctx.p.packs is None or k in ctx.p.packs)]
        hint = "Muốn thêm ứng dụng, nhờ quản trị bật."
    order = {"on": 0, "available": 1, "locked": 2}
    blocks = [ui.b_tiles(sorted(items, key=lambda t: order.get(t["state"], 3)), title, rows=True)
              for kind, title in GROUPS if (items := [t for t in tiles if t.get("kind") == kind])]
    n = await _my_ai_apps(ctx)
    outside = [{"title": "ChatGPT, Claude", "subtitle": "Trợ lý AI quen thuộc của bạn đọc Barista bằng đúng quyền của bạn", "module": "agent",
                "badge": f"Đã nối {n}" if n else "Nối", "tone": "green" if n else "accent", "open": {"query": "core.ai_link", "input": {}}}]
    for r in await _rows(ctx, "connect.apps") or []:
        active = r.get("status") == "active"
        outside.append({"title": r.get("name") or "", "subtitle": "App của công ty gửi / nhận dữ liệu với Barista bằng khoá riêng", "module": "connect",
                        "badge": "Đang dùng" if active else "Đã thu hồi", "tone": "green" if active else "gray", "open": {"query": "connect.apps", "input": {}}})
    blocks.append(ui.b_tiles(outside, "App bên ngoài nối vào Barista", rows=True))
    return ui.card("Ứng dụng", blocks, subtitle=hint, icon="square.grid.2x2", actions=[ui.act("connect.install", "Thêm app bên ngoài")])


# PHÂN HỆ CHÍNH, luôn trên thanh bên cho MỌI vai (``fixed``), menu con như ChatGPT. Màn nào trống với người xem thì tự nói vì sao; phần của công ty chỉ hiện theo quyền.
m.workspace(Workspace("plugins", "Kết nối & kỹ năng", "puzzlepiece.extension", (
    Screen("plugin_apps", "Ứng dụng", "square.grid.2x2", "core.plugin_apps", tint="purple", pinnable=False,
           about="Ứng dụng có sẵn, gói ngành và app bên ngoài nối vào Barista (ChatGPT, Claude…); quản trị bấm vào để xem gồm gì và bật thêm."),
    Screen("connections", "Kết nối", "link", "core.connections", tint="teal", pinnable=False,
           about="Barista lấy dữ liệu từ đâu: nguồn dữ liệu của công ty (sheet, CRM cũ…) và sau này tài khoản của bạn (Gmail, Lịch)."),
    Screen("my_skills", "Kỹ năng", "sparkles", "core.my_skills", tint="orange", pinnable=False,
           about="Những việc trợ lý của bạn biết làm theo cách của công ty."),
), order=95, fixed=True))
