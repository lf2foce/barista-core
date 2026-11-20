"""Luật tự động mở rộng (ADR 0246): cò mới (lead đổi chặng / được giao), luật THEO NGÀY (nguồn ngày module khai + độ lệch, một lần mỗi (bản ghi,
ngày), chạy thử, trần / ngày, im với ngày tới hạn trước khi đặt luật, quyền người đặt nạp lại), hành động mới "chạy lệnh" (chỉ lệnh khai
``auto=True``) và "đề xuất" (thẻ trong kho đề xuất của người nhận), chặn chuỗi luật kích nhau.

Luật của test lọc theo TÊN một sale mới tinh (``Người làm`` / ``Người phụ trách``) và tắt khi xong — bộ test dùng chung DB, luật không được chạm bản ghi
của test khác."""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio

from barista.kernel import events, executor, registry
from barista.kernel.clock import today
from barista.kernel.forms import field_specs
from barista.kernel.principal import Principal, system_principal
from barista.modules.flow import automations as auto
from barista.modules.flow import m as flow_m
from tests.conftest import Who, approved, fresh_phone, fresh_sale, ok, owner_val, q


@pytest.fixture(autouse=True)
def _any_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto, "FROM_HOUR", 0)   # luật theo ngày chạy từ 8 giờ — test chạy được mọi giờ


@pytest_asyncio.fixture
async def rules(who: Who) -> AsyncIterator[list[str]]:
    """Mã các luật test tạo — tắt hết khi xong (luật còn bật sẽ chạy trên bản ghi của test khác)."""
    made: list[str] = []
    yield made
    admin = await who("admin")
    for rid in made:
        await executor.run(admin, "flow.set_automation", {"automation_id": rid, "active": False})


async def _rule(admin: Principal, made: list[str], **kw: Any) -> str:
    out = await ok(admin, "flow.save_automation", **({"name": f"Luật thử {uuid.uuid4().hex[:6]}"} | kw))
    made.append(str(out["automation_id"]))
    return str(out["automation_id"])


async def _sweep(tenant: Any, *rule_ids: str) -> None:
    """Một lượt vòng quét theo ngày (bỏ nhịp mỗi giờ của các luật này) rồi giao hết sự kiện."""
    for rid in rule_ids:
        await _owner_exec("UPDATE flow.automations SET swept_at = NULL WHERE id = %s", (rid,))
    r = await executor.run(system_principal(tenant), "flow.sweep_dated_automations", {})
    assert r.status == "ok", r.error
    await events.drain()


async def _owner_exec(sql: Any, params: Any = None) -> None:
    from barista.db import db

    async with db.owner.connection() as conn:
        await conn.execute(sql, params)


async def _runs(rid: str) -> list[dict[str, Any]]:
    return await owner_val("""SELECT coalesce(json_agg(json_build_object('status', status, 'note', note, 'ref', ref, 'on', on_date) ORDER BY id), '[]')
                              FROM flow.automation_runs WHERE automation_id = %s""", (rid,))


async def _task(admin: Principal, sale: Principal, due_days_ago: int) -> str:
    t = await ok(admin, "flow.create_task", title=f"Việc thử {uuid.uuid4().hex[:5]}", assignee_email=sale.email)
    await _owner_exec("UPDATE flow.tasks SET due_on = %s WHERE id = %s", (today() - timedelta(days=due_days_ago), t["task_id"]))
    return str(t["task_id"])


async def _notes(user_id: Any, title: str) -> list[str]:
    return await owner_val("SELECT coalesce(json_agg(body ORDER BY created_at), '[]') FROM core.notifications WHERE user_id = %s AND title = %s",
                           (user_id, title))


def test_new_triggers_and_date_sources_are_declared_and_auto_is_a_deliberate_choice() -> None:
    assert {"crm.lead_stage_changed", "crm.lead_assigned"} <= set(registry.TRIGGERS)
    assert registry.TRIGGERS["crm.lead_assigned"].owner == "owner_id" and registry.TRIGGERS["crm.lead_stage_changed"].ids == ("lead_id",)
    assert {"crm.follow_up_due", "flow.task_due", "acct.receivable_due"} <= set(registry.DATE_SOURCES)
    for key, s in registry.DATE_SOURCES.items():   # nguồn ngày là truy vấn NỘI BỘ chỉ động cơ luật gọi, có lý do hợp đồng
        op = registry.OPS[key]
        assert op.internal and op.callers == ("flow",) and op.why and op.agent == "deny", key
        assert not registry.DATE_RESERVED & set(s.fields), key
    assert {"crm.set_stage", "flow.set_task"} <= {k for k, o in registry.OPS.items() if o.auto}
    for k, o in registry.OPS.items():   # lệnh luật được chạy: rủi ro thấp khai tường minh, không nội bộ, trợ lý không bị cấm
        if o.auto:
            assert o.risk == "low" and not o.internal and o.agent != "deny", k
    with pytest.raises(ValueError, match="auto=True"):
        flow_m.command("zz_auto_probe", resource="flow", title="x", auto=True)   # không khai risk="low" ⇒ chặn lúc khai
    with pytest.raises(ValueError, match="auto=True"):
        flow_m.command("zz_auto_probe", resource="flow", title="x", auto=True, risk="low", internal=True)
    with pytest.raises(ValueError, match="auto=True"):
        flow_m.command("zz_auto_probe", resource="flow", title="x", auto=True, risk="low", agent="deny")
    assert "flow.zz_auto_probe" not in registry.OPS


async def test_rule_editor_offers_lists_not_codes(who: Who) -> None:
    admin, tech = await who("admin"), await _tech(who)
    dates = {o["value"] for o in await q(admin, "flow.date_source_options")}
    assert {"crm.follow_up_due", "flow.task_due", "acct.receivable_due"} <= dates
    cmds = {o["value"] for o in await q(admin, "flow.command_options", action="command", date_source="flow.task_due")}
    assert "flow.set_task" in cmds and "crm.set_stage" not in cmds              # lệnh cần lead_id không điền được từ việc
    assert "crm.set_stage" in {o["value"] for o in await q(admin, "flow.command_options", action="command", trigger="crm.lead_assigned")}
    proposable = {o["value"] for o in await q(admin, "flow.command_options", action="propose", trigger="crm.lead_assigned")}
    assert "crm.set_stage" in proposable and "crm.log_activity" in proposable   # đề xuất: lệnh về chính lead, người bấm mới chạy
    assert "flow.set_task" not in {o["value"] for o in await q(tech, "flow.command_options", action="command", date_source="flow.task_due")}
    r = await executor.run(tech, "flow.save_automation", {"name": "Thử", "date_source": "flow.task_due", "action": "command", "command": "flow.set_task"})
    assert r.status == "denied" and "không chạy được" in r.error                  # người đặt không chạy được lệnh ⇒ không có trong danh sách, không lưu được
    form = {f["key"]: f for f in field_specs(registry.OPS["flow.save_automation"])}   # form do server vẽ: chọn từ danh sách, không gõ mã
    assert form["trigger"]["options_query"] == "flow.trigger_options" and form["date_source"]["options_query"] == "flow.date_source_options"
    assert form["command"]["options_query"] == "flow.command_options" and list(form["command"]["options_with"]) == ["action", "trigger", "date_source"]
    assert {o["value"] for o in form["action"]["options"]} == {"notify", "task", "propose", "command"}


async def _tech(who: Who) -> Principal:
    local = f"cn{uuid.uuid4().hex[:8]}"
    from barista import seed

    await approved(await who("admin"), "admin.invite_user", email=f"{local}@{seed.DOMAIN}", name=f"Công nghệ {local}", role="tech")
    return await who(local)


async def test_dated_rule_fires_once_per_record_and_date_in_trial_then_for_real(who: Who, demo: dict[str, Any], rules: list[str]) -> None:
    admin, sale = await who("admin"), await fresh_sale(who)
    rid = await _rule(admin, rules, date_source="flow.task_due", direction="after", days=1, conditions=f"Người làm = {sale.name}",
                      action="notify", to_owner=True, message="Việc quá hạn 1 ngày ({Trạng thái})")
    await _owner_exec("UPDATE flow.automations SET created_at = now() - interval '3 days' WHERE id = %s", (rid,))   # luật đặt từ 3 ngày trước
    late, old = await _task(admin, sale, 2), await _task(admin, sale, 30)
    await _sweep(demo["tenant"], rid)
    runs = await _runs(rid)
    assert [(x["status"], x["ref"]) for x in runs] == [("trial", late)] and old not in {x["ref"] for x in runs}   # tới hạn TRƯỚC khi đặt luật ⇒ im
    assert "Sẽ báo người phụ trách" in runs[0]["note"] and "todo" in runs[0]["note"]
    await _sweep(demo["tenant"], rid)
    assert len(await _runs(rid)) == 1                                              # quét lại ⇒ không lần hai
    name = await owner_val("SELECT name FROM flow.automations WHERE id = %s", (rid,))
    assert await _notes(sale.user_id, name) == []                                  # chạy thử: chưa gửi
    await ok(admin, "flow.set_automation", automation_id=rid, trial=False)
    fresh = await _task(admin, sale, 3)
    await _sweep(demo["tenant"], rid)
    assert await _notes(sale.user_id, name) == ["Việc quá hạn 1 ngày (todo)"]
    await _sweep(demo["tenant"], rid)
    assert len(await _notes(sale.user_id, name)) == 1                              # một lần mỗi (bản ghi, ngày)
    await _owner_exec("UPDATE flow.tasks SET due_on = due_on + 1 WHERE id = %s", (fresh,))   # dời hạn ⇒ ngày mới ⇒ kích lại MỘT lần
    await _sweep(demo["tenant"], rid)
    await _sweep(demo["tenant"], rid)
    assert len(await _notes(sale.user_id, name)) == 2
    await ok(admin, "flow.set_task", task_id=fresh, status="done")
    await _owner_exec("UPDATE flow.tasks SET due_on = due_on - 2 WHERE id = %s", (fresh,))   # ngày mới trong khoảng — nhưng việc đã xong
    await _sweep(demo["tenant"], rid)
    assert len(await _notes(sale.user_id, name)) == 2                              # việc đã xong ⇒ nguồn không trả nữa
    page = await q(admin, "flow.automation", automation_id=rid)
    assert "1 ngày sau hạn việc" in page["blocks"][0]["title"]


async def test_daily_cap_skips_with_a_note(who: Who, demo: dict[str, Any], rules: list[str]) -> None:
    admin, sale = await who("admin"), await fresh_sale(who)
    rid = await _rule(admin, rules, date_source="flow.task_due", direction="on", conditions=f"Người làm = {sale.name}", action="notify",
                      to_owner=True, message="Hạn hôm nay", daily_cap=1)
    await _task(admin, sale, 0)
    await _task(admin, sale, 0)
    await _sweep(demo["tenant"], rid)
    assert sorted(x["status"] for x in await _runs(rid)) == ["skipped", "trial"]
    assert any("trần 1 lần" in x["note"] for x in await _runs(rid))


async def test_run_command_runs_as_the_owner_with_rights_reloaded(who: Who, demo: dict[str, Any], rules: list[str]) -> None:
    """Chạy lệnh: bằng quyền người đặt luật NẠP LẠI lúc chạy — người đó mất quyền ⇒ lệnh bị từ chối (ghi Lỗi), không chạy bằng quyền hệ thống."""
    admin, sale, tech = await who("admin"), await fresh_sale(who), await _tech(who)
    rid = await _rule(admin, rules, date_source="flow.task_due", direction="after", days=1, conditions=f"Người làm = {sale.name}",
                      action="command", command="flow.set_task", params="Trạng thái = cancelled", message="Huỷ việc quá hạn")
    await _owner_exec("UPDATE flow.automations SET created_at = now() - interval '5 days' WHERE id = %s", (rid,))
    first = await _task(admin, sale, 2)
    await _sweep(demo["tenant"], rid)
    trial = (await _runs(rid))[0]
    assert trial["status"] == "trial" and "Đổi trạng thái việc" in trial["note"]   # chạy thử THẬT rồi bỏ: việc chưa đổi
    assert await owner_val("SELECT status FROM flow.tasks WHERE id = %s", (first,)) == "todo"
    await ok(admin, "flow.set_automation", automation_id=rid, trial=False)
    # người đặt luật đổi sang một người còn quyền quản lý luật nhưng KHÔNG có quyền việc (vai Công nghệ) — như khi vai của họ bị đổi
    await _owner_exec("UPDATE flow.automations SET owner_id = %s WHERE id = %s", (tech.user_id, rid))
    second = await _task(admin, sale, 3)
    await _sweep(demo["tenant"], rid)
    run = next(x for x in await _runs(rid) if x["ref"] == second)
    assert run["status"] == "failed" and await owner_val("SELECT status FROM flow.tasks WHERE id = %s", (second,)) == "todo"
    await _owner_exec("UPDATE flow.automations SET owner_id = %s WHERE id = %s", (admin.user_id, rid))
    third = await _task(admin, sale, 4)
    await _sweep(demo["tenant"], rid)
    assert next(x for x in await _runs(rid) if x["ref"] == third)["status"] == "done"
    assert await owner_val("SELECT status FROM flow.tasks WHERE id = %s", (third,)) == "cancelled"
    assert await owner_val("SELECT trace FROM core.audit_log WHERE command = 'flow.set_task' AND input->>'task_id' = %s AND status = 'ok'",
                           (third,)) == f"auto:{rid}"   # nhật ký thao tác mang mã vết của luật


async def test_propose_files_a_card_for_the_lead_owner_on_assignment(who: Who, rules: list[str]) -> None:
    admin, sale = await who("admin"), await fresh_sale(who, "KD-A")
    await _rule(admin, rules, trigger="crm.lead_assigned", conditions=f"Người phụ trách = {sale.name}", action="propose", command="crm.set_stage",
                params="Chặng = interested", to_owner=True, message="Lead mới giao — gọi chưa?", trial=False)
    lead = await ok(admin, "crm.create_lead", name="Khách đề xuất", phone=fresh_phone())
    await events.drain()
    await ok(admin, "crm.assign", lead_id=str(lead["lead_id"]), owner_email=sale.email)
    await events.drain()
    card = await owner_val("""SELECT json_build_object('command', command, 'input', input, 'reason', reason) FROM core.proposals
                              WHERE user_id = %s AND key LIKE 'auto:%%'""", (sale.user_id,))
    assert card["command"] == "crm.set_stage" and card["input"]["lead_id"] == str(lead["lead_id"]) and card["input"]["stage"] == "interested"
    assert "Lead mới giao" in card["reason"]
    assert await owner_val("SELECT stage FROM crm.leads WHERE id = %s", (lead["lead_id"],)) == "lead"   # chưa chạy — người bấm mới chạy


async def test_stage_change_notifies_the_owner_and_a_rule_loop_stops(who: Who, rules: list[str]) -> None:
    """Hai luật kích nhau (Quan tâm ⇒ Đã gặp ⇒ Quan tâm…) dừng sau ``MAX_CHAIN`` tầng, có ghi — không vòng lặp vô hạn."""
    admin, sale = await who("admin"), await fresh_sale(who, "KD-A")
    lead = await ok(sale, "crm.create_lead", name="Khách vòng lặp", phone=fresh_phone())
    await events.drain()
    who_ = f"Người phụ trách = {sale.name}"
    note = await _rule(admin, rules, trigger="crm.lead_stage_changed", conditions=f"{who_}\nChặng = interested", action="notify", to_owner=True,
                       message="Lead sang {Chặng}", trial=False)
    a = await _rule(admin, rules, trigger="crm.lead_stage_changed", conditions=f"{who_}\nChặng = interested", action="command", command="crm.set_stage",
                    params="Chặng = met", trial=False)
    b = await _rule(admin, rules, trigger="crm.lead_stage_changed", conditions=f"{who_}\nChặng = met", action="command", command="crm.set_stage",
                    params="Chặng = interested", trial=False)
    await ok(sale, "crm.set_stage", lead_id=str(lead["lead_id"]), stage="interested")
    await events.drain()
    name = await owner_val("SELECT name FROM flow.automations WHERE id = %s", (note,))
    assert "Lead sang interested" in await _notes(sale.user_id, name)
    stopped = [x for x in await _runs(a) + await _runs(b) if x["status"] == "skipped"]
    assert stopped and "chuỗi luật" in stopped[0]["note"]
    changes = await owner_val("SELECT count(*) FROM crm.stage_events WHERE lead_id = %s AND kind = 'pipeline'", (lead["lead_id"],))
    assert changes <= auto.MAX_CHAIN + 1
