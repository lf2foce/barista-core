"""CHỐT THÁNG của Báo cáo quản trị (0213): chụp đầu ra từng trang chính thức bằng quyền người chốt; bản chụp không đổi khi dữ liệu sau đó đổi; chốt lại
= lần mới, bản cũ còn; chỉ người phạm vi cả công ty chốt / xem; trợ lý chỉ đề xuất; không chốt tháng chưa tới. Dữ liệu giả, Postgres thật."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio

from barista.kernel import executor
from barista.kernel.clock import today
from tests.conftest import Who, ok, q


@pytest_asyncio.fixture
async def mgmt_on(who: Who) -> AsyncIterator[None]:
    admin = await who("admin")
    r = await executor.run(admin, "admin.set_pack", {"pack": "mgmt", "enabled": True})
    assert r.status == "ok", (r.status, r.error)
    try:
        yield
    finally:
        await executor.run(admin, "admin.set_pack", {"pack": "mgmt", "enabled": False})


async def test_close_month_snapshots_the_menu_pages_and_keeps_them_when_data_changes(who: Who, mgmt_on: None) -> None:
    gd, mkt = await who("giamdoc"), await who("mkt")
    month = (today().replace(day=1) - timedelta(days=1)).replace(day=1)
    first = await ok(gd, "mgmt.close_month", period=f"{month:%Y-%m}", note="số họp giao ban")
    assert first["pages"] >= 3 and first["version"] >= 1                         # dòng tiền, KPI, quảng cáo… của lõi
    rows = [r for r in (await q(gd, "mgmt.closings"))["rows"] if r["month"] == f"{month:%m/%Y}" and r["version"] == first["version"]]
    assert len(rows) == first["pages"] and all(r["latest"] == "yes" and r["closed_by"] for r in rows)
    ads = next(r for r in rows if r["title"] == "Hiệu quả quảng cáo")
    before = await q(gd, "mgmt.snapshot", snapshot_id=ads["id"])
    assert before["note"].startswith(f"Bản chốt {month:%m/%Y} · lần {first['version']}") and "(bản chốt" in before.get("title", "(bản chốt")
    assert "drill" not in str(before)                                             # bấm ra dòng gốc sẽ là số sống ⇒ bỏ trong bản chụp

    code = f"T-CHOT-{first['version']}-{today():%H%M%S}"                          # dữ liệu đổi SAU khi chốt
    await ok(mkt, "mkt.upsert_campaign", code=code, name="Chiến dịch sau chốt", channel="facebook", offering_code="HSR", budget_vnd="5tr",
             start_on=(today() - timedelta(days=3)).isoformat())
    await ok(mkt, "mkt.record_spend", campaign_code=code, day=(today() - timedelta(days=1)).isoformat(), amount_vnd="9tr")
    assert await q(gd, "mgmt.snapshot", snapshot_id=ads["id"]) == before          # bản chốt không đổi

    again = await ok(gd, "mgmt.close_month", period=f"{month:%Y-%m}", note="chốt lại sau khi nhập bù")
    assert again["version"] == first["version"] + 1
    by_version = {(r["version"], r["latest"]) for r in (await q(gd, "mgmt.closings"))["rows"] if r["month"] == f"{month:%m/%Y}"}
    assert (first["version"], "no") in by_version and (again["version"], "yes") in by_version   # bản cũ còn, đánh dấu đã thay


async def test_only_company_wide_managers_close_and_the_assistant_cannot(who: Who, mgmt_on: None) -> None:
    for local in ("sale1", "mkt", "truonga"):
        p = await who(local)
        assert (await executor.run(p, "mgmt.close_month", {})).status == "denied", local
        assert (await executor.query(p, "mgmt.closings", {})).status == "denied", local
    gd = await who("giamdoc")
    r = await executor.run(gd.as_agent(), "mgmt.close_month", {})
    assert r.status == "denied", (r.status, r.error)                            # 0213 hoãn: trợ lý không đề xuất chốt tháng
    nxt = (today().replace(day=1) + timedelta(days=32)).replace(day=1)
    bad = await executor.run(gd, "mgmt.close_month", {"period": f"{nxt:%Y-%m}"})
    assert bad.status == "invalid" and "chưa tới" in (bad.error or "")


async def test_a_dashboard_shared_into_the_menu_is_part_of_the_close(who: Who, mgmt_on: None) -> None:
    """Dashboard tự tạo đã đưa vào menu là số chính thức ⇒ có trong bản chốt; app riêng chưa chia thì không."""
    gd = await who("giamdoc")
    shared = await ok(gd, "apps.create", title="Lead theo nguồn (chia)")
    await ok(gd, "apps.add_block", app_id=shared["app_id"], page="main",
             block={"kind": "kpi", "title": "Lead", "source": {"dataset": "crm.leads", "measures": ["count"]}})
    await ok(gd, "mgmt.publish", app_id=shared["app_id"])
    private = await ok(gd, "apps.create", title="Nháp riêng (chưa chia)")
    month = (today().replace(day=1) - timedelta(days=1)).replace(day=1)
    r = await ok(gd, "mgmt.close_month", period=f"{month:%Y-%m}")
    titles = {x["title"] for x in (await q(gd, "mgmt.closings"))["rows"] if x["month"] == f"{month:%m/%Y}" and x["version"] == r["version"]}
    assert "Lead theo nguồn (chia)" in titles and "Nháp riêng (chưa chia)" not in titles
    await ok(gd, "mgmt.publish", app_id=shared["app_id"], publish=False)
    assert private["app_id"]


async def test_the_close_records_how_the_numbers_were_taken(who: Who, mgmt_on: None) -> None:
    """Mỗi trang trong bản chốt ghi phiên bản lõi + gói, truy vấn + đầu vào, mốc lấy số (Codex 14/11 điểm 1)."""
    from barista import api
    from tests.conftest import owner_val

    gd = await who("giamdoc")
    month = (today().replace(day=1) - timedelta(days=1)).replace(day=1)
    r = await ok(gd, "mgmt.close_month", period=f"{month:%Y-%m}")
    src = await owner_val("""SELECT jsonb_agg(source) FROM mgmt.snapshots WHERE tenant_id = %s AND period = %s AND version = %s""",
                          (gd.tenant_id, month, r["version"]))
    assert len(src) == r["pages"] and all(s["api"] == api.__version__ and s["query"] and "input" in s and s["as_of"] for s in src)
    assert len({s["as_of"] for s in src}) == 1 and "crm" in src[0]["packs"]           # một mốc cho cả lượt chốt
    page = await q(gd, "mgmt.snapshot", snapshot_id=next(x["id"] for x in (await q(gd, "mgmt.closings"))["rows"] if x["version"] == r["version"]))
    assert f"(lõi {api.__version__})" in page["note"]


async def test_a_closer_who_cannot_see_a_page_cannot_close(who: Who, mgmt_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Thiếu quyền xem một trang ⇒ không chốt, nêu tên trang, không ghi gì (bản chốt không được thiếu trang mà không ai biết)."""
    from barista.api import Screen
    from barista.modules import mgmt
    from tests.conftest import owner_val

    real = mgmt.menu_pages
    monkeypatch.setattr(mgmt, "menu_pages", lambda ws: (*real(ws), Screen("t_forbidden", "Trang giám đốc không xem được", "lock", "admin.chains")))
    gd = await who("giamdoc")
    before = await owner_val("SELECT count(*) FROM mgmt.snapshots WHERE tenant_id = %s", (gd.tenant_id,))
    r = await executor.run(gd, "mgmt.close_month", {})
    assert r.status == "denied" and "Trang giám đốc không xem được" in (r.error or ""), (r.status, r.error)
    assert await owner_val("SELECT count(*) FROM mgmt.snapshots WHERE tenant_id = %s", (gd.tenant_id,)) == before   # không ghi trang nào


async def test_a_consistent_command_reads_one_snapshot_even_when_others_commit_meanwhile(who: Who) -> None:
    """``consistent=True`` (1.35): hai lần đọc trong CÙNG giao dịch thấy cùng số dù giao dịch khác commit chen giữa; không cờ thì thấy số mới.
    Chốt tháng khai cờ này ⇒ mọi trang của một lượt chốt cùng một mốc dữ liệu (Codex 14/11 điểm 3)."""
    from barista.db import db
    from barista.kernel import registry

    assert registry.OPS["mgmt.close_month"].consistent
    gd, mkt = await who("giamdoc"), await who("mkt")
    count = "SELECT count(*) FROM mkt.campaigns"
    for consistent, sees_new in ((True, False), (False, True)):
        async with db.tenant_tx(gd, consistent=consistent) as conn:
            before = (await (await conn.execute(count)).fetchone() or {"count": 0})["count"]
            await ok(mkt, "mkt.upsert_campaign", code=f"T-RR-{int(consistent)}-{today():%H%M%S%f}"[:40], name="Chen giữa", channel="facebook",
                     offering_code="HSR", budget_vnd="1tr", start_on=today().isoformat())   # giao dịch KHÁC, commit ngay
            after = (await (await conn.execute(count)).fetchone() or {"count": 0})["count"]
        assert (after > before) is sees_new, (consistent, before, after)


async def test_a_consistent_command_refuses_to_run_nested_in_an_ordinary_transaction(who: Who, mgmt_on: None) -> None:
    """Bất biến 1.35: gọi lồng (kể cả phiếu duyệt chạy lại lệnh) không đổi được mức cách ly ⇒ lệnh ``consistent`` từ chối rõ, không chạy với số có thể
    lệch giữa các trang. Trong giao dịch REPEATABLE READ thì chạy được."""
    import pytest as _pt

    from barista.db import db
    from barista.kernel.errors import Conflict
    from barista.kernel.executor import core_ctx

    gd = await who("giamdoc")
    async with db.tenant_tx(gd, rollback=True) as conn:
        with _pt.raises(Conflict, match="đọc nhất quán"):
            await executor.invoke(core_ctx(conn, gd), "mgmt.close_month", {})
    async with db.tenant_tx(gd, rollback=True, consistent=True) as conn:
        assert (await executor.invoke(core_ctx(conn, gd), "mgmt.close_month", {}))["pages"] >= 1


async def test_month_close_is_not_on_the_menu_while_deferred(who: Who, mgmt_on: None) -> None:
    """0213 hoãn (anh Anh 14/11): lệnh + bảng còn, nhưng menu Báo cáo quản trị không có màn / nút chốt tháng."""
    from barista.workspaces import manifest

    gd = await who("giamdoc")
    ws = next((w for w in manifest(gd, "x")["workspaces"] if w["key"] == "mgmt"), {"screens": []})
    assert not [s for s in ws["screens"] if s["key"] == "mgmt_closings" or "mgmt.close_month" in str(s.get("actions", ""))]
