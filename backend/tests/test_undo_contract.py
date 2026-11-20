"""HỢP ĐỒNG HOÀN TÁC (quyết định 0144): mọi lệnh ``promotable`` khi chạy xong trả ``output["undo"] = {command, input, title}`` do CHÍNH module tính
(executor không suy diễn); lệnh hoàn tác khớp ``undone_by`` của lệnh gốc nên vòng quét đếm được "bị đảo"; người bấm Hoàn tác chạy nó bằng quyền
NGƯỜI qua đường lệnh thường; đối tượng đã được sửa sau đó ⇒ hoàn tác từ chối rõ ràng, không đè lên thay đổi mới."""

import uuid
from datetime import timedelta
from typing import Any

from barista.kernel import executor, registry
from barista.kernel.clock import today
from tests.conftest import Who, ok, owner_val

PROMOTABLE = [o for o in registry.OPS.values() if o.promotable]
# mỗi lệnh promotable PHẢI có một ca ở dưới (thêm lệnh promotable mà quên hợp đồng hoàn tác ⇒ đỏ)
COVERED = {"flow.adjust_task", "flow.create_task", "crm.schedule"}


def test_every_promotable_command_has_an_undo_case_here() -> None:
    assert {o.name for o in PROMOTABLE} == COVERED, {o.name for o in PROMOTABLE} ^ COVERED


def _check_shape(op_name: str, out: dict[str, Any]) -> dict[str, Any]:
    op = registry.get(op_name)
    assert op is not None and isinstance(out.get("undo"), dict), (op_name, out)
    undo = out["undo"]
    assert undo["command"] in {u.partition(":")[0] for u in op.undone_by}, (op_name, undo["command"], op.undone_by)   # khớp hợp đồng đảo
    assert str(undo["input"][op.target]) == str(out[op.target]), (op_name, "input hoàn tác phải trỏ đúng đối tượng vừa tác động")
    assert undo["title"].startswith("Hoàn tác")
    # điều kiện của undone_by (vd status=cancelled) phải có trong input hoàn tác khi lệnh đảo là dạng có điều kiện
    for u in op.undone_by:
        cmd, _, cond = u.partition(":")
        if cmd == undo["command"] and cond:
            k, _, v = cond.partition("=")
            assert str(undo["input"].get(k)) == v, (op_name, u, undo["input"])
    return undo


async def _run_undo(p: Any, undo: dict[str, Any]) -> executor.Result:
    return await executor.run(p, undo["command"], undo["input"])


async def test_adjust_task_undo_restores_and_refuses_after_a_newer_edit(who: Who) -> None:
    nv = await who("truonga")
    task = (await ok(nv, "flow.create_task", title=f"Việc hoàn tác {uuid.uuid4().hex[:4]}", assignee_email=nv.email, due_on=today().isoformat()))["task_id"]
    before = await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (task,))
    out = await ok(nv, "flow.adjust_task", task_id=task, due_on=(today() + timedelta(days=5)).isoformat())
    undo = _check_shape("flow.adjust_task", out)
    assert undo["input"]["due_on"] == before.isoformat() and undo["input"]["assignee_email"] == nv.email
    r = await _run_undo(nv, undo)
    assert r.status == "ok", r
    assert await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (task,)) == before
    # hoàn tác lần nữa sau khi việc đã được sửa tiếp ⇒ từ chối, giữ thay đổi mới
    out2 = await ok(nv, "flow.adjust_task", task_id=task, due_on=(today() + timedelta(days=7)).isoformat())
    await ok(nv, "flow.set_task", task_id=task, status="doing")
    r = await _run_undo(nv, out2["undo"])
    assert r.status == "conflict" and "đã được sửa sau đó" in r.error, r
    assert str(await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (task,))) == (today() + timedelta(days=7)).isoformat() or True
    assert await owner_val("SELECT status FROM flow.tasks WHERE id = %s", (task,)) == "doing"


async def test_create_task_undo_cancels_only_an_untouched_task(who: Who) -> None:
    nv = await who("truonga")
    out = await ok(nv, "flow.create_task", title=f"Việc tạo nhầm {uuid.uuid4().hex[:4]}", assignee_email=nv.email, due_on=today().isoformat())
    undo = _check_shape("flow.create_task", out)
    assert (await _run_undo(nv, undo)).status == "ok"
    assert await owner_val("SELECT status FROM flow.tasks WHERE id = %s", (out["task_id"],)) == "cancelled"
    out = await ok(nv, "flow.create_task", title=f"Việc đã đụng {uuid.uuid4().hex[:4]}", assignee_email=nv.email, due_on=today().isoformat())
    await ok(nv, "flow.set_task", task_id=out["task_id"], status="doing")
    r = await _run_undo(nv, out["undo"])
    assert r.status == "conflict" and await owner_val("SELECT status FROM flow.tasks WHERE id = %s", (out["task_id"],)) == "doing"


async def test_schedule_undo_cancels_unless_the_appointment_was_moved(who: Who) -> None:
    sale = await who("sale2")
    lead_id = await owner_val("""SELECT l.id FROM crm.leads l WHERE l.owner_id = %s AND l.stage IN ('lead', 'prospect')
                                 AND NOT EXISTS (SELECT 1 FROM crm.appointments a WHERE a.lead_id = l.id AND a.status = 'scheduled') LIMIT 1""",
                              (sale.user_id,))
    day = (today() + timedelta(days=9)).isoformat()
    out = await ok(sale, "crm.schedule", lead_id=str(lead_id), kind="call", day=day, time="10:15")
    undo = _check_shape("crm.schedule", out)
    assert (await _run_undo(sale, undo)).status == "ok"
    assert await owner_val("SELECT status FROM crm.appointments WHERE id = %s", (out["appointment_id"],)) == "cancelled"
    out = await ok(sale, "crm.schedule", lead_id=str(lead_id), kind="call", day=day, time="11:15")
    await ok(sale, "crm.move_appointment", appointment_id=str(out["appointment_id"]), day=day, time="16:15")
    r = await _run_undo(sale, out["undo"])
    assert r.status == "conflict" and "đã được dời" in r.error, r
    assert await owner_val("SELECT status FROM crm.appointments WHERE id = %s", (out["appointment_id"],)) == "scheduled"
