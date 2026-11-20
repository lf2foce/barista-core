"""0068: ngoại lệ nằm trên nghĩa vụ CỦA KỲ, có căn cứ; không sửa mẫu hay lịch sử / hạn gốc, quyền người và trợ lý giống nhau."""

import asyncio
from datetime import date

import pytest
from psycopg import errors as pg

from barista.db import db
from barista.kernel import executor
from tests.conftest import ok, owner_val, q
from tests.test_books import _company, _open


async def test_supplement_a_period_without_changing_the_obligation_or_other_periods() -> None:
    tid, p = await _company()
    await _open(tid, "2026-10-05")
    period = await owner_val("SELECT id FROM books.periods WHERE tenant_id = %s AND period_start = '2026-08-01'", (tid,))
    args = {"period_id": str(period), "obligation_code": "BHXH", "due_on": "2026-09-15", "evidence": "Khách xác nhận phát sinh lao động tháng 8"}
    assert (await executor.run(p["kt1"], "books.add_filing", args)).status == "denied"
    assert (await executor.run(p["kt2"], "books.add_filing", {**args, "evidence": "  "})).status == "invalid"
    assert (await executor.run(p["kt2"].as_agent(), "books.add_filing", args)).status == "proposed"
    assert await owner_val("SELECT count(*) FROM books.filings WHERE period_id = %s", (period,)) == 0
    both = await asyncio.gather(*(executor.run(p["kt2"], "books.add_filing", args) for _ in range(2)))
    assert sorted(r.status for r in both) == ["conflict", "ok"]
    fid = next(r.output["filing_id"] for r in both if r.ok)
    f = await owner_val("SELECT to_jsonb(f) FROM books.filings f WHERE id = %s", (fid,))
    assert f["original_due_on"] == f["due_on"] == "2026-09-15" and f["rule_ref"] == "" and f["basis"] == args["evidence"]
    assert await owner_val("SELECT active FROM books.obligations WHERE tenant_id = %s AND code = 'BHXH'", (tid,)) is False
    assert await owner_val("SELECT count(*) FROM books.filings WHERE tenant_id = %s AND obligation_id = (SELECT id FROM books.obligations WHERE tenant_id = %s AND code = 'BHXH')",
                           (tid, tid)) == 1
    assert await owner_val("SELECT kind FROM books.filing_events WHERE filing_id = %s", (fid,)) == "added"
    view = await q(p["kt2"], "books.filing", filing_id=fid)
    assert view["actions"] and (await q(p["kt1"], "books.filing", filing_id=fid))["actions"] == []
    other, outsiders = await _company()
    assert (await executor.run(outsiders["admin"], "books.add_filing", args)).status == "not_found"
    assert other != tid


async def test_deadline_changes_are_scoped_audited_and_cannot_overwrite_a_stale_deadline() -> None:
    tid, p = await _company()
    await _open(tid, "2026-10-05")
    f = (await q(p["kt2"], "books.filings"))["rows"][0]
    args = {"filing_id": f["id"], "expected_due_on": f["due_on"], "due_on": "2026-11-10", "evidence": "Văn bản gia hạn giả lập (test)"}
    assert (await executor.run(p["kt1"], "books.reschedule_filing", args)).status == "denied"
    assert (await executor.run(p["kt2"], "books.reschedule_filing", {**args, "evidence": " "})).status == "invalid"
    assert (await executor.run(p["kt2"].as_agent(), "books.reschedule_filing", args)).status == "proposed"
    assert await owner_val("SELECT due_on FROM books.filings WHERE id = %s", (f["id"],)) == date.fromisoformat(f["due_on"])
    key = f"due-{f['id']}"
    assert (await executor.run(p["kt2"], "books.reschedule_filing", args, key=key)).ok
    assert (await executor.run(p["kt2"], "books.reschedule_filing", args, key=key)).replayed
    assert (await executor.run(p["kt2"], "books.reschedule_filing", {**args, "due_on": "2026-11-12"})).status == "conflict"
    stored = await owner_val("SELECT to_jsonb(f) FROM books.filings f WHERE id = %s", (f["id"],))
    assert stored["original_due_on"] == f["due_on"] and stored["due_on"] == "2026-11-10" and stored["calendar_ref"]
    assert await owner_val("SELECT count(*) FROM books.filing_events WHERE filing_id = %s", (f["id"],)) == 1
    view = await q(p["kt2"], "books.filing", filing_id=f["id"])
    assert "Đổi hạn" in str(view) and args["evidence"] in str(view)
    await ok(p["kt1"], "books.mark_filing", filing_id=f["id"], status="filed", evidence="Biên nhận test")
    assert (await executor.run(p["kt2"], "books.reschedule_filing", {**args, "expected_due_on": "2026-11-10"})).status == "invalid"
    # DB cũng giữ nguồn ban đầu và nhật ký chỉ thêm — không chỉ dựa vào người viết handler cẩn thận.
    with pytest.raises(pg.RaiseException, match="Nguồn tính hạn"):
        async with db.tenant_tx(p["admin"]) as conn:
            await conn.execute("UPDATE books.filings SET original_due_on = '2030-01-01' WHERE id = %s", (f["id"],))
    with pytest.raises(pg.InsufficientPrivilege):
        async with db.tenant_tx(p["admin"]) as conn:
            await conn.execute("DELETE FROM books.filing_events WHERE filing_id = %s", (f["id"],))


async def test_cancelled_period_cannot_receive_new_obligations() -> None:
    tid, p = await _company()
    await _open(tid, "2026-08-05")
    pid = await owner_val("UPDATE books.periods SET status = 'cancelled' WHERE tenant_id = %s RETURNING id", (tid,))
    r = await executor.run(p["admin"], "books.add_filing", {"period_id": str(pid), "obligation_code": "BHXH", "due_on": "2026-09-01", "evidence": "Test"})
    assert r.status == "invalid"
