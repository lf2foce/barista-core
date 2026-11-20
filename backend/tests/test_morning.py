"""Đề xuất buổi sáng (quyết định 0074 A): gợi ý do code chọn theo quyền người nhận, chạy thử thành thẻ, người bấm mới ghi."""

import dataclasses
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from barista.agent import morning
from barista.kernel import executor
from barista.kernel.clock import VN, today
from barista.kernel.principal import Principal
from tests.conftest import Who, ok, owner_val


async def test_owner_gets_ready_cards_nothing_written_until_confirmed(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    gd, lead, sale = await who("giamdoc"), await who("mkt"), await who("sale1")
    # mốc HÔM NAY: nguồn gợi ý chỉ xét 20 kế hoạch đầu theo mốc (không mốc xếp cuối) — kế hoạch của test khác không đẩy được nó ra
    out = await ok(gd, "flow.propose_plan", title="Kế hoạch sáng nay (test)", team_code="MKT", due_on=today().isoformat(),
                   tasks=[{"title": "Việc sale chậm sáng", "assignee_email": sale.email}])
    await owner_val("UPDATE flow.tasks SET due_on = %s WHERE plan_id = %s RETURNING 1", (today() - timedelta(days=1), out["plan_id"]))
    await owner_val("DELETE FROM core.proposals WHERE user_id = %s RETURNING 1", (lead.user_id,))
    # Trần MAX_CARDS = 3 thẻ / người / ngày xếp theo hạng trên MỌI dữ liệu của người nhận. Cùng tiến trình xdist (--dist loadfile ⇒ cùng DB) test khác
    # để lại kế hoạch của trưởng MKT có việc quá hạn: test_plan_assign 4, test_goals thêm 1 — cùng hạng 50, kế hoạch có mốc xếp trước kế hoạch không
    # mốc (bản test cũ) ⇒ thẻ của nó rơi khỏi 3 chỗ, `next(...)` ném StopIteration (15/11/2026; tái hiện ở bản cũ: pytest -n0 tests/test_plan_assign.py
    # tests/test_goals.py tests/test_morning.py). Luật trần + thứ hạng đúng (test riêng bên dưới); ở đây kiểm nguồn gợi ý THẬT có kế hoạch này, rồi
    # soạn thẻ từ đúng gợi ý đó.
    real = morning.suggestions
    mine = [s for s in await real(lead) if (s.get("input") or {}).get("plan_id") == str(out["plan_id"])]
    assert [s["command"] for s in mine] == ["flow.remind_plan"]

    async def only_this_plan(p: Principal) -> list[dict[str, Any]]:
        return [s for s in await real(p) if (s.get("input") or {}).get("plan_id") == str(out["plan_id"])]
    monkeypatch.setattr(morning, "suggestions", only_this_plan)
    before = await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Nhắc:%%sáng nay%%'", (sale.user_id,))
    cards = await morning.cards(lead)
    card = next(c for c in cards if c["input"].get("plan_id") == str(out["plan_id"]))
    assert card["command"] == "flow.remind_plan" and "quá hạn" in card["reason"] and card["fields"]           # thẻ có lý do + form sửa được
    assert len(cards) <= morning.MAX_CARDS
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Nhắc:%%sáng nay%%'",
                           (sale.user_id,)) == before                                                       # chạy thử: chưa nhắc ai
    assert (await morning.decide(sale, UUID(card["id"]), "confirm", None, "k1"))["status"] == "not_found"    # thẻ của người khác
    r = await morning.decide(lead, UUID(card["id"]), "confirm", None, "k1")
    assert r["status"] == "ok"
    trail = await owner_val("""SELECT jsonb_agg(jsonb_build_array(via, status) ORDER BY id) FROM core.audit_log WHERE command = 'flow.remind_plan'
                               AND trace = (SELECT 'morning:' || user_id || ':' || day || ':' || key FROM core.proposals WHERE id = %s)""",
                            (card["id"],))
    assert trail == [["agent", "proposed"], ["human", "ok"]]                                                # vết (0086): trợ lý soạn → người xác nhận
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Nhắc:%%sáng nay%%'",
                           (sale.user_id,)) == before + 1
    assert not any(c["id"] == card["id"] for c in await morning.cards(lead))                                 # xong ⇒ rời danh sách
    await morning.prepare(lead)
    assert not any(c["input"].get("plan_id") == str(out["plan_id"]) for c in await morning.cards(lead))      # cùng khoá trong ngày ⇒ không soạn lại


async def test_at_most_max_cards_a_day_best_ranked_first(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trần thẻ / người / ngày: 4 kế hoạch cùng hạng (trễ) ⇒ đúng MAX_CARDS thẻ, theo thứ tự nguồn (mốc sớm trước); soạn lại trong ngày không thêm."""
    gd, lead, sale = await who("giamdoc"), await who("mkt"), await who("sale1")
    plans = []
    for i in range(morning.MAX_CARDS + 1):
        out = await ok(gd, "flow.propose_plan", title=f"Kế hoạch trần thẻ {i} (test)", team_code="MKT", due_on=(today() + timedelta(days=i)).isoformat(),
                       tasks=[{"title": f"Việc trễ {i}", "assignee_email": sale.email}])
        await owner_val("UPDATE flow.tasks SET due_on = %s WHERE plan_id = %s RETURNING 1", (today() - timedelta(days=1), out["plan_id"]))
        plans.append(str(out["plan_id"]))
    await owner_val("DELETE FROM core.proposals WHERE user_id = %s RETURNING 1", (lead.user_id,))
    real = morning.suggestions

    async def only_these(p: Principal) -> list[dict[str, Any]]:   # chỉ kế hoạch của test này — dữ liệu test khác trong cùng DB không xen vào
        return [s for s in await real(p) if (s.get("input") or {}).get("plan_id") in plans]
    monkeypatch.setattr(morning, "suggestions", only_these)
    assert len(await only_these(lead)) == morning.MAX_CARDS + 1
    assert [c["input"]["plan_id"] for c in await morning.cards(lead)] == plans[:morning.MAX_CARDS]
    assert await morning.prepare(lead) == 0                                                                  # hết chỗ trong ngày


async def test_sale_gets_a_call_slot_for_overdue_follow_ups_and_can_dismiss(who: Who) -> None:
    sale = await who("sale2")
    lead_id = await owner_val("""SELECT l.id FROM crm.leads l WHERE l.owner_id = %s AND l.stage IN ('lead', 'prospect')
                                 AND NOT EXISTS (SELECT 1 FROM crm.appointments a WHERE a.lead_id = l.id AND a.status = 'scheduled') LIMIT 1""",
                              (sale.user_id,))
    await owner_val("UPDATE crm.leads SET next_follow_up = %s WHERE id = %s RETURNING 1", (today() - timedelta(days=9), lead_id))
    await owner_val("DELETE FROM core.proposals WHERE user_id = %s RETURNING 1", (sale.user_id,))
    cards = await morning.cards(sale)
    card = next(c for c in cards if c["input"].get("lead_id") == str(lead_id))
    assert card["command"] == "crm.schedule" and "quá hẹn gọi lại 9 ngày" in card["reason"]
    assert await owner_val("SELECT count(*) FROM crm.appointments WHERE lead_id = %s AND status = 'scheduled'", (lead_id,)) == 0
    assert (await morning.decide(sale, UUID(card["id"]), "dismiss", None, "k2"))["status"] == "dismissed"
    await morning.prepare(sale)
    assert not any(c["input"].get("lead_id") == str(lead_id) for c in await morning.cards(sale))            # đã bỏ ⇒ không hỏi lại trong ngày


async def test_propose_never_runs_even_when_the_assistant_may_act_directly(who: Who) -> None:
    sale = await who("sale1")
    lead_id = await owner_val("SELECT id FROM crm.leads WHERE owner_id = %s LIMIT 1", (sale.user_id,))
    n = await owner_val("SELECT count(*) FROM crm.activities WHERE lead_id = %s", (lead_id,))
    r = await executor.propose(sale.as_agent(), "crm.log_activity", {"lead_id": str(lead_id), "kind": "note", "note": "chỉ chạy thử"})
    assert r.status == "proposed"                                                                            # log_activity là "allow" nhưng ở đây chỉ soạn
    assert await owner_val("SELECT count(*) FROM crm.activities WHERE lead_id = %s", (lead_id,)) == n


async def test_ai_off_means_no_cards_and_old_open_cards_expire(who: Who) -> None:
    lead = await who("mkt")
    assert await morning.cards(dataclasses.replace(lead, ai=False)) == []
    await owner_val("""INSERT INTO core.proposals (tenant_id, user_id, day, key, command, title, input)
                       VALUES (%s, %s, %s, 'old:x', 'flow.remind_plan', 'Cũ', '{}') RETURNING 1""", (lead.tenant_id, lead.user_id, today() - timedelta(days=1)))
    await morning.prepare(lead)
    assert await owner_val("SELECT status FROM core.proposals WHERE user_id = %s AND key = 'old:x'", (lead.user_id,)) == "expired"


async def test_morning_run_covers_each_company_once_a_day(who: Who) -> None:
    lead = await who("mkt")
    await owner_val("DELETE FROM core.proposal_runs WHERE tenant_id = %s RETURNING 1", (lead.tenant_id,))
    early = datetime.combine(today(), datetime.min.time(), VN).replace(hour=5)
    # đếm lượt của CHÍNH công ty này: run_once quét mọi công ty trong DB — công ty của test khác (múi giờ khác, vd 5 giờ VN = 7 giờ Tokyo) làm số
    # trả về của run_once phụ thuộc thứ tự test chạy
    async def runs() -> int:
        return await owner_val("SELECT count(*) FROM core.proposal_runs WHERE tenant_id = %s AND day = %s", (lead.tenant_id, today()))
    await morning.run_once(early)
    assert await runs() == 0                                                                                  # trước 6 giờ: chưa chạy
    await morning.run_once(early.replace(hour=7))
    assert await runs() == 1
    await morning.run_once(early.replace(hour=8))
    assert await runs() == 1                                                                                  # mỗi công ty một lượt / ngày


async def test_team_task_nobody_took_goes_to_the_direct_lead_only(who: Who) -> None:
    gd, truonga = await who("giamdoc"), await who("truonga")
    t = await ok(truonga, "flow.create_task", title="Việc chung chưa ai nhận (test sáng)", team_code="KD-A")
    # gợi ý chỉ lấy 2 việc chung đầu (theo hạn rồi ngày tạo); test khác trong cùng DB để lại việc KD-A chưa ai nhận có hạn ⇒ cho việc này hạn sớm nhất
    await owner_val("UPDATE flow.tasks SET created_at = now() - interval '2 days', due_on = DATE '2000-01-01' WHERE id = %s RETURNING 1", (t["task_id"],))
    # kiểm DANH SÁCH GỢI Ý (trước trần MAX_CARDS = 3 thẻ / người / ngày): qua morning.cards thì lead / việc test khác để lại trong cùng DB có hạng cao
    # hơn chiếm hết 3 chỗ ⇒ test hỏng theo thứ tự chạy (11/11/2026), dù luật "chỉ trưởng trực tiếp" vẫn đúng. Trần 3 thẻ có test riêng.
    mine = [c for c in await morning.suggestions(truonga) if (c.get("input") or {}).get("task_id") == str(t["task_id"])]
    assert mine and mine[0]["command"] == "flow.adjust_task" and "ít việc nhất" in mine[0]["reason"]
    assert not any((c.get("input") or {}).get("task_id") == str(t["task_id"]) for c in await morning.suggestions(gd))   # giám đốc không nhận trùng

