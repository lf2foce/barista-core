"""THANH BÊN THEO VAI (ADR 0255 lát 1) qua gói thế vai: gói KHAI phân hệ hiện sẵn theo vai (``Pack(rail=)``), công ty đổi (``core.role_rail``), người dùng ghim
cả phân hệ (``core.pin`` với khoá màn trống). Lõi không biết vai / phân hệ nào — chỉ đọc khai báo; giấu menu không thay quyền; một phân hệ chỉ hiện một lần."""

from typing import Any

import pytest

from barista import workspaces
from barista.kernel import executor, registry
from tests.conftest import Who, ok, q
from tests.standin import pack


async def _menu(p: Any) -> dict[str, Any]:
    """Manifest như cửa HTTP trả (có phần cá nhân: ghim + phần công ty đã đặt cho vai)."""
    return workspaces.manifest(p, "", mine=await workspaces.personal(p))


def _keys(m: dict[str, Any]) -> list[str]:
    return [w["key"] for w in m["workspaces"]]


async def test_pack_declaration_becomes_the_role_defaults_and_unknown_keys_are_dropped(who: Who) -> None:
    giamdoc, sale = await _menu(await who("giamdoc")), await _menu(await who("sale1"))
    assert giamdoc["rail"] == {"role": ["accounting", "sales"], "pinned": []}   # "ghost_ws" không phải phân hệ nào ⇒ bỏ lặng lẽ
    assert sale["rail"] == {"role": ["sales", "kpi"], "pinned": []}
    # thanh bên chỉ là CÁCH SẮP: danh sách phân hệ người này được dùng không đổi, các phân hệ còn lại vẫn mở được (qua "Tất cả mục")
    assert {"accounting", "sales"} < set(_keys(giamdoc)) and len(_keys(giamdoc)) > 2
    assert "accounting" not in _keys(sale)


async def test_role_without_a_declaration_keeps_the_full_menu(who: Who) -> None:
    admin = await _menu(await who("admin"))
    assert "rail" not in admin and len(_keys(admin)) > 3   # vai không khai ⇒ như trước: app vẽ hết


async def test_defaults_that_are_all_invisible_keep_the_full_menu(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mặc định chỉ trỏ phân hệ người đó không thấy (gói tắt / không quyền) ⇒ không có nhóm — thanh bên không bao giờ trống."""
    monkeypatch.setitem(registry.PACKS[pack.PACK].rail, "sale", ("accounting", "ghost_ws"))
    assert "rail" not in await _menu(await who("sale1"))


async def test_pinning_a_workspace_shows_it_once_and_losing_it_hides_it(who: Who) -> None:
    sale = await who("sale1")
    seen = _keys(await _menu(sale))
    extra = next(k for k in seen if k not in ("sales", "kpi"))
    try:
        await ok(sale, "core.pin", workspace=extra)   # khoá màn trống = ghim cả phân hệ
        m = await _menu(sale)
        assert m["rail"] == {"role": ["sales", "kpi"], "pinned": [extra]}
        assert [w["key"] for w in m["workspaces"] if w.get("pinned")] == [extra]
        assert all(x["key"] for x in m["pins"])   # ghim phân hệ không lọt vào ghim MÀN (khoá trống không phải màn)

        await ok(sale, "core.pin", workspace="sales")   # đang là mặc định theo vai: ghi nhận, nhưng hiện MỘT lần
        m = await _menu(sale)
        assert m["rail"]["role"] == ["sales", "kpi"] and m["rail"]["pinned"] == [extra]
        assert {w["key"] for w in m["workspaces"] if w.get("pinned")} == {extra, "sales"}
        await ok(sale, "core.unpin", workspace="sales")
        assert "pinned" not in next(w for w in (await _menu(sale))["workspaces"] if w["key"] == "sales")   # bỏ ghim; vẫn ở "Theo vai"
        assert (await _menu(sale))["rail"]["role"] == ["sales", "kpi"]

        # ghim phân hệ người này KHÔNG được dùng (hoặc chưa được dùng nữa) ⇒ ghi nhận nhưng không hiện — giấu menu không thay quyền
        await ok(sale, "core.pin", workspace="accounting")
        m = await _menu(sale)
        assert "accounting" not in _keys(m) and m["rail"]["pinned"] == [extra]
    finally:
        for w in (extra, "sales", "accounting"):
            await ok(sale, "core.unpin", workspace=w)
    assert (await _menu(sale))["rail"]["pinned"] == []


async def test_pinning_is_personal_and_screen_pins_are_unchanged(who: Who) -> None:
    sale1, sale2 = await who("sale1"), await who("sale2")
    extra = next(k for k in _keys(await _menu(sale1)) if k not in ("sales", "kpi"))
    ws = (await _menu(sale1))["workspaces"][-1]
    key = ws["screens"][0]["key"]
    try:
        await ok(sale1, "core.pin", workspace=extra)
        await ok(sale1, "core.pin", workspace=ws["key"], key=key)   # ghim MÀN như cũ
        assert (await _menu(sale2))["rail"]["pinned"] == []
        m1 = await _menu(sale1)
        assert {"workspace": ws["key"], "key": key, "mine": True} in m1["pins"] and m1["rail"]["pinned"] == [extra]
        rows = await q(sale1, "core.pins")
        assert {"workspace": extra, "key": ""} in rows and {"workspace": ws["key"], "key": key} in rows
    finally:
        await ok(sale1, "core.unpin", workspace=extra)
        await ok(sale1, "core.unpin", workspace=ws["key"], key=key)


async def test_pin_rejects_unknown_workspace_and_bad_address(who: Who) -> None:
    sale = await who("sale1")
    assert (await executor.run(sale, "core.pin", {"workspace": "khong_co_phan_he_nay"})).status == "not_found"   # ghim phân hệ ⇒ phải là phân hệ có thật
    assert (await executor.run(sale, "core.pin", {"workspace": "Sai Chu Hoa"})).status == "invalid"
    assert (await executor.run(sale, "core.pin", {"workspace": "sales", "key": "SAI MAN"})).status == "invalid"
    assert (await executor.query(sale, "core.pins", {})).output == []   # không để lại dòng rác


async def test_company_changes_the_defaults_and_a_pack_upgrade_does_not_overwrite_them(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    admin, sale = await who("admin"), await who("sale1")
    sale_other = await who("sale1", other=True)
    assert (await _menu(sale))["rail"]["role"] == ["sales", "kpi"]
    try:
        r = await ok(admin, "core.set_role_rail", role="sale", workspaces=["docs", "sales"])
        assert r["workspaces"] == ["docs", "sales"]
        assert (await _menu(sale))["rail"]["role"] == ["docs", "sales"]   # công ty đặt thắng khai báo của gói, theo đúng thứ tự
        assert ((await _menu(sale_other)).get("rail") or {}).get("role") != ["docs", "sales"]   # dòng của công ty này không lọt sang công ty khác (RLS)

        # gói nâng cấp đổi khai báo ⇒ lựa chọn ĐÃ LƯU của công ty vẫn giữ (mặc định đọc từ mã, không cài đè vào dữ liệu)
        monkeypatch.setitem(registry.PACKS[pack.PACK].rail, "sale", ("kpi", "sales", "schedule"))
        assert (await _menu(sale))["rail"]["role"] == ["docs", "sales"]
        rows = {x["role"]: x for x in (await q(admin, "core.role_rail"))["rows"]}   # màn Quản trị: đang theo công ty đặt, kèm mặc định MỚI của gói để so
        assert rows["sale"]["state"] == "set" and rows["sale"]["base"] == "KPI & xếp hạng, Kinh doanh, Lịch làm việc"
        assert rows["sale"]["workspaces_in"] == ["docs", "sales"]
    finally:
        await ok(admin, "core.clear_role_rail", role="sale")
    assert (await _menu(sale))["rail"]["role"] == ["kpi", "sales", "schedule"]   # bỏ phần công ty đặt ⇒ theo mặc định (mới) của gói


async def test_company_rail_is_validated_and_only_admins_change_it(who: Who) -> None:
    admin, sale, giamdoc = await who("admin"), await who("sale1"), await who("giamdoc")
    assert (await executor.run(sale, "core.set_role_rail", {"role": "sale", "workspaces": ["sales"]})).status == "denied"
    assert (await executor.run(admin.as_agent(), "core.set_role_rail", {"role": "sale", "workspaces": ["sales"]})).status == "denied"   # trợ lý không tự sửa
    for bad in (["khong_co"], [], ["sales", "kpi", "schedule", "work", "docs", "apps", "hr"]):   # phân hệ lạ / rỗng / vượt trần
        assert (await executor.run(admin, "core.set_role_rail", {"role": "sale", "workspaces": bad})).status == "invalid", bad
    assert (await executor.run(admin, "core.set_role_rail", {"role": "khong_co_vai", "workspaces": ["sales"]})).status == "not_found"
    assert (await executor.run(admin, "core.set_role_rail", {"role": "sale", "workspaces": ["account"]})).status == "invalid"   # phân hệ cá nhân không lên thanh bên
    # đặt trùng khoá ⇒ gộp; giám đốc không có quyền cấu hình (chỉ quản trị / công nghệ) nên không đổi được thanh bên của vai
    assert (await executor.run(giamdoc, "core.set_role_rail", {"role": "sale", "workspaces": ["sales"]})).status == "denied"
    r = await ok(admin, "core.set_role_rail", role="sale", workspaces=["sales", "sales", "kpi"])
    try:
        assert r["workspaces"] == ["sales", "kpi"]
    finally:
        await ok(admin, "core.clear_role_rail", role="sale")


async def test_a_role_that_lost_every_default_falls_back_to_the_full_menu(who: Who) -> None:
    """Công ty đặt phân hệ mà vai không còn quyền dùng ⇒ lọc hết ⇒ không để thanh bên trống, vẽ hết (quyền vẫn do executor / RLS)."""
    admin, sale = await who("admin"), await who("sale1")
    await ok(admin, "core.set_role_rail", role="sale", workspaces=["accounting"])   # sale không có phân hệ Kế toán
    try:
        m = await _menu(sale)
        assert "rail" not in m and "accounting" not in _keys(m)
    finally:
        await ok(admin, "core.clear_role_rail", role="sale")
