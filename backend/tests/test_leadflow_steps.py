"""Chia lead theo BẬC (ADR 0264): bậc + hạn giữ là dữ liệu của luật. Hết hạn giữ mà lead chưa chốt ⇒ chuyển giao sang bậc kế (không tính thu hồi, không lùi
bậc); lead đã liên hệ chỉ bị đẩy khi bậc khai "đẩy cả lead đã liên hệ"; lead đỗ (nạp từ hệ cũ) đứng ngoài; bậc kế hết người / đã qua bậc cuối ⇒ hàng chờ có
chủ; sale trả lead ⇒ chia lại ở bậc hiện tại, người trả không nhận lại. Luật không khai bậc ⇒ như cũ.

Mỗi test dựng MỘT công ty riêng."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from barista import seed
from barista.kernel import executor
from barista.kernel.clock import VN
from barista.kernel.principal import Principal
from tests.conftest import ok, owner_val
from tests.test_leadflow_plus import _company, _exec, _intake, _kinds, _rule, _state, _sweep

OFF = "STP"


async def _steps(admin: Principal, rule_id: Any, *steps: dict[str, Any]) -> None:
    for i, s in enumerate(steps, 1):
        await ok(admin, "leadflow.set_step", rule_id=str(rule_id), seq=i, **{"label": f"Bậc {i}", "day_kind": "calendar", **s})


async def _stepped_company(*steps: dict[str, Any], members: tuple[tuple[str, int | None], ...] = (("a", 1), ("b", 2), ("c", 3)),
                           **rule: Any) -> tuple[UUID, dict[str, Principal], UUID]:
    tid, _, pp = await _company(seed.ALL_PACKS)
    for who, step in members:
        await ok(pp["admin"], "leadflow.set_member", offering_code=OFF, user_email=pp[who].email, step=step)
    out = await _rule(pp["admin"], offering_code=OFF, receivers="members", **rule)
    await _steps(pp["admin"], out["rule_id"], *steps)
    return tid, pp, out["rule_id"]


async def _expire_hold(lead: UUID) -> None:
    await _exec("UPDATE leadflow.lead_state SET hold_until = now() - interval '1 minute' WHERE lead_id = %s", (lead,))


async def _owner(lead: UUID) -> Any:
    return await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (lead,))


async def _sees(p: Principal, lead: UUID) -> bool:
    return (await executor.query(p, "crm.journey", {"lead_id": str(lead)})).status == "ok"


async def test_a_lead_steps_up_when_its_hold_ends_without_counting_a_recall_and_never_steps_back() -> None:
    tid, pp, _ = await _stepped_company({"after_days": 1}, {"after_days": 1}, {"after_days": 0})
    lead = (x := await _intake(pp["admin"], offering_code=OFF))["lead_id"]
    assert x["assigned_to"] == pp["a"].user_id                                                    # bậc 1: chỉ thành viên bậc 1
    assert await _state(lead, "step_seq") == 1
    hold = await _state(lead, "hold_until")
    assert timedelta(hours=23) < hold - datetime.now(VN) <= timedelta(days=1)                     # tính lúc nhận: 1 ngày lịch
    assert (await _sweep(tid))["stepped"] == 0                                                    # chưa hết hạn giữ ⇒ không đụng
    await _expire_hold(lead)
    assert (await _sweep(tid))["stepped"] == 1
    assert await _state(lead, "holder_id") == pp["b"].user_id == await _owner(lead)               # bậc 2, một nguồn người giữ
    assert await _state(lead, "step_seq") == 2 and await _state(lead, "recalls") == 0             # chuyển giao, không phải thu hồi
    assert "stepped" in await _kinds(lead) and "recalled" not in await _kinds(lead)
    assert not await _sees(pp["a"], lead)                                                         # người bậc trước hết thấy khách
    await _expire_hold(lead)
    await _sweep(tid)
    assert await _state(lead, "holder_id") == pp["c"].user_id and await _state(lead, "step_seq") == 3
    assert await _state(lead, "hold_until") is None                                               # bậc cuối không hạn giữ
    assert (await _sweep(tid))["stepped"] == 0 and await _state(lead, "step_seq") == 3            # không lùi, không đi tiếp


async def test_contacted_leads_step_up_only_when_the_step_includes_them() -> None:
    tid, pp, _ = await _stepped_company({"after_days": 2, "include_contacted": False}, {"after_days": 2, "include_contacted": True},
                                        {"after_days": 0})
    lead = (await _intake(pp["admin"], offering_code=OFF))["lead_id"]
    await ok(pp["a"], "crm.log_activity", lead_id=lead, kind="call", outcome="connected")
    await _sweep(tid)
    assert await _state(lead, "status") == "contacted"
    await _expire_hold(lead)
    assert (await _sweep(tid))["stepped"] == 0 and await _state(lead, "holder_id") == pp["a"].user_id   # bậc 1 giữ lead đã liên hệ
    # lead CHƯA liên hệ thì luôn bị đẩy khi hết hạn giữ, kể cả bậc không đẩy lead đã liên hệ
    fresh = (await _intake(pp["admin"], offering_code=OFF))["lead_id"]
    await _expire_hold(fresh)
    await _sweep(tid)
    assert await _state(fresh, "holder_id") == pp["b"].user_id and await _state(fresh, "step_seq") == 2
    await ok(pp["b"], "crm.log_activity", lead_id=fresh, kind="call", outcome="connected")
    await _sweep(tid)
    assert await _state(fresh, "status") == "contacted"
    await _expire_hold(fresh)
    assert (await _sweep(tid))["stepped"] == 1 and await _state(fresh, "holder_id") == pp["c"].user_id   # bậc 2 đẩy cả lead đã liên hệ


async def test_parked_leads_stay_out_of_the_step_sweep() -> None:
    tid, pp, _ = await _stepped_company({"after_days": 1}, {"after_days": 0})
    lead = (await _intake(pp["admin"], offering_code=OFF))["lead_id"]
    await _exec("UPDATE leadflow.lead_state SET imported_at = now(), hold_until = now() - interval '1 day' WHERE lead_id = %s", (lead,))
    assert (await _sweep(tid))["stepped"] == 0
    assert await _state(lead, "holder_id") == pp["a"].user_id and await _state(lead, "step_seq") == 1


async def test_a_step_with_no_one_left_and_the_end_of_the_steps_go_to_an_owned_queue() -> None:
    tid, pp, _ = await _stepped_company({"after_days": 1}, {"after_days": 1}, members=(("a", 1), ("b", 2)))
    lead = (await _intake(pp["admin"], offering_code=OFF))["lead_id"]
    await ok(pp["b"], "leadflow.pause", until=(datetime.now(VN).date() + timedelta(days=1)).isoformat())
    await _expire_hold(lead)
    await _sweep(tid)
    assert await _state(lead, "status") == "queued" and await _state(lead, "queue_reason") == "no_eligible"   # bậc 2 hết người: hàng chờ, không ngõ cụt
    assert await _state(lead, "step_seq") == 2 and await _owner(lead) is None
    await ok(pp["b"], "leadflow.pause")                                                           # b nhận lại ⇒ vòng quét chia lại ĐÚNG bậc 2
    await _sweep(tid)
    assert await _state(lead, "holder_id") == pp["b"].user_id and await _state(lead, "step_seq") == 2
    await _expire_hold(lead)
    await _sweep(tid)
    assert await _state(lead, "status") == "queued" and await _state(lead, "queue_reason") == "steps_done"    # đã qua bậc cuối
    await _exec("UPDATE leadflow.lead_state SET queued_at = now() - interval '1 day' WHERE lead_id = %s", (lead,))
    assert (await _sweep(tid))["queue_escalated"] == 1                                            # hàng chờ có chủ: báo người sở hữu
    assert pp["a"].user_id in await _state(lead, "holders") and pp["b"].user_id in await _state(lead, "holders")


async def test_a_holder_releases_a_lead_and_it_goes_to_someone_else_at_the_same_step() -> None:
    tid, pp, _ = await _stepped_company({"after_days": 3}, {"after_days": 0}, members=(("a", 1), ("b", 1), ("c", 2)))
    out = await _intake(pp["admin"], offering_code=OFF)
    lead, first = out["lead_id"], out["assigned_to"]
    holder = next(p for p in pp.values() if p.user_id == first)
    other = pp["b"] if holder is pp["a"] else pp["a"]
    assert (await executor.run(other, "leadflow.release", {"lead_id": str(lead), "reason": "không phải của tôi"})).status in ("not_found", "denied")
    assert (await executor.run(holder, "leadflow.release", {"lead_id": str(lead), "reason": "  "})).status == "invalid"
    got = await ok(holder, "leadflow.release", lead_id=str(lead), reason="Khách cần khu vực khác")
    assert UUID(str(got["assigned_to"])) == other.user_id == await _owner(lead)                   # cùng bậc, người chưa giữ
    assert await _state(lead, "step_seq") == 1 and await _state(lead, "recalls") == 0
    ev = await owner_val("SELECT data FROM leadflow.sla_events WHERE lead_id = %s AND kind = 'released'", (lead,))
    assert ev["why"] == "Khách cần khu vực khác"
    assert not await _sees(holder, lead)
    await ok(other, "leadflow.release", lead_id=str(lead), reason="Cũng không theo được")
    assert await _state(lead, "status") == "queued" and await _state(lead, "step_seq") == 1        # bậc 1 hết người chưa giữ ⇒ hàng chờ, không nhảy bậc
    assert (await _sweep(tid))["stepped"] == 0


async def test_steps_are_rule_data_set_by_managers_and_frozen_once_the_rule_has_routed() -> None:
    tid, _, pp = await _company(seed.ALL_PACKS)
    rule = (await _rule(pp["admin"], offering_code=OFF, receivers="members"))["rule_id"]
    assert (await executor.run(pp["a"], "leadflow.set_step", {"rule_id": str(rule), "seq": 1, "label": "Bậc 1"})).status == "denied"
    assert (await executor.run(pp["admin"], "leadflow.set_step", {"rule_id": str(rule), "seq": 2, "label": "Bậc 2"})).status == "invalid"   # liền nhau
    got = await ok(pp["admin"], "leadflow.set_step", rule_id=str(rule), seq=1, label="Bậc 1", after_days=2, invite_n=2)
    assert "Bậc 1" in got["summary"] and "mời 2 người" in got["summary"]
    policy = await executor.query(pp["admin"], "leadflow.policy", {})
    assert "chia theo 1 bậc" in str(policy.output)
    await ok(pp["admin"], "leadflow.set_member", offering_code=OFF, user_email=pp["a"].email)
    await _intake(pp["admin"], offering_code=OFF)
    assert (await executor.run(pp["admin"], "leadflow.set_step", {"rule_id": str(rule), "seq": 1, "label": "Đổi"})).status == "conflict"
    members = (await executor.query(pp["admin"], "leadflow.members", {"offering_code": OFF})).output
    assert {c["key"] for c in members["columns"]} >= {"step", "team"}
    assert tid


async def test_a_rule_without_steps_routes_exactly_as_before() -> None:
    tid, _, pp = await _company(seed.ALL_PACKS)
    await _rule(pp["admin"], team_code="KD-A")
    lead = (await _intake(pp["admin"]))["lead_id"]
    assert await _state(lead, "step_seq") is None and await _state(lead, "hold_until") is None
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (lead,))
    out = await _sweep(tid)
    assert out["recalled"] == 1 and out["stepped"] == 0 and await _state(lead, "recalls") == 1


async def test_max_open_caps_leads_held_but_not_closed() -> None:
    _tid, _, pp = await _company(seed.ALL_PACKS)
    await ok(pp["admin"], "leadflow.set_member", offering_code=OFF, user_email=pp["a"].email)
    await _rule(pp["admin"], offering_code=OFF, receivers="members", max_open=1)
    first = await _intake(pp["admin"], offering_code=OFF)
    assert first["assigned_to"] == pp["a"].user_id
    await ok(pp["a"], "crm.log_activity", lead_id=first["lead_id"], kind="call", outcome="connected")   # đã liên hệ nhưng CHƯA chốt
    second = await _intake(pp["admin"], offering_code=OFF)
    assert second["assigned_to"] is None and await _state(second["lead_id"], "queue_reason") == "cap"
