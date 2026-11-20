"""CHIA DASHBOARD VÀO BÁO CÁO QUẢN TRỊ (0176) với nguồn của một gói ngành, qua gói thế vai: bản LÕI (ADR 0190) của
``barista-packs:real_estate/tests/test_dashboard_share.py`` — chỉ chia ĐỊNH NGHĨA: người xem mở bằng quyền chính mình; khối cần phạm vi cả công ty mà người xem
không có ⇒ báo "không đủ quyền xem số toàn công ty" (không hiện số thiếu lặng lẽ); chỉ người có quyền quản lý báo cáo quản trị đưa / gỡ (trợ lý chỉ đề
xuất); dashboard vừa gỡ còn hiện "(đã gỡ)" với ai đã ghim; app đang chia không xoá được, app từng chia xoá mềm."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest_asyncio

from barista import workspaces
from barista.kernel import executor
from barista.modules.apps import NO_COMPANY
from tests.conftest import Who, ok, owner_val, q
from tests.standin import helpers
from tests.standin.test_apps import _app
from tests.test_apps import _blocks

BLOCKS = [
    {"kind": "kpi", "title": "Đơn", "source": {"query": "orderdesk.list"}},
    {"kind": "chart", "title": "Theo trạng thái", "source": {"query": "orderdesk.list"}, "by": "status"},
    {"kind": "table", "title": "Mới nhất", "source": {"query": "orderdesk.list"}, "columns": ["code", "customer", "amount_vnd"], "rows": 5},
]


@pytest_asyncio.fixture
async def mgmt_on(who: Who) -> AsyncIterator[None]:
    """Gói Báo cáo quản trị tắt mặc định ⇒ bật cho CÔNG TY THẾ VAI trong test rồi tắt lại (test khác thấy đúng mặc định)."""
    admin = await who("admin")
    r = await executor.run(admin, "admin.set_pack", {"pack": "mgmt", "enabled": True})
    assert r.status == "ok", (r.status, r.error)
    try:
        yield
    finally:
        await executor.run(admin, "admin.set_pack", {"pack": "mgmt", "enabled": False})


def _screens(m: dict[str, Any], ws: str) -> dict[str, dict[str, Any]]:
    return {s["key"]: s for w in m["workspaces"] if w["key"] == ws for s in w["screens"]}


async def test_publish_needs_manage_rights_and_the_assistant_only_proposes(who: Who, mgmt_on: None) -> None:
    gd, admin, mkt = await who("giamdoc"), await who("admin"), await who("mkt")
    app = await _app(gd, "Dashboard chia thử", BLOCKS)
    r = await executor.run(gd.as_agent(), "mgmt.publish", {"app_id": app})
    assert r.status == "proposed", (r.status, r.error)                          # trợ lý chỉ đề xuất
    assert await owner_val("SELECT published FROM apps.items WHERE id = %s", (app,)) is False
    assert (await executor.run(mkt, "mgmt.publish", {"app_id": app})).status == "denied"
    assert (await executor.run(admin, "mgmt.publish", {"app_id": app})).status == "not_found"   # không phải chủ app
    out = await ok(gd, "mgmt.publish", app_id=app)
    assert "Đã đưa" in out["summary"] and out["menu"] is True
    row = await owner_val("SELECT row_to_json(i) FROM apps.items i WHERE id = %s", (app,))
    assert row["published"] is True and row["published_by"] == str(gd.user_id) and row["published_at"]
    await ok(gd, "mgmt.publish", app_id=app, publish=False)
    await ok(gd, "apps.delete", app_id=app)


async def test_shared_dashboard_opens_with_the_viewers_rights_and_no_edit_buttons(who: Who, mgmt_on: None) -> None:
    gd, admin, sale1 = await who("giamdoc"), await who("admin"), await who("sale1")
    app = await _app(gd, "Dashboard chung", BLOCKS)
    await ok(gd, "mgmt.publish", app_id=app)
    try:
        m = workspaces.manifest(admin, "", mine=await workspaces.personal(admin))
        s = _screens(m, "mgmt")[f"dash-{app}"]
        assert s["query"] == "apps.open" and s["input"] == {"app_id": app}
        assert f"app-{app}" not in _screens(m, "apps")                           # không lẫn vào "App của tôi" của người xem
        page = await q(admin, "apps.open", app_id=app)
        assert not _blocks(page, "callout") and "Dashboard chung của công ty" in page["subtitle"]
        assert "edit" not in str(page["blocks"])                                 # nút sửa khối chỉ cho chủ app
        mine = await q(gd, "apps.open", app_id=app)
        assert "edit" in str(mine["blocks"])
        # người xem không có quyền Báo cáo quản trị ⇒ không mở được; không sửa / mô tả được app người khác
        assert (await executor.query(sale1, "apps.open", {"app_id": app})).status == "not_found"
        for name, inp in (("apps.describe", {"app_id": app}), ("apps.remove_block", {"app_id": app, "page": "main", "index": 1}),
                          ("apps.delete", {"app_id": app})):
            r = await (executor.query if name == "apps.describe" else executor.run)(admin, name, inp)
            assert r.status == "not_found", (name, r.status)
        assert (await executor.query(admin, "apps.check", {"app_id": app})).status == "not_found"
    finally:
        await ok(gd, "mgmt.publish", app_id=app, publish=False)
        await ok(gd, "apps.delete", app_id=app)


async def test_a_viewer_without_company_scope_is_told_not_shown_partial_numbers(who: Who, company: UUID, mgmt_on: None) -> None:
    gd = await who("giamdoc")
    app = await _app(gd, "Dashboard phạm vi", BLOCKS)
    await ok(gd, "mgmt.publish", app_id=app)
    t = company
    await owner_val("INSERT INTO core.grants (tenant_id, role, resource, scope, actions) VALUES (%s, 'sale', 'mgmt', 'all', '{read}') RETURNING 1", (t,))
    try:
        sale1 = await who("sale1")
        assert sale1.can("mgmt", "read") and not sale1.has_scope("orderdesk", "all")
        page = await q(sale1, "apps.open", app_id=app)
        callouts = _blocks(page, "callout")
        assert len(callouts) == len(BLOCKS) and all(NO_COMPANY in c["text"] for c in callouts)
        assert not _blocks(page, "kpis") and not _blocks(page, "table")
    finally:
        await owner_val("DELETE FROM core.grants WHERE tenant_id = %s AND role = 'sale' AND resource = 'mgmt' RETURNING 1", (t,))
        await ok(gd, "mgmt.publish", app_id=app, publish=False)
        await ok(gd, "apps.delete", app_id=app)


async def test_unpublished_dashboard_stays_as_removed_for_pinners_and_delete_is_soft(who: Who, mgmt_on: None) -> None:
    gd, admin = await who("giamdoc"), await who("admin")
    app = await _app(gd, "Dashboard sẽ gỡ", BLOCKS)
    await ok(gd, "mgmt.publish", app_id=app)
    key = f"dash-{app}"
    await ok(admin, "core.pin", workspace="mgmt", key=key)
    try:
        r = await executor.run(gd, "apps.delete", {"app_id": app})
        assert r.status == "invalid" and "gỡ" in str(r.error)                    # đang chia ⇒ không xoá
        await ok(gd, "mgmt.publish", app_id=app, publish=False)
        m = workspaces.manifest(admin, "", mine=await workspaces.personal(admin))
        assert _screens(m, "mgmt")[key]["title"].endswith("(đã gỡ)")             # người đã ghim thấy đã gỡ
        assert key not in _screens(workspaces.manifest(gd, "", mine=await workspaces.personal(gd)), "mgmt")   # chưa ghim ⇒ không thấy nữa
        page = await q(admin, "apps.open", app_id=app)
        assert _blocks(page, "callout")[0]["title"] == "Dashboard đã gỡ"
        assert (await ok(gd, "apps.delete", app_id=app))["menu"] is True            # lệnh đổi menu ⇒ máy khách tải lại manifest ngay
        assert await owner_val("SELECT removed_at IS NOT NULL FROM apps.items WHERE id = %s", (app,)) is True   # xoá mềm: dấu còn cho người ghim
        assert all(str(a["id"]) != app for a in (await q(gd, "apps.list"))["rows"])   # chủ không còn thấy trong "App của tôi"
        assert (await executor.run(gd, "mgmt.publish", {"app_id": app})).status == "not_found"   # app đã xoá không đưa lại được
        page = await q(admin, "apps.open", app_id=app)
        assert _blocks(page, "callout")[0]["title"] == "Dashboard đã gỡ"
    finally:
        await ok(admin, "core.unpin", workspace="mgmt", key=key)


async def test_builder_check_reads_back_the_outline_without_row_values(who: Who) -> None:
    await helpers.order(who, owner="sale1", customer="Bà Vũ Kín Tiếng")
    await helpers.signed(who, owner="sale3", customer="Ông Đỗ Giấu Tên")
    gd = await who("giamdoc")
    app = await _app(gd, "Dashboard kiểm", BLOCKS)
    try:
        out = await q(gd, "apps.check", app_id=app)
        kinds = [b["loại"] for b in out["khối"]]
        assert kinds == ["kpis", "chart", "table"]
        table = out["khối"][2]
        assert table["cột"] and isinstance(table["số dòng"], int) and "rows" not in str(table)
        rows = (await q(gd, "orderdesk.list"))["rows"]
        assert {"Bà Vũ Kín Tiếng", "Ông Đỗ Giấu Tên"} <= {r["customer"] for r in rows}
        assert not any(str(r["customer"]) in str(out) for r in rows)             # không mang tên khách vào ngữ cảnh model
    finally:
        await ok(gd, "apps.delete", app_id=app)
