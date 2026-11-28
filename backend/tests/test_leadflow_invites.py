"""Mời nhiều người — ai nhận trước giữ (ADR 0264): bậc có ``invite_n`` > 1 ⇒ lead CHƯA có người giữ, người được mời thấy lead (chính sách đọc riêng), người
đầu tiên bấm nhận (hoặc ghi chăm sóc / đổi chặng) thành người giữ qua đúng đường ghi duy nhất; người kia hết thấy; người phụ trách đặt đúng một lần; hai
người bấm cùng lúc chỉ một người thắng; hết hạn lời mời không ai nhận ⇒ lượt kế trong bậc / bậc kế. Bất biến một lead một người giữ giữ nguyên.

Mỗi test dựng MỘT công ty riêng."""

import asyncio
from uuid import UUID

from barista.kernel import executor
from barista.kernel.principal import Principal
from tests.conftest import ok, owner_val, q
from tests.test_leadflow_plus import _exec, _intake, _kinds, _state, _sweep
from tests.test_leadflow_steps import OFF, _owner, _sees, _stepped_company

INVITE = {"after_days": 2, "invite_n": 2, "invite_min": 30}


async def _invited(pp: dict[str, Principal]) -> UUID:
    out = await _intake(pp["admin"], offering_code=OFF)
    assert out["assigned_to"] is None
    lead: UUID = out["lead_id"]
    assert await _state(lead, "status") == "queued" and await _state(lead, "queue_reason") == "invited"
    return lead


async def _owner_set_count(lead: UUID) -> int:
    n: int = await owner_val("SELECT count(*) FROM crm.stage_events WHERE lead_id = %s AND kind = 'assigned' AND data ->> 'to' IS NOT NULL", (lead,))
    return n


async def _holders_match_owners(tid: UUID) -> int:
    n: int = await owner_val("""SELECT count(*) FROM leadflow.lead_state s JOIN crm.leads l ON l.id = s.lead_id
                                WHERE s.tenant_id = %s AND s.status IN ('offered', 'waiting', 'contacted', 'queued')
                                  AND s.holder_id IS DISTINCT FROM l.owner_id""", (tid,))
    return n


async def test_two_people_invited_the_first_to_claim_holds_and_the_other_stops_seeing_the_lead() -> None:
    tid, pp, _ = await _stepped_company(INVITE, {"after_days": 0}, members=(("a", 1), ("b", 1), ("c", 2)))
    lead = await _invited(pp)
    assert await _owner(lead) is None and await _state(lead, "holder_id") is None                # chưa ai là người giữ
    assert await _sees(pp["a"], lead) and await _sees(pp["b"], lead) and not await _sees(pp["c"], lead)
    mine = await q(pp["a"], "leadflow.mine")
    assert any(a.get("command") == "leadflow.claim" for b in mine["blocks"] for a in (b.get("actions") or []))
    card = await q(pp["b"], "crm.journey", lead_id=str(lead))
    assert any(a.get("command") == "leadflow.claim" for b in card["blocks"] for a in (b.get("actions") or []))
    got = await ok(pp["b"], "leadflow.claim", lead_id=str(lead))
    assert UUID(str(got["assigned_to"])) == pp["b"].user_id
    assert await _owner(lead) == pp["b"].user_id == await _state(lead, "holder_id")
    assert await _state(lead, "status") == "waiting" and await _state(lead, "accepted_at") is not None   # nhận lời mời = đã cam kết
    assert not await _sees(pp["a"], lead)                                                         # người kia hết thấy
    late = await executor.run(pp["a"], "leadflow.claim", {"lead_id": str(lead)})
    assert late.status in ("conflict", "not_found")
    assert await _owner_set_count(lead) == 1                                                      # người phụ trách đặt đúng một lần
    assert "invited" in await _kinds(lead) and "claimed" in await _kinds(lead)
    assert await _holders_match_owners(tid) == 0


async def test_two_people_claiming_at_the_same_time_only_one_wins() -> None:
    tid, pp, _ = await _stepped_company(INVITE, members=(("a", 1), ("b", 1)))
    for _ in range(3):
        lead = await _invited(pp)
        runs = await asyncio.wait_for(asyncio.gather(executor.run(pp["a"], "leadflow.claim", {"lead_id": str(lead)}),
                                                     executor.run(pp["b"], "leadflow.claim", {"lead_id": str(lead)})), 20)
        assert sorted(r.status for r in runs) == ["conflict", "ok"], [(r.status, r.error) for r in runs]
        winner = next(p for p, r in zip((pp["a"], pp["b"]), runs, strict=True) if r.status == "ok")
        assert await _owner(lead) == winner.user_id and await _owner_set_count(lead) == 1
        assert await owner_val("SELECT count(*) FROM leadflow.invites WHERE lead_id = %s AND status = 'claimed'", (lead,)) == 1
    assert await _holders_match_owners(tid) == 0


async def test_logging_care_first_is_claiming() -> None:
    _tid, pp, _ = await _stepped_company(INVITE, members=(("a", 1), ("b", 1)))
    lead = await _invited(pp)
    await ok(pp["a"], "crm.log_activity", lead_id=str(lead), kind="call", outcome="connected")
    assert await _owner(lead) == pp["a"].user_id == await _state(lead, "holder_id")
    assert await owner_val("SELECT last_activity_at IS NOT NULL FROM crm.leads WHERE id = %s", (lead,))   # chăm sóc ghi lên lead của người vừa nhận
    assert not await _sees(pp["b"], lead)
    other = await _invited(pp)
    await ok(pp["b"], "crm.set_stage", lead_id=str(other), stage="prospect")                     # đổi chặng trước cũng là nhận
    assert await _owner(other) == pp["b"].user_id


async def test_an_unanswered_invite_goes_to_the_next_round_then_the_next_step() -> None:
    tid, pp, _ = await _stepped_company(INVITE, {"after_days": 0}, members=(("a", 1), ("b", 1), ("truc", 1), ("c", 2)))
    lead = await _invited(pp)
    first = {UUID(u) for u in (await owner_val("SELECT data FROM leadflow.sla_events WHERE lead_id = %s AND kind = 'invited'", (lead,)))["users"]}
    assert len(first) == 2
    assert (await _sweep(tid))["invites"] == 0                                                    # còn hạn ⇒ chờ
    await _exec("UPDATE leadflow.invites SET until = now() - interval '1 minute' WHERE lead_id = %s", (lead,))
    assert (await _sweep(tid))["invites"] == 1
    third = ({pp["a"].user_id, pp["b"].user_id, pp["truc"].user_id} - first).pop()
    assert await _state(lead, "holder_id") == third                                               # lượt kế: người duy nhất chưa được mời ⇒ giao thẳng
    assert await _state(lead, "step_seq") == 1
    # lead thứ hai: lượt 1 mời hai người, lượt 2 còn MỘT ⇒ giao; hết người ở bậc 1 ⇒ bậc 2
    lead2 = await _invited(pp)
    for _ in range(2):
        await _exec("UPDATE leadflow.invites SET until = now() - interval '1 minute' WHERE lead_id = %s AND status = 'open'", (lead2,))
        await _exec("UPDATE leadflow.lead_state SET hold_until = now() - interval '1 minute' WHERE lead_id = %s AND status <> 'queued'", (lead2,))
        await _sweep(tid)
    assert await _state(lead2, "step_seq") == 2 and await _state(lead2, "holder_id") == pp["c"].user_id
    assert await _holders_match_owners(tid) == 0


async def test_an_agent_cannot_claim_on_its_own() -> None:
    _tid, pp, _ = await _stepped_company(INVITE, members=(("a", 1), ("b", 1)))
    lead = await _invited(pp)
    r = await executor.run(pp["a"].as_agent(), "leadflow.claim", {"lead_id": str(lead)})
    assert r.status == "proposed", (r.status, r.error)                                            # thẻ đề xuất — người bấm mới nhận
    assert await _owner(lead) is None
