"""CHỜ TÔI DUYỆT (ADR 0253): từng việc (đề xuất + phiếu duyệt) nhóm theo loại; duyệt lô cùng một lệnh, mỗi thẻ vẫn qua đường quyết của nó. Postgres thật."""

from datetime import timedelta
from uuid import UUID

import pytest
from psycopg.types.json import Jsonb

from barista import seed
from barista.agent import decisions
from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.errors import Invalid
from tests.conftest import Who, edge, owner_val
from tests.test_admin_owner_approval import PEOPLE, _company


async def _file(p, key: str, title: str, command: str = "flow.create_task", inp: dict | None = None) -> UUID:
    inp = inp or {"title": title, "due_on": (today() + timedelta(days=2)).isoformat()}
    async with db.tenant_tx(p) as conn:
        cur = await conn.execute(
            "INSERT INTO core.proposals (user_id, day, key, command, title, reason, input) VALUES (core.me(), %s, %s, %s, %s, 'thử', %s) RETURNING id",
            (today(), key, command, title, Jsonb(inp)))
        row = await cur.fetchone()
    assert row is not None
    return row["id"]


async def test_waiting_lists_each_job_grouped_and_batch_runs_every_card_once(who: Who) -> None:
    sale = await who("sale1")
    await owner_val("DELETE FROM core.proposals WHERE user_id = %s RETURNING 1", (sale.user_id,))
    titles = [f"Việc lô {i} (test duyệt lô)" for i in range(3)]
    ids = [await _file(sale, f"batch-{i}", t) for i, t in enumerate(titles)]
    items = [x for x in await decisions.waiting(sale) if x["id"] in {str(i) for i in ids}]
    assert len(items) == 3 and {x["group"] for x in items} == {"proposal:flow.create_task"} and all(x["batchable"] for x in items)
    assert await decisions.count(sale) >= 3
    res = await decisions.decide_many(sale, "proposal", [str(i) for i in ids], "confirm")
    assert [r["status"] for r in res] == ["ok", "ok", "ok"]
    for t in titles:
        assert await owner_val("SELECT count(*) FROM flow.tasks WHERE title = %s", (t,)) == 1       # mỗi thẻ đúng MỘT việc
    with pytest.raises(Invalid):
        await decisions.decide_many(sale, "proposal", [str(ids[0])], "confirm")                         # đã quyết ⇒ không còn chờ


async def test_batch_refuses_mixed_commands_and_money_or_authority_commands(who: Who) -> None:
    sale = await who("sale1")
    a = await _file(sale, "mix-a", "Việc trộn A (test)")
    b = await _file(sale, "mix-b", "Việc trộn B (test)", command="crm.create_lead",
                    inp={"name": "Khách trộn", "phone": "0101234567", "offering_code": "HSR"})
    with pytest.raises(Invalid):
        await decisions.decide_many(sale, "proposal", [str(a), str(b)], "confirm")                      # khác lệnh ⇒ không gộp
    assert await owner_val("SELECT count(*) FROM core.proposals WHERE id = ANY(%s) AND status = 'open'", ([a, b],)) == 2
    money = await _file(sale, "money-a", "Phiếu chi (test)", command="acct.record_expense", inp={})
    with pytest.raises(Invalid):
        await decisions.decide_many(sale, "proposal", [str(money)], "confirm")                          # lệnh tiền ⇒ từng thẻ


async def test_a_card_whose_basis_changed_stays_open_while_the_others_go_through(who: Who) -> None:
    gd, sale = await who("giamdoc"), await who("sale1")
    await owner_val("DELETE FROM core.proposals WHERE user_id = %s RETURNING 1", (gd.user_id,))
    tids = []
    for i in range(2):
        out = await executor.run(gd, "flow.create_task", {"title": f"Việc căn cứ {i} (test lô)", "assignee_email": sale.email,
                                                           "due_on": (today() + timedelta(days=3)).isoformat()})
        tids.append(str(out.output["task_id"]))
    cards = []
    for i, t in enumerate(tids):
        r = await executor.propose(gd.as_agent(), "flow.adjust_task", {"task_id": t, "due_on": (today() + timedelta(days=9)).isoformat()})
        assert r.status == "proposed"
        cards.append(await _file(gd, f"basis-{i}", r.output["brief"]["title"], "flow.adjust_task", r.output["input"]))
        await owner_val("UPDATE core.proposals SET basis = %s WHERE id = %s RETURNING 1", (Jsonb(r.output.get("basis")) if r.output.get("basis") else None, cards[-1]))
    # việc thứ nhất bị người khác đổi ngay sau khi soạn thẻ ⇒ căn cứ khác
    await executor.run(gd, "flow.adjust_task", {"task_id": tids[0], "due_on": (today() + timedelta(days=5)).isoformat()})
    res = await decisions.decide_many(gd, "proposal", [str(c) for c in cards], "confirm")
    assert [r["status"] for r in res][1] == "ok"
    assert await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (UUID(tids[1]),)) == today() + timedelta(days=9)


async def test_approval_slips_of_one_chain_are_listed_with_their_brief_and_decided_in_one_go() -> None:
    tid, d = await _company(PEOPLE)
    admin, gd = await seed.who(tid, "admin", d), await seed.who(tid, "giamdoc", d)
    slips = []
    for role in ("admin", "staff"):
        r = await executor.run(admin, "admin.set_grant", {"role": role, "resource": "acct", "scope": "all", "actions": ["read"]})
        assert r.status == "pending_approval"
        slips.append(r.approval_id)
    mine = [x for x in await decisions.waiting(gd) if x["kind"] == "approval"]
    assert {x["id"] for x in mine} >= set(slips) and all(x["brief"]["title"] for x in mine) and len({x["group"] for x in mine}) == 1
    res = await decisions.decide_many(edge(gd), "approval", slips, "confirm")
    assert [r["status"] for r in res] == ["executed", "executed"], res
    assert await owner_val("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s AND command = 'admin.set_grant' AND status = 'ok' AND ref = ANY(%s)",
                           (tid, slips)) == 2                                                          # nhật ký từng phiếu
    assert not [x for x in await decisions.waiting(gd) if x["kind"] == "approval"]


async def test_cards_needing_reconciliation_head_the_list_and_never_batch(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rà 22/11: thẻ CẦN ĐỐI SOÁT (thẻ sáng — kể cả từ hôm trước — và thẻ trong chat) đứng đầu "Chờ tôi duyệt", nhóm riêng, không duyệt lô, có nút
    đối soát; thẻ đang chạy / đã lỗi hẳn không phải việc chờ quyết; số trên nút "Việc" đếm cả việc đang mắc."""
    import dataclasses

    from barista.agent import chat
    from barista.kernel import preview, registry
    from tests.conftest import fresh_phone
    from tests.test_assistant import Scripted, call
    from tests.test_resume import Log

    sale = await who("sale1")
    stuck = await _file(sale, "rec:stuck", "Thẻ sáng kẹt giữa lúc gọi ngoài")
    failed = await _file(sale, "rec:failed", "Thẻ sáng lỗi hẳn")
    still_open = await _file(sale, "rec:open", "Thẻ sáng còn chờ")
    async with db.owner_tx() as conn:
        await conn.execute("UPDATE core.proposals SET status = 'running', day = day - 2, decided_at = now() - (%s * interval '1 second') WHERE id = %s",
                           (preview.STALE_RUNNING_S + 5, stuck))
        await conn.execute("UPDATE core.proposals SET status = 'failed', result = '{\"status\": \"failed\"}' WHERE id = %s", (failed,))

    async def prepare(_prep, inp):
        raise TimeoutError("hết giờ chờ nhà cung cấp")

    orig = registry.OPS["crm.create_lead"]
    monkeypatch.setitem(registry.OPS, "crm.create_lead", dataclasses.replace(orig, prepare=prepare))
    model = Scripted([[call("crm.create_lead", name="Khách kẹt", phone=fresh_phone(), offering_code="HSR")], "Đã ghi nhận."])
    first = Log()
    await chat.chat_turn(sale, "Thêm khách", emit=first, model=model)
    chat_card = first.proposals()[0]
    await chat.decide(sale, chat_card["id"], "confirm", chat_card["input"], "rec-list", emit=Log(), model=model)

    items = await decisions.waiting(sale)
    head = [x for x in items if x["group"] == decisions.RECONCILE_GROUP]
    assert {x["id"] for x in head} == {str(stuck), chat_card["id"]}
    assert items[: len(head)] == head                                            # nhóm việc đang mắc đứng đầu
    assert all(not x["batchable"] and x["card"]["resolve"] == preview.RESOLVE for x in head)
    ids = {x["id"] for x in items}
    assert str(still_open) in ids and str(failed) not in ids
    assert await decisions.count(sale) >= 3                                       # còn chờ + 2 việc đang mắc
    with pytest.raises(Invalid):
        await decisions.decide_many(sale, "proposal", [str(stuck)], "confirm")    # không duyệt lô thẻ cần đối soát
