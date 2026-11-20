"""Trợ lý (agent) làm thay người: quyền KHÔNG BAO GIỜ hơn người; lệnh ghi mặc định chỉ thành đề xuất; duyệt chỉ do người."""

from barista.kernel import executor, registry
from tests.conftest import Who, fresh_phone, ok, owner_val, q


async def test_agent_sees_exactly_the_same_rows_as_the_person(who: Who) -> None:
    for local in ("sale1", "truonga", "giamdoc"):
        human = await who(local)
        a = await executor.query(human.as_agent(), "crm.leads", {"stage": ""})
        h = await q(human, "crm.leads", stage="")
        assert a.status == "ok" and [r["id"] for r in a.output["rows"]] == [r["id"] for r in h["rows"]], local


async def test_agent_can_never_exceed_the_person(who: Who) -> None:
    """Mọi thao tác người KHÔNG được làm thì agent của người đó cũng bị chặn (đi qua đúng executor)."""
    for local in ("sale1", "mkt", "ketoan"):
        human = await who(local)
        agent = human.as_agent()
        for op in registry.OPS.values():
            if op.internal or executor.allowed(human, op):
                continue
            if op.kind == "query":
                r = await executor.query(agent, op.name, {})
            else:
                r = await executor.run(agent, op.name, {})
            assert r.status in ("denied", "not_found", "invalid"), (local, op.name, r.status)
            assert r.status != "invalid" or not executor.allowed(human, op)


async def test_agent_write_becomes_proposal_and_writes_nothing(who: Who) -> None:
    sale1 = await who("sale1")
    phone = fresh_phone()
    r = await executor.run(sale1.as_agent(), "crm.create_lead", {"name": "Khách agent soạn", "phone": phone})
    assert r.status == "proposed"
    assert r.output["command"] == "crm.create_lead" and r.output["input"]["phone"] == phone
    assert await owner_val("SELECT count(*) FROM crm.customers WHERE phone = %s", (phone,)) == 0
    assert await owner_val("SELECT via FROM core.audit_log WHERE command = 'crm.create_lead' AND status = 'proposed'"
                           " ORDER BY at DESC LIMIT 1") == "agent"
    # người bấm xác nhận = chạy lại đúng input đó bằng quyền người
    await ok(sale1, "crm.create_lead", **r.output["input"])
    assert await owner_val("SELECT count(*) FROM crm.customers WHERE phone = %s", (phone,)) == 1


async def test_low_risk_write_allowed_for_agent(who: Who) -> None:
    sale1 = await who("sale1")
    lead_id = (await q(sale1, "crm.leads"))["rows"][0]["id"]
    r = await executor.run(sale1.as_agent(), "crm.log_activity", {"lead_id": lead_id, "kind": "note", "note": "agent ghi chú"})
    assert r.status == "ok"
