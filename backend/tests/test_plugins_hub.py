"""Kết nối & kỹ năng (ADR 0261): Ứng dụng (có sẵn, gói ngành, app bên ngoài NỐI VÀO — ChatGPT / Claude của tôi, app của công ty), Kết nối (Barista NỐI RA lấy dữ
liệu), Kỹ năng — mỗi người thấy đúng tầng của mình. Phần của công ty đọc bằng chính truy vấn của module và quyền người xem: nhân viên không thấy; quản trị thấy.
Ngắt ứng dụng AI chỉ thu hồi token của CHÍNH mình."""

import uuid
from datetime import UTC, datetime, timedelta

from barista.workspaces import workspaces
from tests.conftest import Who, approved, ok, owner_val, q

OUTSIDE = "App bên ngoài nối vào Barista"


def _titles(card: dict, section: str) -> list[str]:
    return [t["title"] for b in card["blocks"] if b.get("type") == "tiles" and b.get("title") == section for t in b["items"]]


async def _token(user_id: object, tenant_id: object, client: str) -> None:
    await owner_val("INSERT INTO core.mcp_clients (client_id, info) VALUES (%s, '{}') ON CONFLICT DO NOTHING RETURNING client_id", (client,))
    await owner_val("""INSERT INTO core.mcp_tokens (kind, token_hash, client_id, user_id, tenant_id, expires_at)
                       VALUES ('access', %s, %s, %s, %s, %s) RETURNING id""", (uuid.uuid4().hex, client, user_id, tenant_id, datetime.now(UTC) + timedelta(days=1)))


async def test_each_person_sees_their_layer_and_the_company_layer_only_with_rights(who: Who) -> None:
    sale, admin = await who("sale1"), await who("admin")
    mine = await q(sale, "core.plugin_apps")
    assert _titles(mine, OUTSIDE) == ["ChatGPT", "Claude"]                                          # của tôi: ai cũng có; app công ty: chỉ ai quản lý
    assert not any(a["command"] == "connect.install" for a in mine.get("actions", []))          # nút chỉ hiện cho ai chạy được
    assert any(a["command"] == "connect.install" for a in (await q(admin, "core.plugin_apps")).get("actions", []))
    for who_ in (sale, admin):                                                                   # kết nối: tài khoản của bạn trước; công ty chưa nối gì ⇒ không khung trống
        card = await q(who_, "core.connections")
        assert "Gmail" in _titles(card, "Của bạn") and _titles(card, "Của công ty") == [] and not any(b["type"] == "callout" for b in card["blocks"])
    skills = await q(sale, "core.my_skills")
    assert skills["title"] == "Kỹ năng" and skills["blocks"]


async def test_disconnecting_ai_apps_revokes_only_my_own_tokens(who: Who) -> None:
    sale, other = await who("sale1"), await who("sale2")
    client = f"c-{uuid.uuid4().hex[:8]}"
    await _token(sale.user_id, sale.tenant_id, client)
    await _token(other.user_id, other.tenant_id, client)
    link = await q(sale, "core.ai_link")
    assert link["blocks"][0]["items"][0]["value"] >= 1 and "/mcp" in str(link["blocks"])
    assert (await ok(sale, "core.disconnect_ai_apps"))["revoked"] >= 1
    assert await owner_val("SELECT count(*) FROM core.mcp_tokens WHERE client_id = %s AND user_id = %s AND revoked_at IS NULL", (client, sale.user_id)) == 0
    assert await owner_val("SELECT count(*) FROM core.mcp_tokens WHERE client_id = %s AND user_id = %s AND revoked_at IS NULL", (client, other.user_id)) == 1


def test_the_hub_is_a_main_workspace_fixed_on_everyones_rail_with_three_sub_screens_and_the_store_is_off_the_menu() -> None:
    by_key = {w.key: w for w in workspaces()}
    hub = by_key["plugins"]
    assert hub.fixed and [s.key for s in hub.screens] == ["plugin_apps", "connections", "my_skills"]   # phân hệ chính, menu con kiểu ChatGPT
    assert all(s.about for s in hub.screens)
    assert "plugins" not in {s.key for s in by_key["account"].screens}                                  # không còn là màn cá nhân
    assert not any(s.query == "admin.pack_store" for w in workspaces() for s in w.screens)


async def test_an_installed_outside_app_shows_in_the_company_layer_for_people_who_manage_it(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    name = f"App thử {uuid.uuid4().hex[:6]}"
    await approved(admin, "connect.install", name=name, grants="crm:own:read")
    assert name in _titles(await q(admin, "core.plugin_apps"), OUTSIDE)
    assert name not in _titles(await q(sale, "core.plugin_apps"), OUTSIDE)


async def test_apps_view_is_the_store_for_admins_and_the_list_in_use_for_everyone_else(who: Who) -> None:
    """Ứng dụng kiểu App Store (hàng): có sẵn / gói ngành; quản trị thấy cả gói có thể thêm, bấm mở trang gói (có nút bật); nhân viên chỉ thấy gói công ty đang dùng,
    không mở được trang bật gói. Gói lõi không hiện."""
    admin, sale = await who("admin"), await who("sale1")
    def packs(card: dict) -> dict[str, list[dict]]:
        return {b["title"]: b["items"] for b in card["blocks"] if b.get("type") == "tiles" and b["title"] != OUTSIDE}
    full, mine = packs(await q(admin, "core.plugin_apps")), packs(await q(sale, "core.plugin_apps"))
    assert "Có sẵn trong Barista" in full and all(t["open"]["query"] == "admin.pack_info" for items in full.values() for t in items)
    assert all(t.get("kind") != "core" for items in full.values() for t in items)
    assert all("open" not in t and t["badge"] in ("Đang dùng", "Chưa bật") for items in mine.values() for t in items)   # thấy cả gói chưa bật, chỉ xem
    in_use = {t["title"] for items in full.values() for t in items if t["state"] == "on"}
    assert {t["title"] for items in mine.values() for t in items if t["badge"] == "Đang dùng"} == in_use
    assert {t["title"] for items in mine.values() for t in items} == {t["title"] for items in full.values() for t in items}
