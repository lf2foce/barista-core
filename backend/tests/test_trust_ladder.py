"""THANG TIN CẬY của trợ lý (quyết định 0143): mức tự chủ theo lệnh là chính sách công ty — nới từ "đề xuất" lên "tự làm" chỉ cho lệnh ``promotable``,
khi thành tích đề xuất đủ ngưỡng công ty đặt, có trần số lần / ngày (và tiền), qua chủ tài khoản duyệt; vượt trần ⇒ rơi về đề xuất; siết nhóm vẫn
áp; lỗi nhiều khi tự làm ⇒ vòng quét tự hạ và báo chủ tài khoản. Mỗi test dùng công ty RIÊNG (nới ở công ty mẫu làm lệch test khác)."""

import uuid
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.kernel.principal import Principal, system_principal
from tests.conftest import approved, ok, owner_val, q

PEOPLE = [("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True),
          ("nv", "Nhân viên thử", "staff", "HCNS", False)]


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    d = f"t{uuid.uuid4().hex[:8]}.demo"
    tid = await seed.create_tenant(f"Công ty tin cậy {d}", d, people=PEOPLE, packs=(), about="công ty thử (test)")
    return {"tid": tid, "d": d}


async def _who(co: dict[str, Any], local: str) -> Principal:
    return await seed.who(co["tid"], local, co["d"])


async def _task(nv: Principal) -> str:
    out = await ok(nv, "flow.create_task", title=f"Việc thử {uuid.uuid4().hex[:4]}", assignee_email=nv.email, due_on=today().isoformat())
    return str(out["task_id"])


async def _propose_and_confirm(nv: Principal, task_id: str, n: int, *, edit_last: bool = False) -> None:
    """Trợ lý soạn n đề xuất dời hạn, người xác nhận (lần cuối có thể SỬA) — cùng vết (0086) như thẻ chat / buổi sáng."""
    agent = nv.as_agent()
    for i in range(n):
        inp = {"task_id": task_id, "due_on": (today() + timedelta(days=i + 1)).isoformat()}
        with executor.tracing(f"test-trust:{task_id}:{i}:{uuid.uuid4().hex[:6]}"):
            r = await executor.run(agent, "flow.adjust_task", inp)
            assert r.status == "proposed", r
            human = {**inp, "note": "sửa tay"} if edit_last and i == n - 1 else inp
            r = await executor.run(nv, "flow.adjust_task", human, edited=human != inp)
            assert r.status == "ok", r


def test_promotable_only_on_proposal_commands() -> None:
    """Rào cấu trúc: lệnh nới được phải là lệnh ghi nghiệp vụ trợ lý soạn thẻ — không có lệnh duyệt / quyền / tiền ra."""
    promotable = [op for op in registry.OPS.values() if op.promotable]
    assert promotable and all(op.kind == "command" and op.agent == "propose" and not op.internal for op in promotable)
    assert not any(op.module in ("admin", "core", "acct", "commission") for op in promotable)


async def test_cannot_promote_a_hard_ceiling_command(co: dict[str, Any]) -> None:
    admin = await _who(co, "admin")
    r = await executor.run(admin, "admin.set_agent_policy", {"command": "flow.set_plan", "mode": "allow", "daily_cap": 5})
    assert r.status == "invalid" and "không nới" in r.error
    r = await executor.run(admin, "admin.set_agent_policy", {"command": "flow.adjust_task", "mode": "allow"})
    assert r.status == "invalid" and "trần số lần" in r.error


async def test_promotion_needs_track_record_then_runs_within_caps(co: dict[str, Any]) -> None:
    admin, nv = await _who(co, "admin"), await _who(co, "nv")
    task = await _task(nv)
    # chưa có thành tích ⇒ không nới được, kể cả chủ tài khoản
    r = await executor.run(admin, "admin.set_agent_policy", {"command": "flow.adjust_task", "mode": "allow", "daily_cap": 2})
    assert r.status == "invalid" and "Chưa đủ thành tích" in r.error
    # công ty tin trợ lý: HẠ ngưỡng (qua chủ tài khoản duyệt) — 4 đề xuất, 75% duyệt nguyên văn
    await approved(admin, "admin.set_agent_trust", min_proposals=4, min_approved_pct=75, window_days=30, max_errors_7d=2)
    await _propose_and_confirm(nv, task, 4, edit_last=True)   # 3 nguyên văn + 1 sửa = 75%
    page = await q(admin, "admin.agent_policies")
    row = next(x for x in page["rows"] if x["id"] == "flow.adjust_task")
    assert row["proposed"] == 4 and row["approved_pct"] == "75%" and row["can_promote"] == "yes" and row["promotable"] == "Nới được"
    # 75% đủ ngưỡng 75; nếu công ty đòi 90% thì chưa
    await approved(admin, "admin.set_agent_trust", min_proposals=4, min_approved_pct=90, window_days=30, max_errors_7d=2)
    r = await executor.run(admin, "admin.set_agent_policy", {"command": "flow.adjust_task", "mode": "allow", "daily_cap": 2})
    assert r.status == "invalid" and "75% duyệt nguyên văn" in r.error
    await approved(admin, "admin.set_agent_trust", min_proposals=4, min_approved_pct=75, window_days=30, max_errors_7d=2)
    # nới = cấp quyền ⇒ phiếu cho chủ tài khoản; sau duyệt: căn cứ (thành tích lúc đặt) + ai đặt lưu kèm
    out = await approved(admin, "admin.set_agent_policy", command="flow.adjust_task", mode="allow", daily_cap=2)
    assert out["promoted"] is True
    pol = await owner_val("SELECT to_jsonb(p) FROM core.agent_policies p WHERE tenant_id = %s AND command = 'flow.adjust_task'", (co["tid"],))
    assert pol["mode"] == "allow" and pol["daily_cap"] == 2 and pol["basis"]["proposed"] == 4 and pol["set_by"] == str(admin.user_id)
    # trợ lý chạy THẲNG trong trần: 2 lần ok (nhật ký via=agent), lần 3 trong ngày ⇒ rơi về đề xuất
    agent = nv.as_agent()
    before = await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (UUID(task),))
    for i in (3, 4):
        r = await executor.run(agent, "flow.adjust_task", {"task_id": task, "due_on": (today() + timedelta(days=10 + i)).isoformat()}, attended=True)
        assert r.status == "ok", r
    assert await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (UUID(task),)) != before   # đã ghi thật (hạn dời theo lịch làm việc)
    r = await executor.run(agent, "flow.adjust_task", {"task_id": task, "due_on": (today() + timedelta(days=20)).isoformat()}, attended=True)
    assert r.status == "proposed"
    assert await owner_val("SELECT runs FROM core.agent_autoruns WHERE tenant_id = %s AND command = 'flow.adjust_task' AND day = core.today()",
                           (co["tid"],)) == 3   # lần vượt cũng đếm (để thấy trợ lý đã chạm trần)
    assert await owner_val("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s AND command = 'flow.adjust_task' AND via = 'agent' AND status = 'ok'",
                           (co["tid"],)) == 2
    # siết cả nhóm vẫn áp lên lệnh đã nới
    await ok(admin, "admin.set_agent_group", group="flow.*", mode="propose")
    await owner_val("DELETE FROM core.agent_autoruns WHERE tenant_id = %s RETURNING 1", (co["tid"],))
    r = await executor.run(agent, "flow.adjust_task", {"task_id": task, "due_on": (today() + timedelta(days=21)).isoformat()}, attended=True)
    assert r.status == "proposed"
    await ok(admin, "admin.set_agent_group", group="flow.*", mode="")
    # bỏ nới ⇒ về trần (siết / bỏ không cần duyệt)
    await ok(admin, "admin.set_agent_policy", command="flow.adjust_task", mode="")
    r = await executor.run(agent, "flow.adjust_task", {"task_id": task, "due_on": (today() + timedelta(days=22)).isoformat()})
    assert r.status == "proposed"


async def test_errors_while_running_directly_demote_and_tell_the_owner(co: dict[str, Any]) -> None:
    admin, giamdoc, nv = await _who(co, "admin"), await _who(co, "giamdoc"), await _who(co, "nv")
    task = await _task(nv)
    await approved(admin, "admin.set_agent_trust", min_proposals=2, min_approved_pct=50, window_days=30, max_errors_7d=2)
    await _propose_and_confirm(nv, task, 2)
    await approved(admin, "admin.set_agent_policy", command="flow.adjust_task", mode="allow", daily_cap=20)
    sysp = system_principal(co["tid"])
    assert (await ok(sysp, "admin.review_agent_policies"))["demoted"] == 0
    # trợ lý chạy thẳng lỗi hệ thống 2 lần trong 7 ngày (ghi nhật ký như executor ghi)
    for _ in range(2):
        await owner_val("""INSERT INTO core.audit_log (tenant_id, user_id, via, command, input, status, error, trace)
                           VALUES (%s, %s, 'agent', 'flow.adjust_task', '{}', 'error', 'lỗi thử', 'test') RETURNING id""", (co["tid"], nv.user_id))
    assert (await ok(sysp, "admin.review_agent_policies"))["demoted"] == 1
    assert await owner_val("SELECT count(*) FROM core.agent_policies WHERE tenant_id = %s AND command = 'flow.adjust_task'", (co["tid"],)) == 0
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND title LIKE 'Trợ lý bị hạ về đề xuất%%'",
                           (co["tid"], giamdoc.user_id)) == 1
    r = await executor.run(nv.as_agent(), "flow.adjust_task", {"task_id": task, "due_on": (today() + timedelta(days=3)).isoformat()}, attended=True)
    assert r.status == "proposed"


async def test_without_an_approval_rule_nothing_can_be_loosened(co: dict[str, Any]) -> None:
    """FAIL CLOSED (review an ninh 08/11): công ty không có luật duyệt cho lệnh nới ⇒ nới / hạ ngưỡng bị từ chối, không chạy thẳng, không phiếu."""
    admin, nv = await _who(co, "admin"), await _who(co, "nv")
    rules = await owner_val("""SELECT jsonb_agg(jsonb_build_array(command, chain)) FROM core.approval_rules WHERE tenant_id = %s
                               AND command IN ('admin.set_agent_policy', 'admin.set_agent_trust')""", (co["tid"],))
    assert rules and len(rules) == 2   # công ty mới: luật mặc định của gói nền (công ty cũ: migration 051 chèn)
    await owner_val("DELETE FROM core.approval_rules WHERE tenant_id = %s AND command IN ('admin.set_agent_policy', 'admin.set_agent_trust') RETURNING 1",
                    (co["tid"],))
    try:
        before = await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s", (co["tid"],))
        r = await executor.run(admin, "admin.set_agent_trust", {"min_proposals": 1, "min_approved_pct": 0, "window_days": 30, "max_errors_7d": 100})
        assert r.status == "denied" and "chưa có luật duyệt" in r.error
        task = await _task(nv)
        await _propose_and_confirm(nv, task, 1)
        r = await executor.run(admin, "admin.set_agent_policy", {"command": "flow.adjust_task", "mode": "allow", "daily_cap": 10000})
        assert r.status == "denied" and "chưa có luật duyệt" in r.error
        assert await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s", (co["tid"],)) == before
        assert await owner_val("SELECT count(*) FROM core.agent_trust WHERE tenant_id = %s AND min_proposals = 1", (co["tid"],)) == 0
        # siết vẫn được (không phải cấp quyền)
        await ok(admin, "admin.set_agent_policy", command="flow.adjust_task", mode="deny")
        await ok(admin, "admin.set_agent_policy", command="flow.adjust_task", mode="")
    finally:
        for c, ch in rules:
            await owner_val("INSERT INTO core.approval_rules (tenant_id, command, chain) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING 1",
                            (co["tid"], c, ch))


async def test_promotion_applies_only_to_attended_turns(co: dict[str, Any]) -> None:
    """Lượt KHÔNG người (việc theo dõi chạy nền, đề xuất buổi sáng) không được chạy thẳng dù lệnh đã nới — đầu vào có thể là chữ người ngoài nộp."""
    admin, nv = await _who(co, "admin"), await _who(co, "nv")
    task = await _task(nv)
    await approved(admin, "admin.set_agent_trust", min_proposals=2, min_approved_pct=50, window_days=30, max_errors_7d=3)
    await _propose_and_confirm(nv, task, 2)
    await approved(admin, "admin.set_agent_policy", command="flow.adjust_task", mode="allow", daily_cap=20)
    inp = {"task_id": task, "due_on": (today() + timedelta(days=5)).isoformat()}
    assert (await executor.run(nv.as_agent(), "flow.adjust_task", inp)).status == "proposed"   # mặc định: không người ⇒ đề xuất
    r = await executor.run(nv.as_agent(), "flow.adjust_task", inp, attended=True)
    assert r.status == "ok"
    assert await owner_val("SELECT ref FROM core.audit_log WHERE tenant_id = %s AND command = 'flow.adjust_task' AND via = 'agent' AND status = 'ok'"
                           " ORDER BY id DESC LIMIT 1", (co["tid"],)) == f"autorun:{task}"   # lần tự làm theo chính sách nới + đối tượng
    await ok(admin, "admin.set_agent_policy", command="flow.adjust_task", mode="")


def test_every_promotable_command_declares_how_a_person_undoes_it() -> None:
    """0143 lát 2: lệnh nới được phải khai đối tượng + lệnh đảo có thật — không thì không đo được trợ lý làm đúng hay sai."""
    for op in (o for o in registry.OPS.values() if o.promotable):
        assert op.target and op.undone_by, op.name
        for u in op.undone_by:
            undo = registry.get(u.partition(":")[0])
            assert undo is not None and undo.kind == "command", (op.name, u)
            assert op.target in undo.input.model_fields, (op.name, u, "lệnh đảo phải mang cùng ô đối tượng")


async def test_people_undoing_autoruns_demote_the_command() -> None:
    """Trợ lý tự làm hợp lệ (không lỗi) nhưng người phải SỬA / HUỶ lại ⇒ tính là sai; đủ ngưỡng ⇒ vòng quét tự hạ + báo chủ tài khoản. Công ty
    riêng: lỗi giả của test khác trong công ty dùng chung sẽ cộng vào ngưỡng."""
    d = f"u{uuid.uuid4().hex[:8]}.demo"
    co = {"tid": await seed.create_tenant(f"Công ty đảo {d}", d, people=PEOPLE, packs=(), about="công ty thử (test)"), "d": d}
    admin, giamdoc, nv = await _who(co, "admin"), await _who(co, "giamdoc"), await _who(co, "nv")
    await approved(admin, "admin.set_agent_trust", min_proposals=2, min_approved_pct=50, window_days=30, max_errors_7d=2)
    task = await _task(nv)
    await _propose_and_confirm(nv, task, 2)
    await approved(admin, "admin.set_agent_policy", command="flow.adjust_task", mode="allow", daily_cap=20)
    sysp = system_principal(co["tid"])
    t1, t2 = await _task(nv), await _task(nv)
    for t in (t1, t2):   # trợ lý tự dời hạn trong lượt chat có người
        r = await executor.run(nv.as_agent(), "flow.adjust_task", {"task_id": t, "due_on": (today() + timedelta(days=4)).isoformat()}, attended=True)
        assert r.status == "ok", r
    assert await owner_val("SELECT ref FROM core.audit_log WHERE tenant_id = %s AND via = 'agent' AND status = 'ok' AND command = 'flow.adjust_task'"
                           " ORDER BY id DESC LIMIT 1", (co["tid"],)) == f"autorun:{t2}"
    # người sửa lại MỘT việc ⇒ 1 lần bị đảo, chưa tới ngưỡng 2
    await ok(nv, "flow.adjust_task", task_id=t1, due_on=(today() + timedelta(days=1)).isoformat())
    row = next(x for x in (await q(admin, "admin.agent_policies"))["rows"] if x["id"] == "flow.adjust_task")
    assert row["reversed"] == 1
    assert (await ok(sysp, "admin.review_agent_policies"))["demoted"] == 0
    # việc kia bị huỷ ⇒ 2 lần ⇒ tự hạ; việc người làm KHÁC (đánh dấu xong) không phải đảo
    await ok(nv, "flow.set_task", task_id=t1, status="done")
    await ok(nv, "flow.set_task", task_id=t2, status="cancelled")
    assert (await ok(sysp, "admin.review_agent_policies"))["demoted"] == 1
    note = await owner_val("SELECT body FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND title LIKE 'Trợ lý bị hạ về đề xuất%%'"
                           " ORDER BY id DESC LIMIT 1", (co["tid"], giamdoc.user_id))
    assert "2 lần người sửa / huỷ" in note
    r = await executor.run(nv.as_agent(), "flow.adjust_task", {"task_id": task, "due_on": (today() + timedelta(days=6)).isoformat()}, attended=True)
    assert r.status == "proposed"


async def test_company_switch_lets_the_assistant_run_reversible_low_risk_writes_until_reversed() -> None:
    """0144 công tắc công ty: bật (qua chủ tài khoản) ⇒ lệnh rủi ro thấp đảo được chạy ngay trong lượt có người, trần ngày của công tắc; lượt không
    người vẫn đề xuất; bị đảo nhiều ⇒ vòng quét siết ĐÚNG lệnh đó về đề xuất (dòng chính sách riêng), lệnh khác vẫn chạy ngay."""
    d = f"s{uuid.uuid4().hex[:8]}.demo"
    co = {"tid": await seed.create_tenant(f"Công ty công tắc {d}", d, people=PEOPLE, packs=(), about="công ty thử (test)"), "d": d}
    admin, giamdoc, nv = await _who(co, "admin"), await _who(co, "giamdoc"), await _who(co, "nv")
    task = await _task(nv)
    inp = {"task_id": task, "due_on": (today() + timedelta(days=3)).isoformat()}
    assert (await executor.run(nv.as_agent(), "flow.adjust_task", inp, attended=True)).status == "proposed"   # công tắc tắt: như hôm nay
    before = await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s", (co["tid"],))
    await approved(admin, "admin.set_agent_trust", autorun_low_risk=True, autorun_daily_cap=2, max_errors_7d=2)   # bật = nới ⇒ qua chủ tài khoản
    assert await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s", (co["tid"],)) == before + 1
    assert (await executor.run(nv.as_agent(), "flow.adjust_task", inp)).status == "proposed"                    # không người ⇒ vẫn đề xuất
    r = await executor.run(nv.as_agent(), "flow.adjust_task", inp, attended=True)
    assert r.status == "ok"                                                                                        # có người ⇒ làm ngay
    page = await q(admin, "admin.agent_policies")
    row = next(x for x in page["rows"] if x["id"] == "flow.adjust_task")
    assert row["mode"] == "allow" and row["custom"] == "Công tắc công ty"
    # trần ngày của công tắc: lần 2 ok, lần 3 ⇒ đề xuất
    assert (await executor.run(nv.as_agent(), "flow.adjust_task", {**inp, "due_on": (today() + timedelta(days=4)).isoformat()}, attended=True)).status == "ok"
    assert (await executor.run(nv.as_agent(), "flow.adjust_task", {**inp, "due_on": (today() + timedelta(days=5)).isoformat()}, attended=True)).status == "proposed"
    # người sửa lại 2 lần việc trợ lý đã làm ⇒ vòng quét siết đúng lệnh này
    t2 = await _task(nv)
    await owner_val("DELETE FROM core.agent_autoruns WHERE tenant_id = %s RETURNING 1", (co["tid"],))
    assert (await executor.run(nv.as_agent(), "flow.adjust_task", {"task_id": t2, "due_on": (today() + timedelta(days=3)).isoformat()}, attended=True)).status == "ok"
    await ok(nv, "flow.adjust_task", task_id=task, due_on=(today() + timedelta(days=1)).isoformat())
    await ok(nv, "flow.set_task", task_id=t2, status="cancelled")
    sysp = system_principal(co["tid"])
    assert (await ok(sysp, "admin.review_agent_policies"))["demoted"] == 1
    assert await owner_val("SELECT mode FROM core.agent_policies WHERE tenant_id = %s AND command = 'flow.adjust_task'", (co["tid"],)) == "propose"
    assert await owner_val("SELECT autorun_low_risk FROM core.agent_trust WHERE tenant_id = %s", (co["tid"],)) is True   # công tắc vẫn bật
    await owner_val("DELETE FROM core.agent_autoruns WHERE tenant_id = %s RETURNING 1", (co["tid"],))
    assert (await executor.run(nv.as_agent(), "flow.adjust_task", {"task_id": task, "due_on": (today() + timedelta(days=6)).isoformat()}, attended=True)).status == "proposed"
    out = await executor.run(nv.as_agent(), "flow.create_task", {"title": "Việc trợ lý tạo", "assignee_email": nv.email, "due_on": today().isoformat()}, attended=True)
    assert out.status == "ok"   # lệnh đảo được khác vẫn chạy ngay dưới công tắc
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND title LIKE 'Trợ lý bị hạ về đề xuất%%'",
                           (co["tid"], giamdoc.user_id)) == 1
