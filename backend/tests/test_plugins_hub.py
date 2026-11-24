"""Kết nối & kỹ năng (ADR 0261): MỘT màn gom kỹ năng, kết nối của tôi (ChatGPT / Claude qua MCP) và kết nối của công ty — mỗi người thấy đúng tầng của mình.
Phần của công ty đọc bằng chính truy vấn của module và quyền người xem: nhân viên không thấy; quản trị thấy. Ngắt ứng dụng AI chỉ thu hồi token của CHÍNH mình."""

import uuid
from datetime import UTC, datetime, timedelta

from barista.workspaces import workspaces
from tests.conftest import Who, approved, ok, owner_val, q


def _titles(card: dict, section: str) -> list[str]:
    return [t["title"] for b in card["blocks"] if b.get("type") == "tiles" and b.get("title") == section for t in b["items"]]


async def _token(user_id: object, tenant_id: object, client: str) -> None:
    await owner_val("INSERT INTO core.mcp_clients (client_id, info) VALUES (%s, '{}') ON CONFLICT DO NOTHING RETURNING client_id", (client,))
    await owner_val("""INSERT INTO core.mcp_tokens (kind, token_hash, client_id, user_id, tenant_id, expires_at)
                       VALUES ('access', %s, %s, %s, %s, %s) RETURNING id""", (uuid.uuid4().hex, client, user_id, tenant_id, datetime.now(UTC) + timedelta(days=1)))


async def test_each_person_sees_their_layer_and_the_company_layer_only_with_rights(who: Who) -> None:
    sale, admin = await who("sale1"), await who("admin")
    mine = await q(sale, "core.plugins", view="connections")
    assert _titles(mine, "Của tôi") == ["ChatGPT, Claude…"] and _titles(mine, "Của công ty") == []
    assert not any(a["command"] == "connect.install" for a in mine.get("actions", []))          # nút chỉ hiện cho ai chạy được
    assert any(a["command"] == "connect.install" for a in (await q(admin, "core.plugins", view="connections")).get("actions", []))
    skills = await q(sale, "core.plugins", view="skills")
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


def test_the_hub_is_in_everyones_account_menu_and_the_store_is_off_the_menu() -> None:
    screens = {s.key: w.key for w in workspaces() for s in w.screens}
    assert screens.get("plugins") == "account" and "store" not in screens


async def test_an_installed_outside_app_shows_in_the_company_layer_for_people_who_manage_it(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    name = f"App thử {uuid.uuid4().hex[:6]}"
    await approved(admin, "connect.install", name=name, grants="crm:own:read")
    assert name in _titles(await q(admin, "core.plugins", view="connections"), "Của công ty")
    assert name not in _titles(await q(sale, "core.plugins", view="connections"), "Của công ty")


async def test_apps_view_is_the_store_for_admins_and_the_list_in_use_for_everyone_else(who: Who) -> None:
    """Ứng dụng (gói nghiệp vụ) kiểu App Store: quản trị thấy đang dùng + có thể thêm, bấm thẻ mở trang gói (có nút bật); nhân viên chỉ thấy gói công ty đang dùng,
    không mở được trang bật gói. Gói lõi không hiện."""
    admin, sale = await who("admin"), await who("sale1")
    full = await q(admin, "core.plugins")                                             # mặc định: Ứng dụng
    sections = {b["title"]: b["items"] for b in full["blocks"] if b.get("type") == "tiles"}
    assert "Công ty đang dùng" in sections and all(t["open"]["query"] == "admin.pack_info" for items in sections.values() for t in items)
    assert all(t.get("kind") != "core" for items in sections.values() for t in items)
    mine = await q(sale, "core.plugins", view="apps")
    sale_sections = {b["title"]: b["items"] for b in mine["blocks"] if b.get("type") == "tiles"}
    assert list(sale_sections) == ["Công ty đang dùng"] and all("open" not in t for t in sale_sections["Công ty đang dùng"])
    assert {t["title"] for t in sale_sections["Công ty đang dùng"]} == {t["title"] for t in sections["Công ty đang dùng"]}
