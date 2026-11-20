"""Luật duyệt áp ở MỌI cửa (quyết định 0066): lệnh công ty đặt "phải duyệt" không chạy thẳng khi module khác gọi lồng (``invoke``) — lỗi rõ thay vì
lách luật; hệ thống / lệnh đang chạy sau duyệt không xét lại; xem trước nhập Excel báo "chờ duyệt" đúng như khi nhập thật. Chính sách trợ lý của công
ty ("đề xuất") cũng áp khi gọi lồng: lệnh trợ lý được làm thẳng mà bên trong ghi vào phần công ty muốn người xác nhận ⇒ cả lệnh thành thẻ đề xuất."""

import uuid
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.db import db
from barista.kernel import executor, imports, registry
from barista.kernel.clock import today
from barista.kernel.errors import Invalid
from barista.kernel.executor import Ctx, core_ctx
from barista.kernel.principal import Principal, issued, system_principal
from tests.conftest import edge, owner_val


async def _company(rule_on: str) -> tuple[UUID, Principal]:
    d = f"{uuid.uuid4().hex[:6]}.duyet.test"
    tid = await seed.create_tenant("Luật duyệt (test)", d, packs=("finance",), about="công ty thử luật duyệt",
                                   people=[("chu", "Chủ", "admin", "QT", True), ("gd", "Giám đốc", "director", "QT", False),
                                           ("ketoan", "Kế toán", "accountant", "KT", False)])
    await owner_val("INSERT INTO core.approval_rules (tenant_id, command, chain) VALUES (%s, %s, 'remittance') RETURNING 1", (tid, rule_on))
    return tid, await seed.who(tid, "ketoan", d)


async def test_nested_call_cannot_skip_the_companys_approval_rule() -> None:
    tid, kt = await _company("flow.create_task")
    task = {"title": "Gọi lại khách", "due_on": today().isoformat()}
    assert (await executor.run(kt, "flow.create_task", task)).status == "pending_approval"     # gọi thẳng: thành phiếu như trước

    async with db.tenant_tx(kt, rollback=True) as conn:                                        # gọi lồng bằng quyền người: không lọt
        with pytest.raises(Invalid, match="phải qua duyệt"):
            await executor.invoke(core_ctx(conn, kt), "flow.create_task", task)

    # lệnh đang chạy SAU duyệt: phiếu bao phần gọi lồng CÙNG chuỗi người duyệt; chuỗi khác vẫn phải duyệt riêng
    same = issued(replace(kt, approved_by=(kt.user_id,), approved_scope=("acct.add_statement_line", "remittance")))   # như core.decide cấp
    async with db.tenant_tx(same, rollback=True) as conn:
        assert (await executor.invoke(core_ctx(conn, same), "flow.create_task", task))["task_id"]
    other = issued(replace(kt, approved_by=(kt.user_id,), approved_scope=("acct.add_statement_line", "chuoi_khac")))
    async with db.tenant_tx(other, rollback=True) as conn:
        with pytest.raises(Invalid, match="phải qua duyệt"):
            await executor.invoke(core_ctx(conn, other), "flow.create_task", task)

    system = system_principal(tid, kt.user_id)                                                 # hệ thống (vòng quét, bên nghe sự kiện): hợp đồng module
    async with db.tenant_tx(system, rollback=True) as conn:
        assert (await executor.invoke(core_ctx(conn, system), "flow.create_task", task))["task_id"]


async def test_import_preview_reports_pending_approval_like_the_real_import() -> None:
    _, kt = await _company("acct.add_statement_line")
    rows = [{"Ngày": "05/11/2026", "Số tiền": "1.000.000", "Nội dung": "TT HD-1", "Mã GD": f"REF-{uuid.uuid4().hex[:8]}"}]
    preview = (await imports.run_import(kt, "acct.add_statement_line", rows, dry_run=True)).output
    real = (await imports.run_import(kt, "acct.add_statement_line", rows, dry_run=False)).output
    assert [r["status"] for r in preview["rows"]] == [r["status"] for r in real["rows"]] == ["pending_approval"]


async def test_agent_cannot_write_through_a_nested_command_the_company_wants_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    tid, kt = await _company("deals.cancel")
    await owner_val("INSERT INTO core.agent_policies (tenant_id, command, mode) VALUES (%s, 'flow.*', 'propose') RETURNING 1", (tid,))
    op = registry.get("acct.add_statement_line")
    assert op is not None   # lệnh mẫu trợ lý được làm thẳng (0151 hạ lệnh thật về propose ⇒ test đặt allow trên bản thay)

    async def handler(ctx: Ctx, inp: Any) -> dict[str, Any]:   # lệnh trợ lý được làm thẳng, bên trong tạo việc (nhóm Quy trình — công ty muốn xác nhận)
        return {"task_id": (await ctx.call("flow.create_task", title="Kiểm dòng sao kê", due_on=today().isoformat()))["task_id"]}

    monkeypatch.setitem(registry.OPS, op.name, replace(op, handler=handler, agent="allow"))
    tasks = "SELECT count(*) FROM flow.tasks WHERE tenant_id = %s"
    line = {"received_on": "2026-11-05", "amount_vnd": "1.000.000"}
    r = await executor.run(kt.as_agent(), op.name, line)
    assert r.status == "proposed" and r.output["preview"]["task_id"]                         # thành thẻ đề xuất đã chạy thử
    assert await owner_val(tasks, (tid,)) == 0                                                # chưa ghi gì
    assert (await executor.run(kt, op.name, line)).status == "ok" and await owner_val(tasks, (tid,)) == 1   # người bấm ⇒ chạy

    await owner_val("DELETE FROM core.agent_policies WHERE tenant_id = %s RETURNING 1", (tid,))
    assert (await executor.run(kt.as_agent(), op.name, line)).status == "ok"                  # công ty không đặt ⇒ trợ lý làm thẳng như cũ
    await owner_val("INSERT INTO core.agent_policies (tenant_id, command, mode) VALUES (%s, 'flow.*', 'propose') RETURNING 1", (tid,))
    async with db.tenant_tx(kt.as_agent(), rollback=True) as conn:                           # gọi lồng thẳng ở kernel: công ty đặt lại ⇒ đòi xác nhận
        with pytest.raises(executor.NeedsConfirmation):
            await executor.invoke(core_ctx(conn, kt.as_agent()), "flow.create_task", {"title": "x"})


@pytest.mark.parametrize("call_kind", ["system", "as"])
async def test_internal_calls_keep_the_agent_origin_and_company_policy(monkeypatch: pytest.MonkeyPatch, call_kind: str) -> None:
    tid, kt = await _company("deals.cancel")
    op = registry.OPS["acct.add_statement_line"]
    completed: list[bool] = []

    async def after_commit() -> None:
        completed.append(True)

    async def handler(ctx: Ctx, inp: Any) -> dict[str, Any]:
        line = await op.handler(ctx, inp)   # đã ghi trước khi gặp lệnh lồng cần xác nhận ⇒ phải hoàn lại cả phần này
        ctx.emit("review.origin", ref=inp.bank_ref)
        ctx.after_commit(after_commit)
        args = {"source": "acct.review_origin", "source_ref": inp.bank_ref, "label": "Phí thử", "nature": "revenue",
                "amount_vnd": 1_000_000, "due_on": "2026-11-05"}
        received = await ctx.call_system("acct.issue_receivable", **args) if call_kind == "system" else \
            await ctx.call_as("acct.issue_receivable", ctx.p, **args)
        return {**line, "receivable_code": received["code"]}

    monkeypatch.setitem(registry.OPS, op.name, replace(op, handler=handler, agent="allow"))
    target = registry.OPS["acct.issue_receivable"]   # lệnh giả mượn module acct làm bên gọi ⇒ thêm acct vào hợp đồng quyền hệ thống (0083) trong test
    monkeypatch.setitem(registry.OPS, target.name, replace(target, callers=(*target.callers, "acct")))
    policy = "INSERT INTO core.agent_policies (tenant_id, command, mode) VALUES (%s, 'acct.issue_receivable', %s) RETURNING 1"
    await owner_val(policy, (tid, "propose"))
    payload = {"received_on": "2026-11-05", "amount_vnd": "1tr", "bank_ref": "AGENT-ORIGIN"}
    proposed = await executor.run(kt.as_agent(), op.name, payload)
    assert proposed.status == "proposed" and proposed.output["preview"]["receivable_code"]
    for table in ("acct.statement_lines", "acct.receivables", "core.events"):
        assert await owner_val(f"SELECT count(*) FROM {table} WHERE tenant_id = %s", (tid,)) == 0
    assert completed == []

    await owner_val("UPDATE core.agent_policies SET mode = 'deny' WHERE tenant_id = %s RETURNING 1", (tid,))
    assert (await executor.run(kt.as_agent(), op.name, payload)).status == "denied"
    assert await owner_val("SELECT count(*) FROM acct.statement_lines WHERE tenant_id = %s", (tid,)) == 0
    assert completed == []
    # Người bấm / worker thuần hệ thống vẫn chạy; chính sách trợ lý không phải chính sách dừng nghiệp vụ của công ty.
    assert (await executor.run(kt, op.name, payload)).ok
    assert (await executor.run(system_principal(tid), op.name, {**payload, "bank_ref": "SYSTEM-ORIGIN"})).ok
    assert len(completed) == 2


async def test_an_approval_covers_its_own_command_and_same_chain_nested_calls_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phiếu đã duyệt chỉ bao ĐÚNG lệnh đã duyệt + lệnh lồng cùng chuỗi người duyệt. A qua quản trị, bên trong gọi B mà công ty bắt qua giám đốc
    ⇒ duyệt A không thay được duyệt B: phiếu A thành "lỗi" kèm lý do, không ghi gì (review 02/11)."""
    tid, kt = await _company("flow.create_task")                                               # B = tạo việc — chuỗi giám đốc
    chu = await seed.who(tid, "chu", kt.email.split("@")[1])
    await owner_val("""INSERT INTO core.approval_chains (tenant_id, key, label, steps) VALUES (%s, 'quan_tri', 'Quản trị duyệt',
                       '[{"kind": "role", "role": "admin"}]') RETURNING 1""", (tid,))
    op = registry.get("acct.add_statement_line")
    assert op is not None
    await owner_val("INSERT INTO core.approval_rules (tenant_id, command, chain) VALUES (%s, %s, 'quan_tri') RETURNING 1", (tid, op.name))

    async def handler(ctx: Ctx, inp: Any) -> dict[str, Any]:
        return {"task_id": (await ctx.call("flow.create_task", title="Kiểm dòng sao kê", due_on=today().isoformat()))["task_id"]}

    monkeypatch.setitem(registry.OPS, op.name, replace(op, handler=handler, agent="allow"))
    tasks = "SELECT count(*) FROM flow.tasks WHERE tenant_id = %s"

    async def approve() -> str:
        r = await executor.run(kt, op.name, {"received_on": "2026-11-05", "amount_vnd": "1.000.000"})
        assert r.status == "pending_approval"
        await executor.run(edge(chu), "core.decide", {"approval_id": r.approval_id, "approve": True})
        return await owner_val("SELECT status FROM core.approvals WHERE id = %s", (r.approval_id,))

    assert await approve() == "failed" and await owner_val(tasks, (tid,)) == 0                # chuỗi khác ⇒ không chạy kèm
    await owner_val("UPDATE core.approval_rules SET chain = 'quan_tri' WHERE tenant_id = %s AND command = 'flow.create_task' RETURNING 1", (tid,))
    assert await approve() == "executed" and await owner_val(tasks, (tid,)) == 1              # cùng chuỗi ⇒ người duyệt đã duyệt cả phần này
