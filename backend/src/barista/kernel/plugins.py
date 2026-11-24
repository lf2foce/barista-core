"""KẾT NỐI & KỸ NĂNG (ADR 0261) — MỘT màn gom mọi thứ "cắm thêm" vào Barista, kiểu Plugins / Skills của ChatGPT. Màn chỉ GOM và chia tầng theo người xem; mỗi
loại vẫn đi cơ chế + luật duyệt riêng của nó (không cơ chế mới):

* KỸ NĂNG (``core.ai_skills``, 0198): cách trợ lý làm một việc — người xem thấy kỹ năng đang dùng cho vai mình.
* KẾT NỐI CỦA TÔI: "Dùng Barista trong ChatGPT / Claude" (MCP của nhân viên, 0093) — địa chỉ để nối, số ứng dụng AI đang nối, nút ngắt.
* KẾT NỐI CỦA CÔNG TY (chỉ ai xem được): nguồn dữ liệu ngoài (``connectors``, 0244) và app bên ngoài gọi vào (``connect``, 0140) — đọc bằng CHÍNH truy vấn của
  module đó và quyền người xem; không có quyền ⇒ phần đó không hiện.
Tầng hệ thống (gói, Store) không ở đây."""

from typing import Any, Literal

from barista.config import settings
from barista.kernel import ai_skills, ui
from barista.kernel.core_ops import m
from barista.kernel.errors import Denied, NotFound
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput, field

VIEWS = {"connections": "Kết nối", "skills": "Kỹ năng"}
HEALTH = {"ok": ("Đang chạy", "green"), "error": ("Lỗi", "red"), "new": ("Chưa chạy", "gray")}


class PluginsIn(Input):
    view: Literal["connections", "skills"] = field("Xem", "connections", options=VIEWS)


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


@m.query("plugins", resource=None, title="Kết nối & kỹ năng", category="core")
async def plugins(ctx: Ctx, inp: PluginsIn) -> dict[str, Any]:
    if inp.view == "skills":
        skills = await ai_skills.for_role(ctx.conn, ctx.p.role or "")
        tiles = [{"title": s["title"], "subtitle": s["when_to_use"], "badge": "Đang dùng", "tone": "green"} for s in skills]
        return ui.card("Kỹ năng", [ui.b_tiles(tiles, "Trợ lý của bạn biết làm") if tiles else
                                    ui.b_callout("info", "Chưa có kỹ năng nào cho vai của bạn",
                                                 "Kỹ năng là cách trợ lý làm một việc hay gặp (soạn báo giá, đối soát…). Quản trị soạn hoặc bật thêm.")],
                       subtitle="Cách trợ lý làm những việc hay gặp của công ty",
                       actions=[ui.act("core.save_skill", "Soạn kỹ năng")])
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
