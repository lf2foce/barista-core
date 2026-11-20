"""0183: chuyển pháp nhân của phiếu chi phí đã ghi = ĐẢO ở pháp nhân cũ + GHI LẠI ở pháp nhân mới qua chuỗi duyệt sửa tiền — không UPDATE entity_id của tiền đã
ghi; phiếu mới trỏ phiếu cũ; làm lại không ghi lần hai; phiếu đã huỷ / trả từ tài khoản ngân hàng pháp nhân cũ / cùng pháp nhân bị từ chối."""

import uuid
from typing import Any

import pytest
import pytest_asyncio

from barista import seed
from barista.kernel import executor
from barista.kernel.principal import Principal
from tests.conftest import approved, ok, owner_val

DOMAIN = "chuyenpn.demo"


@pytest_asyncio.fixture(scope="module")
async def co(demo: dict[str, Any]) -> dict[str, Any]:
    tid = await seed.create_tenant("Công ty thử chuyển pháp nhân", DOMAIN,
                                   people=[("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True), ("ketoan", "Kế toán thử", "accountant", "KT", True),
                                           ("sale1", "Sale thử", "sale", "KD-A", False)])
    admin, kt = await seed.who(tid, "admin", DOMAIN), await seed.who(tid, "ketoan", DOMAIN)
    await ok(admin, "admin.upsert_entity", code="HAI", name="Pháp nhân hai")
    await ok(kt, "acct.set_category", code="van_phong", direction="out", label="Thuê văn phòng", account="expense:thue_van_phong")
    return {"tid": tid}


async def kt_of(co: dict[str, Any]) -> Principal:
    return await seed.who(co["tid"], "ketoan", DOMAIN)


async def _bill(kt: Principal, **kw: Any) -> str:
    inp = {"category": "van_phong", "amount_vnd": "11tr", "mode": "paid", "paid_from": "cash", "ref": "R" + uuid.uuid4().hex[:10].upper(), **kw}
    return str((await ok(kt, "acct.record_expense", **inp))["bill_id"])


async def _entity_net(tid: Any, bill_id: Any, entity_code: str) -> int:
    """Tổng chi phí còn lại trong sổ của một pháp nhân từ phiếu này (gốc + đảo + phiếu thay thế)."""
    return int(await owner_val("""SELECT coalesce(sum(p.amount_vnd), 0) FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id
                                  JOIN ledger.journals j ON j.id = p.journal_id JOIN core.legal_entities e ON e.id = j.entity_id
                                  WHERE a.tenant_id = %s AND a.code = 'expense:thue_van_phong' AND e.code = %s AND (j.idempotency_key LIKE %s OR j.idempotency_key LIKE %s)""",
                               (tid, entity_code, f"acct.bill%{bill_id}", f"acct.bill:{bill_id}")))


async def test_transfer_reverses_in_old_entity_and_reposts_in_new_through_approval(co: dict[str, Any]) -> None:
    kt = await kt_of(co)
    old = await _bill(kt)
    default = await owner_val("SELECT e.code FROM acct.bills b JOIN core.legal_entities e ON e.id = b.entity_id WHERE b.id = %s", (old,))
    r = await executor.run(kt, "acct.transfer_expense_entity", {"bill_id": old, "entity_code": "HAI", "reason": "Ghi nhầm pháp nhân"})
    assert r.status == "pending_approval"                                           # tiền đi ngược ⇒ người khác duyệt
    out = await approved(kt, "acct.transfer_expense_entity", bill_id=old, entity_code="HAI", reason="Ghi nhầm pháp nhân")
    assert out["created"] is True and str(out["from_bill_id"]) == old
    new = str(out["bill_id"])
    assert await _entity_net(co["tid"], old, default) == 0                           # pháp nhân cũ: gốc + đảo = 0
    assert await owner_val("SELECT coalesce(sum(p.amount_vnd), 0)::bigint FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id "
                           "JOIN ledger.journals j ON j.id = p.journal_id JOIN core.legal_entities e ON e.id = j.entity_id "
                           "WHERE a.code = 'expense:thue_van_phong' AND a.tenant_id = %s AND e.code = 'HAI' AND j.idempotency_key = %s", (co["tid"], f"acct.bill:{new}")) == 11_000_000
    o = await owner_val("SELECT row_to_json(b) FROM acct.bills b WHERE id = %s", (old,))
    n = await owner_val("SELECT row_to_json(b) FROM acct.bills b WHERE id = %s", (new,))
    assert o["cancelled_on"] and "Chuyển pháp nhân" in o["cancel_reason"] and "Ghi nhầm pháp nhân" in o["cancel_reason"]
    assert str(n["transferred_from_id"]) == old and n["total_vnd"] == o["total_vnd"] and n["cancelled_on"] is None
    assert await owner_val("SELECT e.code FROM acct.bills b JOIN core.legal_entities e ON e.id = b.entity_id WHERE b.id = %s", (new,)) == "HAI"
    # làm lại cùng phiếu ⇒ không ghi lần hai
    before = await owner_val("SELECT count(*) FROM acct.bills WHERE tenant_id = %s", (co["tid"],))
    again = await approved(kt, "acct.transfer_expense_entity", bill_id=old, entity_code="HAI", reason="Ghi nhầm pháp nhân")
    assert again["created"] is False and str(again["bill_id"]) == new
    assert await owner_val("SELECT count(*) FROM acct.bills WHERE tenant_id = %s", (co["tid"],)) == before


async def test_a_payable_bill_moves_its_debt_to_the_new_entity(co: dict[str, Any]) -> None:
    kt = await kt_of(co)
    old = await _bill(kt, mode="payable", supplier="Bên bán chuyển pháp nhân", due_on=None)
    out = await approved(kt, "acct.transfer_expense_entity", bill_id=old, entity_code="HAI", reason="Hợp đồng đứng tên pháp nhân hai")
    new = str(out["bill_id"])
    ent = await owner_val("SELECT array_agg(e.code ORDER BY p.created_at) FROM acct.payables p JOIN core.legal_entities e ON e.id = p.entity_id "
                          "WHERE p.id IN (SELECT payable_id FROM acct.bills WHERE id IN (%s, %s))", (old, new))
    assert "HAI" in ent and len(ent) == 2                                              # khoản phải trả cũ (đã điều chỉnh về 0) + khoản mới ở pháp nhân hai
    assert await _entity_net(co["tid"], old, "HAI") == 0                              # phiếu cũ không dính pháp nhân hai
    assert await owner_val("SELECT cancelled_on IS NOT NULL FROM acct.bills WHERE id = %s", (old,)) is True


async def test_refusals(co: dict[str, Any]) -> None:
    kt, sale = await kt_of(co), await seed.who(co["tid"], "sale1", DOMAIN)
    b = await _bill(kt)
    default = await owner_val("SELECT e.code FROM acct.bills b JOIN core.legal_entities e ON e.id = b.entity_id WHERE b.id = %s", (b,))
    for inp, msg in (({"bill_id": b, "entity_code": default, "reason": "x"}, "đã ở pháp nhân này"), ({"bill_id": b, "entity_code": "KHONGCO", "reason": "x"}, "KHONGCO"),
                     ({"bill_id": b, "entity_code": "HAI", "reason": "  "}, "lý do"), ({"bill_id": str(uuid.uuid4()), "entity_code": "HAI", "reason": "x"}, "Không thấy phiếu")):
        with pytest.raises(AssertionError, match=msg):                                  # chạy sau khi duyệt ⇒ lệnh từ chối, không ghi gì
            await approved(kt, "acct.transfer_expense_entity", **inp)
    assert await owner_val("SELECT count(*) FROM acct.bills WHERE transferred_from_id = %s", (b,)) == 0
    assert (await executor.run(sale, "acct.transfer_expense_entity", {"bill_id": b, "entity_code": "HAI", "reason": "x"})).status == "denied"
    agent = await executor.run(kt.as_agent(), "acct.transfer_expense_entity", {"bill_id": b, "entity_code": "HAI", "reason": "x"})
    assert agent.status != "ok"                                                         # trợ lý không tự chuyển (như huỷ phiếu: cần người duyệt)
    assert await owner_val("SELECT count(*) FROM acct.bills WHERE transferred_from_id = %s", (b,)) == 0
    await approved(kt, "acct.cancel_expense", bill_id=b, reason="Ghi trùng")
    with pytest.raises(AssertionError, match="đã huỷ"):                                 # phiếu đã huỷ không chuyển được
        await approved(kt, "acct.transfer_expense_entity", bill_id=b, entity_code="HAI", reason="x")
