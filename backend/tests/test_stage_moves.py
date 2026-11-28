"""Chặn chuyển chặng bừa (ADR 0264): công ty khai cặp chặng được phép (``crm.stage_moves`` — từ → sang + vai). Không khai cặp nào ⇒ như cũ; khai ⇒
``crm.set_stage`` chỉ cho cặp đã khai, đúng vai, câu từ chối nói rõ chuyển được sang đâu; bước tự chuyển chặng khi liên hệ lần đầu cũng theo ma trận (không
làm hỏng việc ghi chăm sóc). Mỗi test dựng MỘT công ty riêng."""

from barista import seed
from barista.kernel import executor
from tests.conftest import ok, owner_val, q
from tests.test_leadflow_plus import _company, _intake, _rule


async def _pipeline(lead: object) -> str:
    out: str = await owner_val("SELECT pipeline_stage FROM crm.leads WHERE id = %s", (lead,))
    return out


async def test_without_declared_moves_every_stage_change_works_as_before() -> None:
    _tid, _, pp = await _company(seed.ALL_PACKS)
    await _rule(pp["admin"])
    lead = (await _intake(pp["admin"], owner_email=pp["a"].email))["lead_id"]
    await ok(pp["a"], "crm.set_stage", lead_id=str(lead), stage="negotiating")
    assert await _pipeline(lead) == "negotiating"
    assert (await q(pp["admin"], "crm.stage_moves"))["rows"] == []


async def test_declared_moves_allow_only_listed_pairs_for_the_listed_roles() -> None:
    _tid, _, pp = await _company(seed.ALL_PACKS)
    await _rule(pp["admin"])
    lead = (await _intake(pp["admin"], owner_email=pp["a"].email))["lead_id"]
    assert (await executor.run(pp["a"], "crm.set_stage_move", {"from_stage": "new", "to_stage": "contacting"})).status == "denied"
    await ok(pp["admin"], "crm.set_stage_move", from_stage="new", to_stage="contacting", roles=["sale"])
    await ok(pp["admin"], "crm.set_stage_move", from_stage="contacting", to_stage="interested")
    await ok(pp["admin"], "crm.set_stage_move", from_stage="contacting", to_stage="lost")
    bad = await executor.run(pp["a"], "crm.set_stage", {"lead_id": str(lead), "stage": "interested"})
    assert bad.status == "invalid" and "Chưa được chuyển" in (bad.error or "") and "Đang liên hệ" in (bad.error or "")   # gợi ý cặp hợp lệ
    assert await _pipeline(lead) == "new"
    await ok(pp["a"], "crm.set_stage", lead_id=str(lead), stage="contacting")
    await ok(pp["a"], "crm.set_stage", lead_id=str(lead), stage="interested")
    assert await _pipeline(lead) == "interested"
    # vai không có trong cặp ⇒ không chuyển được, kể cả quản lý
    other = (await _intake(pp["admin"], owner_email=pp["b"].email))["lead_id"]
    r = await executor.run(pp["truong"], "crm.set_stage", {"lead_id": str(other), "stage": "contacting"})
    assert r.status == "invalid", (r.status, r.error)
    # bỏ hết cặp ⇒ tự do như cũ
    for frm, to in (("new", "contacting"), ("contacting", "interested"), ("contacting", "lost")):
        await ok(pp["admin"], "crm.set_stage_move", from_stage=frm, to_stage=to, active=False)
    await ok(pp["truong"], "crm.set_stage", lead_id=str(other), stage="negotiating")
    assert len((await q(pp["admin"], "crm.stage_moves"))["rows"]) == 3


async def test_the_first_contact_auto_move_follows_the_matrix_and_never_breaks_logging() -> None:
    _tid, _, pp = await _company(seed.ALL_PACKS)
    await _rule(pp["admin"])
    lead = (await _intake(pp["admin"], owner_email=pp["a"].email))["lead_id"]
    await ok(pp["admin"], "crm.set_stage_move", from_stage="new", to_stage="interested")   # có khai, nhưng không có new → contacting
    got = await ok(pp["a"], "crm.log_activity", lead_id=str(lead), kind="call", outcome="connected")
    assert "stage_moved_to" not in got and await _pipeline(lead) == "new"                         # không tự chuyển, chăm sóc vẫn ghi
    await ok(pp["admin"], "crm.set_stage_move", from_stage="new", to_stage="contacting")
    lead2 = (await _intake(pp["admin"], owner_email=pp["a"].email))["lead_id"]
    got = await ok(pp["a"], "crm.log_activity", lead_id=str(lead2), kind="call", outcome="connected")
    assert got.get("stage_moved_to") and await _pipeline(lead2) == "contacting"
