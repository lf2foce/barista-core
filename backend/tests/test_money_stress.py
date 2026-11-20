"""TIỀN DƯỚI TÌNH HUỐNG XẤU (ROADMAP A8): ghi đồng thời, sự kiện giao lại, sự kiện đến sai thứ tự. Sau mỗi kịch bản chạy cùng một bộ kiểm
toàn vẹn (``_money_ok``) — test bình thường chỉ chứng minh đường đẹp; đây là bằng chứng tiền không lệch khi hệ thống gặp sự cố thật."""

import asyncio
from typing import Any

import pytest

from barista.db import db
from barista.kernel import events, executor
from barista.modules import acct
from tests import money_source
from tests.conftest import Who, ok, owner_val


@pytest.fixture(autouse=True)
def _source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Khoản phải thu của nguồn tiền giả của lõi (``tests/money_source.py``); kịch bản dùng phí thật của gói (sự kiện giao lại, sai thứ tự, hoa hồng
    về ví đúng một lần) nằm ở test của gói."""
    money_source.use(monkeypatch)


async def _money_ok() -> None:
    """Bất biến tiền của toàn bộ dữ liệu test (mọi công ty): sổ cái tổng 0; khoản phải thu = tổng phiếu thu + trạng thái khớp số; dòng sao kê
    phân bổ = tổng phiếu thu từ dòng, không vượt số tiền."""
    assert await owner_val("SELECT coalesce(sum(amount_vnd), 0) FROM ledger.postings") == 0
    assert await owner_val("SELECT count(*) FROM acct.receivables r WHERE " + _OLD_AR) == 0, "khoản phải thu lệch với phiếu thu"
    assert await owner_val("SELECT count(*) FROM acct.statement_lines l WHERE " + _OLD_LINE) == 0, "dòng sao kê phân bổ lệch"


async def test_two_clerks_recording_the_full_amount_at_once_collect_it_once(who: Who) -> None:
    """Hai kế toán cùng ghi đủ tiền vào một khoản (hai mã giao dịch khác nhau, cùng lúc): đúng một phiếu thành, phiếu kia bị từ chối — không thu
    vượt, không mất số (khoá dòng khoản phải thu)."""
    f = await money_source.revenue(who)
    inv, fee = f["code"], f["fee"]
    ketoan = await who("ketoan")
    rs = await asyncio.gather(*(executor.run(ketoan, "acct.record_payment", {"code": inv, "amount_vnd": fee, "bank_ref": f"{inv}-{i}"})
                                for i in range(2)))
    assert sorted(r.status for r in rs) == ["invalid", "ok"], [r.error for r in rs]
    assert await owner_val("SELECT paid_vnd FROM acct.receivables WHERE code = %s", (inv,)) == fee
    await events.drain()
    await _money_ok()


async def test_one_bank_line_applied_to_two_receivables_at_once_never_exceeds_the_line(who: Who) -> None:
    """Một dòng sao kê, hai người cùng ghi vào hai khoản khác nhau cùng lúc: tổng phân bổ không vượt số tiền của dòng."""
    f1, f2 = await money_source.revenue(who), await money_source.revenue(who)
    ketoan = await who("ketoan")
    line = (await ok(ketoan, "acct.add_statement_line", received_on="2026-11-05", amount_vnd=str(f1["fee"]), memo="CK phi", payer="KHACH THU",
                     bank_ref=f"LINE-{f1['code']}", entity_code=f1["entity"]))["line_id"]
    rs = await asyncio.gather(*(executor.run(ketoan, "acct.apply_line", {"line_id": str(line), "code": c}) for c in (f1["code"], f2["code"])))
    assert [r.status for r in rs].count("ok") >= 1
    assert await owner_val("SELECT allocated_vnd FROM acct.statement_lines WHERE id = %s", (line,)) <= f1["fee"]
    await events.drain()
    await _money_ok()


async def test_a_receipt_delivered_twice_is_recorded_once(who: Who) -> None:
    """Cùng một giao dịch ngân hàng (cùng mã) ghi lại lần hai — nhập trùng file, bấm hai lần — không thành phiếu thu thứ hai."""
    f = await money_source.revenue(who)
    ketoan = await who("ketoan")
    half = f["fee"] // 2
    for _ in range(2):
        await executor.run(ketoan, "acct.record_payment", {"code": f["code"], "amount_vnd": half, "bank_ref": f"DUP-{f['code']}"})
    assert await owner_val("SELECT paid_vnd FROM acct.receivables WHERE code = %s", (f["code"],)) == half
    await events.drain()
    await _money_ok()

# ── Kiểm bất biến THEO TẬP (0185) cho đúng kết quả như bản từng-dòng cũ — trên mọi dữ liệu test đang có + vài dòng cố ý làm lệch ──

_OLD_AR = """(r.paid_vnd <> (SELECT coalesce(sum(p.amount_vnd), 0) FROM acct.payments p WHERE p.receivable_id = r.id)
                 - (SELECT coalesce(sum(x.amount_vnd), 0) FROM acct.payment_reversals x JOIN acct.payments p ON p.id = x.payment_id WHERE p.receivable_id = r.id)
              OR r.credited_vnd <> (SELECT coalesce(sum(c.amount_vnd), 0) FROM acct.receivable_credits c WHERE c.receivable_id = r.id)
              OR r.paid_vnd > r.amount_vnd OR (r.status = 'paid' AND r.paid_vnd <> r.amount_vnd)
              OR (r.status = 'partial' AND NOT r.paid_vnd BETWEEN 1 AND r.amount_vnd - 1) OR (r.status = 'open' AND r.paid_vnd <> 0))"""
_OLD_LINE = """(l.allocated_vnd > l.amount_vnd + l.deducted_vnd
                OR l.deducted_vnd <> (SELECT coalesce(sum(d.amount_vnd), 0) FROM acct.line_deductions d WHERE d.line_id = l.id)
                OR l.allocated_vnd <> (SELECT coalesce(sum(p.amount_vnd), 0) FROM acct.payments p WHERE p.line_id = l.id)
                  - (SELECT coalesce(sum(x.amount_vnd), 0) FROM acct.payment_reversals x JOIN acct.payments p ON p.id = x.payment_id WHERE p.line_id = l.id)
                  + (SELECT coalesce(sum(r.amount_vnd), 0) FROM acct.recovery_receipts r WHERE r.line_id = l.id)
                  + (SELECT coalesce(sum(t.amount_vnd), 0) FROM acct.placement_receipts t WHERE t.line_id = l.id))"""


async def test_set_based_drift_check_matches_the_per_row_definition() -> None:
    async with db.owner.connection() as conn, conn.transaction(force_rollback=True):
        await conn.execute("UPDATE acct.receivables SET credited_vnd = credited_vnd + 1 WHERE id IN (SELECT id FROM acct.receivables ORDER BY id LIMIT 2)")
        await conn.execute("""UPDATE acct.receivables SET paid_vnd = paid_vnd + 1 WHERE id IN (
                                  SELECT id FROM acct.receivables WHERE paid_vnd + 1 < amount_vnd ORDER BY id DESC LIMIT 2)""")
        await conn.execute("""UPDATE acct.statement_lines SET allocated_vnd = allocated_vnd + 1 WHERE id IN (
                                  SELECT id FROM acct.statement_lines WHERE status = 'open' AND allocated_vnd + 1 < amount_vnd ORDER BY id LIMIT 2)""")

        async def ids(sql: str) -> list[Any]:
            return sorted(str(r["id"]) for r in await (await conn.execute(sql)).fetchall())   # type: ignore[arg-type]
        old_ar, new_ar = await ids("SELECT r.id FROM acct.receivables r WHERE " + _OLD_AR), await ids(acct._AR_DRIFT.format(cols="r.id"))
        old_l, new_l = await ids("SELECT l.id FROM acct.statement_lines l WHERE " + _OLD_LINE), await ids(acct._LINE_DRIFT.format(cols="l.id"))
        assert old_ar == new_ar and len(new_ar) >= 2
        assert old_l == new_l
