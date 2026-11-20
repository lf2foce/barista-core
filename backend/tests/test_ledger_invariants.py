"""SỔ CÁI — bất biến 4 của lõi bằng tài khoản của chính lõi (bản lõi của phần sổ cái trong ``barista-packs:commission/tests/test_money.py`` — ADR 0190): ghi
kép tổng 0 (lớp Python + trigger hoãn ở DB), chỉ thêm, khoá chống lặp, đảo bằng bút toán ngược, chỉ người có quyền sổ cả công ty ghi, và chạy thử của
trợ lý kiểm cả ràng buộc hoãn."""

import dataclasses
from uuid import uuid4

import psycopg
import pytest

from barista.db import db
from barista.kernel import executor, ledger, registry
from barista.kernel.clock import today
from barista.kernel.errors import Denied, Invalid
from barista.kernel.executor import Ctx, core_ctx
from tests.conftest import Who, owner_val

LINES = [("cash:bank", 1000), ("revenue:services", -1000)]


async def test_ledger_is_balanced_overall() -> None:
    assert await owner_val("SELECT coalesce(sum(amount_vnd), 0) FROM ledger.postings") == 0


async def test_unbalanced_journal_rejected_by_python_and_by_the_database(who: Who) -> None:
    ketoan = await who("ketoan")
    key = f"t-unbalanced-{uuid4().hex[:8]}"
    with pytest.raises(Invalid):
        async with db.tenant_tx(ketoan) as conn:
            await ledger.post(core_ctx(conn, ketoan), key=key, memo="x", source="test", lines=[("cash:bank", 5), ("revenue:services", -4)], on=today())
    # Bỏ qua lớp Python, chèn thẳng một dòng lệch: trigger hoãn chặn lúc commit (lệch / dưới hai dòng — base/104)
    with pytest.raises(psycopg.errors.RaiseException, match=r"lệch|hai dòng"):
        async with db.tenant_tx(ketoan) as conn:
            cur = await conn.execute("INSERT INTO ledger.journals (memo, source, idempotency_key, posted_on) VALUES ('x', 'test', %s, core.today())"
                                     " RETURNING id", (key + "-raw",))
            jid = (await cur.fetchone())["id"]  # type: ignore[index]
            aid = (await (await conn.execute("SELECT id FROM ledger.accounts WHERE code = 'cash:bank'")).fetchone())["id"]  # type: ignore[index]
            await conn.execute("INSERT INTO ledger.postings (journal_id, account_id, amount_vnd) VALUES (%s, %s, 100)", (jid, aid))
    with pytest.raises(psycopg.errors.RaiseException, match="cân Nợ / Có"):
        async with db.tenant_tx(ketoan) as conn:
            aid = (await (await conn.execute("SELECT id FROM ledger.accounts WHERE code = 'cash:bank'")).fetchone())["id"]  # type: ignore[index]
            await conn.execute("SELECT ledger.write_journal(%s, 'x', 'test', '', NULL, NULL, core.today(), %s::jsonb)",
                               (key + "-fn", f'[{{"account": "{aid}", "amount": 100}}, {{"account": "{aid}", "amount": -90}}]'))
    assert await owner_val("SELECT count(*) FROM ledger.journals WHERE idempotency_key LIKE %s", (key + "%",)) == 0


async def test_dry_run_of_an_unbalanced_entry_is_not_a_runnable_proposal(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đề xuất của trợ lý = chạy thử rồi hoàn. Bút toán lệch phải làm hỏng lần chạy thử — thẻ đề xuất không được hiện "chạy được" để người bấm Xác nhận
    mới lỗi. Từ base/104 cửa ghi của DB kiểm cân ngay lúc ghi (trước đây là ràng buộc hoãn tới commit — executor vẫn ép kiểm ràng buộc hoãn khi chạy thử)."""
    ketoan = await who("ketoan")
    key = f"t-dry-{uuid4().hex[:8]}"

    async def unbalanced(ctx: Ctx, inp: object) -> dict[str, str]:
        aid = await ctx.val("SELECT id FROM ledger.accounts WHERE code = 'cash:bank'")
        await ctx.val("SELECT ledger.write_journal(%s, 'x', 'test', '', NULL, NULL, core.today(), %s::jsonb)",
                      (key, f'[{{"account": "{aid}", "amount": 100}}, {{"account": "{aid}", "amount": -90}}]'))
        return {"ok": "không được tới đây"}

    op = registry.OPS["acct.record_payment"]
    assert op.agent == "propose"
    monkeypatch.setitem(registry.OPS, op.name, dataclasses.replace(op, handler=unbalanced))
    r = await executor.run(ketoan.as_agent(), op.name, {"code": "KHONG-CO", "amount_vnd": 100})
    assert r.status != "proposed" and "cân Nợ / Có" in (r.error or "")


async def test_ledger_is_append_only() -> None:
    for sql in ("UPDATE ledger.postings SET amount_vnd = amount_vnd WHERE id = (SELECT min(id) FROM ledger.postings)",
                "DELETE FROM ledger.postings WHERE id = (SELECT min(id) FROM ledger.postings)"):
        with pytest.raises(psycopg.errors.RaiseException, match="chỉ thêm"):
            async with db.owner_tx() as conn:  # kể cả owner (app còn không có quyền UPDATE)
                await conn.execute(sql)   # type: ignore[arg-type]


async def test_post_is_idempotent_reversal_nets_to_zero_and_needs_full_ledger_scope(who: Who) -> None:
    ketoan, sale1 = await who("ketoan"), await who("sale1")
    key = f"t-idem-{uuid4().hex[:8]}"
    async with db.tenant_tx(ketoan) as conn:
        a = (await ledger.post(core_ctx(conn, ketoan), key=key, memo="x", source="test", lines=LINES, on=today())).journal_id
        b = (await ledger.post(core_ctx(conn, ketoan), key=key, memo="x", source="test", lines=LINES, on=today())).journal_id
        await ledger.reverse(core_ctx(conn, ketoan), original_key=key, key=key + "-rev", memo="đảo", source="test")
    assert a == b
    assert await owner_val("SELECT count(*) FROM ledger.postings p JOIN ledger.journals j ON j.id = p.journal_id WHERE j.idempotency_key = %s", (key,)) == 2
    assert await owner_val("""SELECT sum(p.amount_vnd) FILTER (WHERE a.code = 'cash:bank') FROM ledger.postings p JOIN ledger.journals j ON j.id = p.journal_id
                              JOIN ledger.accounts a ON a.id = p.account_id WHERE j.idempotency_key IN (%s, %s)""", (key, key + "-rev")) == 0
    with pytest.raises(Denied):
        async with db.tenant_tx(sale1) as conn:
            await ledger.post(core_ctx(conn, sale1), key=key + "-sale", memo="x", source="test", lines=LINES, on=today())
