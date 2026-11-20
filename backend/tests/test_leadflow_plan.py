"""Chia lead theo KẾ HOẠCH chiến dịch (0204): luật của một kế hoạch thắng luật theo [[offering]] / nguồn; chỉ người THAM GIA kế hoạch nhận lead của nó; lead
của kế hoạch không bao giờ rơi sang luật chung (kể cả khi không còn ai nhận — nằm hàng chờ, vòng quét chia khi có người); kế hoạch ghi vào trạng thái lead."""

from datetime import timedelta

from barista import seed
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.principal import system_principal
from tests.conftest import ok, owner_val
from tests.test_leadflow_plus import _company, _intake, _rule, _state, _sweep

PLAN = "CPPLANTEST01"


async def test_plan_leads_go_only_to_the_plan_participants_and_never_fall_to_the_general_rule() -> None:
    tid, _, pp = await _company(seed.ALL_PACKS)
    code = f"PLC{tid.hex[:6].upper()}"
    await ok(system_principal(tid, pp["admin"].user_id), "mkt.upsert_campaign", code=code, name="Chiến dịch gói đồng chi trả", channel="facebook", offering_code="MEM", plan_code=PLAN)
    for who in ("a", "c"):   # a ở đội KD-A, c ở đội KD-B: người tham gia không cần cùng đội
        await ok(pp["admin"], "leadflow.set_member", offering_code="MEM", user_email=pp[who].email, plan_code=PLAN.lower())
    await _rule(pp["admin"], offering_code="MEM", team_code="KD-A")                                   # luật chung: đội KD-A (a, b)
    await _rule(pp["admin"], name="Gói đồng chi trả", plan_code=PLAN, receivers="members")            # luật của kế hoạch

    plan_leads = [await _intake(pp["admin"], offering_code="MEM", source="ads", campaign_code=code) for _ in range(4)]
    assert {x["assigned_to"] for x in plan_leads} == {pp["a"].user_id, pp["c"].user_id}               # chỉ người tham gia, xoay đều
    assert await _state(plan_leads[0]["lead_id"], "plan_code") == PLAN
    general = [await _intake(pp["admin"], offering_code="MEM", source="ads") for _ in range(4)]
    assert {x["assigned_to"] for x in general} <= {pp["a"].user_id, pp["b"].user_id, pp["truong"].user_id}   # luật chung, không phải c
    assert pp["c"].user_id not in {x["assigned_to"] for x in general}
    assert await _state(general[0]["lead_id"], "plan_code") == ""

    for who in ("a", "c"):   # cả hai người tham gia tạm ngừng ⇒ lead của kế hoạch CHỜ, không sang b (luật chung)
        await ok(pp[who], "leadflow.pause", until=(today() + timedelta(days=1)).isoformat())
    stuck = await _intake(pp["admin"], offering_code="MEM", source="ads", campaign_code=code)
    assert stuck["assigned_to"] is None and await _state(stuck["lead_id"], "status") == "queued"
    await ok(pp["c"], "leadflow.pause", until=(today() - timedelta(days=1)).isoformat())
    await _sweep(tid)
    assert await _state(stuck["lead_id"], "holder_id") == pp["c"].user_id                             # vòng quét chia lại theo đúng luật của kế hoạch


async def test_plan_code_is_checked_and_a_plan_rule_does_not_clash_with_the_general_rule_of_the_same_day() -> None:
    _tid, _, pp = await _company(seed.ALL_PACKS)
    await _rule(pp["admin"], offering_code="MEM")
    await _rule(pp["admin"], offering_code="MEM", name="Cùng ngày, của kế hoạch", plan_code=PLAN)    # khoá duy nhất có cột kế hoạch
    bad = await executor.run(pp["admin"], "leadflow.set_rule", {"name": "Sai mã", "plan_code": "có dấu cách", "effective_from": today().isoformat()})
    assert bad.status == "invalid"
    assert await owner_val("SELECT count(*) FROM leadflow.rules WHERE tenant_id = %s AND plan_code = %s", (pp["admin"].tenant_id, PLAN)) == 1
