"""DUYỆT VÀ ĐỀ XUẤT GẮN CĂN CỨ (quyết định 0071) qua một lệnh của gói ngành — bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_approval_basis.py``:
căn cứ của lệnh đổi giữa lúc xin / chạy thử và lúc người bấm ⇒ không chạy.

Ca của gói thế vai: ``orderdesk.settle`` để trống số tiền ⇒ lấy phần còn phải trả theo lịch thanh toán LÚC CHẠY. Đề xuất soạn lúc còn 4 tỷ, lịch đổi
thành 4,5 tỷ, người bấm xác nhận ⇒ không được ghi thu theo số chưa ai thấy."""

import asyncio
from typing import Any
from uuid import UUID

import psycopg.errors as pg
import pytest
from psycopg.types.json import Jsonb

from barista.agent import chat
from barista.db import db
from barista.kernel import auth, executor, registry
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal
from tests.conftest import Who, edge, ok, owner_val
from tests.standin import helpers
from tests.test_assistant import Scripted, call
from tests.test_resume import Log

CMD = "orderdesk.settle"


async def _paid(code: str) -> int:
    """Tổng khách đã trả của một đơn — 0 nghĩa là lệnh CHƯA chạy."""
    return int(await owner_val("SELECT coalesce(sum(s.paid_vnd), 0) FROM orderdesk.stages s JOIN orderdesk.orders o ON o.id = s.order_id WHERE o.code = %s",
                               (code,)))


async def _replan(code: str, add: int) -> None:
    """Lịch thanh toán của đơn đổi (đợt 2 tăng ``add`` đồng) — ghi thẳng bằng quyền chủ DB như một người khác sửa đơn giữa chừng."""
    async with db.owner.connection() as conn:
        await conn.execute("""UPDATE orderdesk.stages s SET amount_vnd = s.amount_vnd + %s FROM orderdesk.orders o
                              WHERE o.id = s.order_id AND o.code = %s AND s.seq = 2""", (add, code))


async def test_confirming_a_proposal_after_the_plan_changed_does_not_run(who: Who) -> None:
    sale1 = await who("sale1")
    o = await helpers.order(who, owner="sale1")
    inp = {"code": o["code"]}
    proposed = await executor.run(sale1.as_agent(), CMD, inp)
    assert proposed.status == "proposed"
    basis = proposed.output["basis"]
    assert list(basis) == [f"Số còn phải trả / lịch thanh toán đơn {o['code']}"]      # nhãn gọi tên đối tượng — người đọc hiểu cái gì đổi

    await _replan(o["code"], 500_000_000)
    r = await executor.run(sale1, CMD, inp, expect_basis=basis)
    assert r.status == "conflict" and "Căn cứ đã đổi" in r.error and o["code"] in r.error
    assert await _paid(o["code"]) == 0

    # người tự ghi số tiền trên thẻ ⇒ số còn lại theo lịch không còn là căn cứ; người chọn lại đơn khác ⇒ đơn cũ không tính
    fixed = {**inp, "amount_vnd": 1_000_000_000}
    again = await executor.run(sale1.as_agent(), CMD, fixed)
    await _replan(o["code"], 100_000_000)
    assert (await executor.run(sale1, CMD, fixed, expect_basis=again.output["basis"])).status == "ok"
    assert await _paid(o["code"]) == 1_000_000_000
    other = await helpers.order(who, owner="sale1")
    assert (await executor.run(sale1, CMD, {"code": other["code"]}, expect_basis=basis, edited=True)).status == "ok"
    assert await _paid(other["code"]) == 4_000_000_000
    # KHÔNG sửa gì trên thẻ mà căn cứ khác nhãn (đề xuất của đơn khác) ⇒ vẫn chặn — chỉ ``edited`` mới bỏ nhãn không còn dùng
    third = await helpers.order(who, owner="sale1")
    assert (await executor.run(sale1, CMD, {"code": third["code"]}, expect_basis=basis)).status == "conflict"


async def test_confirm_through_the_chat_card_is_refused_when_the_basis_changed(who: Who) -> None:
    sale1 = await who("sale1")
    o = await helpers.order(who, owner="sale1")
    model = Scripted([[call(CMD, code=o["code"])], "Không ghi được vì lịch thanh toán đã đổi ạ."])
    first = Log()
    await chat.chat_turn(sale1, f"Ghi khách trả hết đơn {o['code']}", emit=first, model=model)
    card = first.proposals()[0]

    await _replan(o["code"], 200_000_000)
    after = Log()
    await chat.decide(sale1, card["id"], "confirm", card["input"], f"k-basis-{o['code']}", emit=after, model=model)
    rejected = after.of("rejected")
    assert rejected and rejected[0]["status"] == "conflict" and "Căn cứ đã đổi" in rejected[0]["message"]
    assert await _paid(o["code"]) == 0


async def _approver(tid: UUID, approval_id: UUID) -> Principal:
    uid = await owner_val("SELECT approver_ids[1] FROM core.approvals WHERE id = %s", (approval_id,))
    async with db.owner.connection() as conn:
        p = await auth.load(conn, tid, user_id=uid)
    assert p is not None
    return p


async def test_approval_goes_stale_at_any_step_when_the_basis_changed(who: Who, company: UUID) -> None:
    sale1 = await who("sale1")
    steps: list[dict[str, Any]] = [{"kind": "team_lead"}, {"kind": "role", "role": "accountant"}]   # hai bước: trưởng nhóm rồi kế toán
    await owner_val("INSERT INTO core.approval_chains (tenant_id, key, label, steps) VALUES (%s, 'order_settle_test', 'Ghi thu đơn (thử)', %s) RETURNING 1",
                    (company, Jsonb(steps)))
    await owner_val("INSERT INTO core.approval_rules (tenant_id, command, chain) VALUES (%s, %s, 'order_settle_test') RETURNING 1", (company, CMD))
    try:
        o = await helpers.order(who, owner="sale1")
        label = f"Số còn phải trả / lịch thanh toán đơn {o['code']}"
        r = await executor.run(sale1, CMD, {"code": o["code"]})
        assert r.status == "pending_approval" and r.approval_id
        aid = UUID(r.approval_id)
        assert list(await owner_val("SELECT basis FROM core.approvals WHERE id = %s", (aid,))) == [label]   # phiếu ghi căn cứ lúc xin

        lead = await _approver(company, aid)
        assert (await ok(edge(lead), "core.decide", approval_id=aid))["status"] == "pending"   # bước 1 qua: căn cứ chưa đổi

        await _replan(o["code"], 800_000_000)
        accountant = await _approver(company, aid)
        assert accountant.user_id != lead.user_id
        out = await ok(edge(accountant), "core.decide", approval_id=aid)
        assert out["status"] == "stale" and "Căn cứ đã đổi" in out["warning"] and out["changed"] == [label]
        assert await owner_val("SELECT status FROM core.approvals WHERE id = %s", (aid,)) == "stale"
        assert await _paid(o["code"]) == 0
        assert await owner_val("SELECT status FROM core.audit_log WHERE ref = %s ORDER BY id DESC LIMIT 1", (str(aid),)) == "conflict"
        assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Phiếu hết căn cứ%%'",
                               (sale1.user_id,)) >= 1
        assert (await executor.run(edge(accountant), "core.decide", {"approval_id": str(aid)})).status == "invalid"   # đã kết thúc

        # phiếu mới trên dữ liệu hiện tại, không ai đổi gì ⇒ duyệt đủ hai bước thì chạy như cũ
        aid2 = UUID((await executor.run(sale1, CMD, {"code": o["code"]})).approval_id or "")
        await ok(edge(await _approver(company, aid2)), "core.decide", approval_id=aid2)
        assert (await ok(edge(await _approver(company, aid2)), "core.decide", approval_id=aid2))["status"] == "executed"
        assert await _paid(o["code"]) == 4_800_000_000
    finally:
        await owner_val("DELETE FROM core.approval_rules WHERE tenant_id = %s AND command = %s RETURNING 1", (company, CMD))
        await owner_val("DELETE FROM core.approval_chains WHERE tenant_id = %s AND key = 'order_settle_test' RETURNING 1", (company,))


async def test_basis_holds_the_order_until_the_command_commits(who: Who) -> None:
    """Không có khe giữa lúc so căn cứ và lúc lệnh đọc lại: căn cứ đã tính trong giao dịch ⇒ người khác đổi đơn phải chờ giao dịch xong."""
    sale1 = await who("sale1")
    o = await helpers.order(who, owner="sale1")
    op = registry.get(CMD)
    assert op is not None
    async with db.tenant_tx(sale1, rollback=True) as conn:
        await executor.basis_of(core_ctx(conn, sale1), op, {"code": o["code"]})
        async with db.owner.connection() as other:
            await other.execute("SET lock_timeout = '300ms'")
            with pytest.raises(pg.LockNotAvailable):
                await other.execute("UPDATE orderdesk.orders SET amount_vnd = amount_vnd + 1 WHERE code = %s", (o["code"],))
        # người thứ hai xác nhận CÙNG đơn phải chờ ngay ở bước căn cứ (khoá ghi). Khoá chia sẻ sẽ cho cả hai qua rồi cùng nâng lên khoá ghi trong lệnh
        # ⇒ kẹt nhau, người thua nhận "Lỗi hệ thống" thay vì "căn cứ đã đổi"
        truonga = await who("truonga")
        async with db.tenant_tx(truonga, rollback=True) as conn2:
            await conn2.execute("SET LOCAL lock_timeout = '300ms'")
            with pytest.raises(pg.LockNotAvailable):
                await executor.basis_of(core_ctx(conn2, truonga), op, {"code": o["code"]})


async def test_two_people_confirming_the_same_order_get_one_run_and_one_conflict_not_a_deadlock(who: Who) -> None:
    sale1, truonga = await who("sale1"), await who("truonga")                           # trưởng team A thấy và ghi được đơn của sale1
    o = await helpers.order(who, owner="sale1")
    a, b = {"code": o["code"]}, {"code": o["code"]}
    pa = (await executor.run(sale1.as_agent(), CMD, a)).output["basis"]
    pb = (await executor.run(truonga.as_agent(), CMD, b)).output["basis"]
    results = await asyncio.gather(executor.run(sale1, CMD, a, expect_basis=pa), executor.run(truonga, CMD, b, expect_basis=pb))
    assert sorted(r.status for r in results) == ["conflict", "ok"], [(r.status, r.error) for r in results]
    assert await _paid(o["code"]) == 4_000_000_000
