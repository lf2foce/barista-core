"""MA TRẬN QUYỀN trên bảng của một gói ngành — bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_permissions.py``: bảng gói khai qua
``core.secure_table`` được lõi cô lập theo công ty và lọc theo phạm vi quyền (của mình / đội / cả công ty) ở DB (RLS), lệnh ghi cho người khác bị chặn
và hoàn trọn giao dịch, vai chỉ đọc không chạy được lệnh ghi, báo cáo toàn công ty cần phạm vi cả công ty. Mỗi khẳng định chạy qua executor như request thật."""

import uuid

from barista import seed
from barista.kernel import executor
from tests.conftest import Who, fresh_phone, ok, owner_val, q
from tests.standin import helpers, pack
from tests.standin.conftest import DOMAIN
from tests.test_packs_industry import PEOPLE


async def test_tenant_isolation(who: Who) -> None:
    """Công ty khác CŨNG bật gói: không thấy dòng nào của công ty thế vai (đơn, lead), không mở được đơn theo mã, và mã đơn trùng không đụng nhau."""
    o = await helpers.order(who)
    await ok(await who("sale1"), "crm.create_lead", name="Khách cô lập", phone=fresh_phone())
    domain = f"p{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty thử {domain}", domain, people=PEOPLE, packs=(pack.PACK, "crm"))
    outsider = await seed.who(tid, "admin", domain)
    assert (await q(outsider, "orderdesk.list"))["rows"] == []
    assert (await q(outsider, "crm.leads", stage=""))["rows"] == []
    assert (await executor.query(outsider, "orderdesk.case_file", {"code": o["code"]})).status == "not_found"
    assert (await executor.run(outsider, "orderdesk.sign", {"code": o["code"]})).status == "not_found"
    same = await ok(outsider, "orderdesk.create", title="Đơn trùng mã", customer="Khách công ty khác", amount_vnd=1_000_000_000, code=o["code"])
    assert same["code"] == o["code"] and [r["customer"] for r in (await q(outsider, "orderdesk.list"))["rows"]] == ["Khách công ty khác"]
    assert await owner_val("SELECT status FROM orderdesk.orders WHERE id = %s", (o["order_id"],)) == "open"
    assert (await q(await who("admin", other=True), "crm.leads", stage=""))["rows"] == []        # công ty thứ hai của bộ test: không lead của ai khác


async def test_sale_cannot_enter_an_order_for_someone_else(who: Who) -> None:
    sale1 = await who("sale1")
    code = f"DH-Q{uuid.uuid4().hex[:6].upper()}"
    r = await executor.run(sale1, "orderdesk.create", {"title": "Tư vấn", "customer": "Khách thử", "amount_vnd": 2_000_000_000, "code": code,
                                                       "owner_email": f"sale2@{DOMAIN}"})
    assert r.status == "denied"
    # cả giao dịch rollback: không dòng đơn / đợt thanh toán nào còn lại, mã chưa bị dùng
    assert await owner_val("SELECT count(*) FROM orderdesk.orders WHERE code = %s AND tenant_id = %s", (code, sale1.tenant_id)) == 0
    out = await ok(sale1, "orderdesk.create", title="Tư vấn", customer="Khách thử", amount_vnd=2_000_000_000, code=code)
    assert out["code"] == code
    assert await owner_val("SELECT owner_id FROM orderdesk.orders WHERE id = %s", (out["order_id"],)) == sale1.user_id


async def test_read_only_roles_and_people_outside_the_scope_cannot_sign_or_cancel(who: Who) -> None:
    """Giám đốc / kế toán thấy mọi đơn nhưng chỉ ĐỌC ⇒ lệnh ghi bị từ chối; nhân viên / trưởng nhóm đội khác không thấy đơn ⇒ như không có."""
    o = await helpers.order(who, owner="sale3")                                              # đơn của sale3 — Team B
    for local in ("giamdoc", "ketoan"):
        p = await who(local)
        assert o["code"] in {r["code"] for r in (await q(p, "orderdesk.list"))["rows"]}, local
        for cmd in ("orderdesk.sign", "orderdesk.cancel"):
            r = await executor.run(p, cmd, {"code": o["code"], "reason": "x"} if cmd == "orderdesk.cancel" else {"code": o["code"]})
            assert r.status == "denied", (local, cmd)
    for local in ("sale1", "truonga"):
        r = await executor.run(await who(local), "orderdesk.sign", {"code": o["code"]})
        assert r.status == "not_found", local
    assert await owner_val("SELECT status FROM orderdesk.orders WHERE id = %s", (o["order_id"],)) == "open"
    await ok(await who("truongb"), "orderdesk.sign", code=o["code"])                         # trưởng nhóm của đúng đội: phạm vi đội, được ghi


async def test_orders_are_private_to_their_scope(who: Who) -> None:
    sale1, sale3, truonga, ketoan = await who("sale1"), await who("sale3"), await who("truonga"), await who("ketoan")
    await helpers.order(who, owner="sale1")
    await helpers.order(who, owner="sale2")
    await helpers.order(who, owner="sale3")
    assert {r["owner"] for r in (await q(sale1, "orderdesk.list"))["rows"]} == {sale1.name}
    assert {r["owner"] for r in (await q(sale3, "orderdesk.list"))["rows"]} == {sale3.name}
    team_a = {r["owner"] for r in (await q(truonga, "orderdesk.list"))["rows"]}
    assert {sale1.name, (await who("sale2")).name} <= team_a and sale3.name not in team_a   # cả đội A, không thấy đội B
    everyone = (await q(ketoan, "orderdesk.list"))["rows"]
    assert {sale1.name, sale3.name} <= {r["owner"] for r in everyone}
    assert len(everyone) == await owner_val("SELECT count(*) FROM orderdesk.orders WHERE tenant_id = %s", (ketoan.tenant_id,))
    mine = next(k for k in (await q(sale1, "orderdesk.list"))["kpis"] if k["label"] == "Đơn")["value"]
    assert mine == await owner_val("SELECT count(*) FROM orderdesk.orders WHERE owner_id = %s AND tenant_id = %s", (sale1.user_id, sale1.tenant_id))


async def test_company_reports_need_company_scope(who: Who) -> None:
    """Sale có quyền xem đơn CỦA MÌNH nhưng không chạy được báo cáo toàn công ty (sẽ ra số thiếu mà tưởng đủ)."""
    sale1 = await who("sale1")
    assert sale1.can("orderdesk", "read")
    for name in ("orderdesk.report_year", "acct.cashflow", "acct.misa_export", "acct.balances"):
        r = await executor.query(sale1, name, {})
        assert r.status == "denied", name
    assert (await executor.query(await who("truonga"), "orderdesk.report_year", {})).status == "denied"   # phạm vi đội cũng chưa đủ
    assert (await executor.query(await who("giamdoc"), "orderdesk.report_year", {})).status == "ok"
