"""Giao mục tiêu đo được (quyết định 0074 B): kế hoạch có chỉ số module công bố + đích; code đo, so với đường cần đạt, gộp vào rủi ro."""

from datetime import timedelta

from barista.agent import morning
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.principal import system_principal
from tests.conftest import Who, ok, owner_val, q


async def test_goal_is_measured_flags_risk_and_feeds_the_owner_morning_and_the_sponsor(who: Who) -> None:
    gd, lead, a = await who("giamdoc"), await who("mkt"), await who("mkt2")
    out = await ok(gd, "flow.propose_plan", title="50 khách dự mở bán (test)", team_code="MKT", due_on=(today() + timedelta(days=10)).isoformat(),
                   goal_metric="crm.metric_appointments", goal_target=500, goal_context="hsr",
                   tasks=[{"title": "Mời khách cũ", "assignee_email": a.email, "due_on": (today() + timedelta(days=8)).isoformat()}])
    pid = out["plan_id"]
    card = await q(lead, "flow.plan", plan_id=pid)
    kpi = next(b for b in card["blocks"] if b.get("type") == "kpis")["items"][0]
    assert kpi["label"] == "Mục tiêu" and kpi["value"].endswith("/500 khách")
    await owner_val("UPDATE flow.plans SET created_at = now() - interval '10 days' WHERE id = %s RETURNING 1", (pid,))   # nửa đường, số còn thấp
    card = await q(lead, "flow.plan", plan_id=pid)
    callout = next(b for b in card["blocks"] if b.get("type") == "callout")
    assert callout["title"] == "Có nguy cơ trễ" and "cần khoảng 250" in callout["text"]
    assert any(r["id"] == pid and r["risk"] == "risk" for r in (await q(lead, "flow.plans"))["rows"])
    # gợi ý CHƯA cắt theo trần 3 thẻ / ngày (trần do test_morning kiểm): kế hoạch của test khác trong cùng DB có thể xếp trên
    cards = [c for c in await morning.suggestions(lead) if (c.get("input") or {}).get("plan_id") == str(pid)]
    assert cards and cards[0]["command"] == "flow.create_task" and "Bù mục tiêu" in cards[0]["input"]["title"]   # việc đúng hạn mà số thiếu ⇒ thêm việc bù
    r = await ok(system_principal(gd.tenant_id), "flow.check_goals")
    assert r["alerted"] >= 1
    note = "SELECT count(*) FROM core.notifications WHERE user_id = %s AND title = 'Mục tiêu “50 khách dự mở bán (test)” đang chậm'"
    assert await owner_val(note, (gd.user_id,)) == 1                                                       # báo NGƯỜI GIAO mục tiêu
    await owner_val("UPDATE flow.plans SET goal_measured_at = NULL WHERE id = %s RETURNING 1", (pid,))
    await ok(system_principal(gd.tenant_id), "flow.check_goals")
    assert await owner_val(note, (gd.user_id,)) == 1                                                       # không báo dồn trong 3 ngày


async def test_goal_met_turns_green_and_finished_tasks_without_the_number_are_not_review(who: Who) -> None:
    gd, a = await who("giamdoc"), await who("mkt2")
    out = await ok(gd, "flow.propose_plan", title="Mục tiêu nhỏ (test)", team_code="MKT", due_on=(today() + timedelta(days=5)).isoformat(),
                   goal_metric="crm.metric_appointments", goal_target=10_000, tasks=[{"title": "Một việc", "assignee_email": a.email}])
    tid = await owner_val("SELECT id FROM flow.tasks WHERE plan_id = %s", (out["plan_id"],))
    await ok(a, "flow.set_task", task_id=str(tid), status="done")
    callout = next(b for b in (await q(gd, "flow.plan", plan_id=out["plan_id"]))["blocks"] if b.get("type") == "callout")
    assert callout["title"] != "Chờ nghiệm thu" and "mục tiêu chưa đạt" in callout["text"]                # xong việc ≠ xong mục tiêu
    await owner_val("UPDATE flow.plans SET goal_target = 1 WHERE id = %s RETURNING 1", (out["plan_id"],))   # dữ liệu mẫu đã có lịch hẹn ⇒ đạt
    card = await q(gd, "flow.plan", plan_id=out["plan_id"])
    assert next(b for b in card["blocks"] if b.get("type") == "kpis")["items"][0]["tone"] == "green"
    assert next(b for b in card["blocks"] if b.get("type") == "callout")["title"] == "Chờ nghiệm thu"


async def test_company_wide_numbers_need_a_company_wide_grant_and_unmet_goals_cannot_be_accepted(who: Who) -> None:
    """Review 04/11: nhân viên không xem được lead thì không gắn được "Lead mới" toàn công ty vào mục tiêu; chưa đạt đích thì server chặn nghiệm thu."""
    staff, gd, a = await who("nhansu"), await who("giamdoc"), await who("mkt2")
    assert "crm.metric_leads" not in {r["key"] for r in (await q(staff, "flow.metrics"))["rows"]}
    r = await executor.run(staff, "flow.propose_plan", {"title": "Lén xem lead (test)", "due_on": (today() + timedelta(days=5)).isoformat(),
                                                        "goal_metric": "crm.metric_leads", "goal_target": 5,
                                                        "tasks": [{"title": "X", "assignee_email": staff.email}]})
    assert r.status == "denied"
    out = await ok(gd, "flow.propose_plan", title="Đích không đạt (test)", due_on=(today() + timedelta(days=5)).isoformat(),
                   goal_metric="crm.metric_leads", goal_target=100_000, tasks=[{"title": "Một việc", "assignee_email": a.email}])
    tid = await owner_val("SELECT id FROM flow.tasks WHERE plan_id = %s", (out["plan_id"],))
    await ok(a, "flow.set_task", task_id=str(tid), status="done")
    r = await executor.run(gd, "flow.set_plan", {"plan_id": out["plan_id"], "status": "done"})
    assert r.status == "invalid" and "Chưa đạt mục tiêu" in (r.error or "")
    r = await executor.run(gd.as_agent(), "flow.set_plan", {"plan_id": out["plan_id"], "status": "done"})
    assert r.status == "invalid"                                                                           # trợ lý cũng bị chặn (chạy thử lỗi ngay)
