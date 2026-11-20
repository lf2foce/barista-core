"""Trợ lý dừng chờ người duyệt rồi chạy tiếp (quyết định 0049). Model giả theo kịch bản (tests/test_assistant.py)."""

from datetime import timedelta
from typing import Any

import pytest
from pydantic_ai import ToolCallPart

from barista.agent import chat
from barista.config import settings
from barista.kernel.clock import today
from tests.conftest import Who, fresh_phone, owner_val
from tests.test_assistant import Scripted, call


class Log:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, ev: dict[str, Any]) -> None:
        self.events.append(ev)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == kind]

    def proposals(self) -> list[dict[str, Any]]:
        return [e["card"] for e in self.of("card") if e["card"]["kind"] == "proposal"]


def task(title: str, call_id: str) -> ToolCallPart:
    return ToolCallPart(chat.tool_name("flow.create_task"), {"title": title, "due_on": (today() + timedelta(days=1)).isoformat()},
                        tool_call_id=call_id)


async def _customers(phone: str) -> int:
    return await owner_val("SELECT count(*) FROM crm.customers WHERE phone = %s", (phone,))


async def test_confirm_runs_with_the_persons_rights_and_the_assistant_continues_with_the_real_result(who: Who) -> None:
    sale1 = await who("sale1")
    phone = fresh_phone()
    model = Scripted([
        [call("crm.create_lead", name="Khách Mai chạy tiếp", phone=phone, offering_code="HSR")],
        [task("Gọi lại khách Mai chạy tiếp", "c-task")],     # sau khi người xác nhận: bước kế, lại là đề xuất ⇒ lại dừng
        "Xong cả hai việc ạ.",
    ])
    first = Log()
    out = await chat.chat_turn(sale1, "Thêm khách Mai rồi tạo việc gọi lại", emit=first, model=model)
    lead = first.proposals()[0]
    assert out["pending"] and await _customers(phone) == 0 and "Xác nhận" in out["text"]

    second = Log()
    await chat.decide(sale1, lead["id"], "confirm", {**lead["input"], "name": "Khách Mai (đã sửa)"}, "k-lead-1", emit=second, model=model)
    assert second.events[0] == {"type": "decided", "card_id": lead["id"], "status": "ok", "message": second.events[0]["message"],
                                "replayed": False, "resuming": True}
    assert await owner_val("SELECT name FROM crm.customers WHERE phone = %s", (phone,)) == "Khách Mai (đã sửa)"   # chạy đúng bản người sửa
    assert await owner_val("""SELECT via FROM core.audit_log WHERE command = 'crm.create_lead' AND status = 'ok'
                              ORDER BY id DESC LIMIT 1""") != "agent"                                             # quyền NGƯỜI, không phải trợ lý
    seen = model.returns[-1]
    assert seen["status"] == "ok" and "lead_id" in str(seen["output"]) and seen["confirmed_by_user"]                         # trợ lý nhận kết quả THẬT
    follow = second.proposals()
    assert [c["command"] for c in follow] == ["flow.create_task"] and second.of("done")[-1]["pending"] is True

    third = Log()
    await chat.decide(sale1, follow[0]["id"], "confirm", follow[0]["input"], "k-task-1", emit=third, model=model)
    assert third.of("done")[-1]["text"] == "Xong cả hai việc ạ." and not third.of("done")[-1]["pending"]
    turn = (await chat.load_thread(sale1, out["thread_id"]))["turns"][-1]                                    # type: ignore[index]
    assert "pending" not in turn and len(turn["decided"]) == 2 and turn["text"].endswith("Xong cả hai việc ạ.")


async def test_dismissing_writes_nothing_and_the_assistant_is_told(who: Who) -> None:
    sale1 = await who("sale1")
    phone = fresh_phone()
    model = Scripted([[call("crm.create_lead", name="Khách bị bỏ", phone=phone, offering_code="HSR")], "Vâng, em không tạo nữa."])
    first = Log()
    await chat.chat_turn(sale1, "Thêm khách", emit=first, model=model)
    after = Log()
    await chat.decide(sale1, first.proposals()[0]["id"], "dismiss", {}, "k-dismiss", emit=after, model=model)
    assert after.events[0]["status"] == "dismissed" and await _customers(phone) == 0
    assert "BỎ" in str(model.returns[-1]) and after.of("done")[-1]["text"] == "Vâng, em không tạo nữa."


async def test_a_broken_edit_goes_back_to_the_form_and_the_proposal_keeps_waiting(who: Who) -> None:
    sale1 = await who("sale1")
    phone = fresh_phone()
    model = Scripted([[call("crm.create_lead", name="Khách sửa sai", phone=phone, offering_code="HSR")], "Đã tạo ạ."])
    first = Log()
    await chat.chat_turn(sale1, "Thêm khách", emit=first, model=model)
    card = first.proposals()[0]
    bad = Log()
    await chat.decide(sale1, card["id"], "confirm", {**card["input"], "phone": "abc"}, "k-bad", emit=bad, model=model)
    assert bad.events == [{"type": "rejected", "card_id": card["id"], "status": "invalid", "message": bad.events[0]["message"]}]
    assert await _customers(phone) == 0 and model.turns == ["Đã tạo ạ."]                                     # chưa đánh thức trợ lý
    good = Log()
    await chat.decide(sale1, card["id"], "confirm", card["input"], "k-good", emit=good, model=model)
    assert await _customers(phone) == 1 and good.of("done")[-1]["text"] == "Đã tạo ạ."


async def test_two_proposals_in_one_step_wait_for_both_decisions(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_tools", "all")   # kiểm việc khác — model giả gọi cứng công cụ ngoài bộ "tìm trước" của câu này
    sale1 = await who("sale1")
    model = Scripted([[task("Việc A chờ đủ", "c-a"), task("Việc B chờ đủ", "c-b")], "Xong hai việc."])
    first = Log()
    await chat.chat_turn(sale1, "Tạo hai việc", emit=first, model=model)
    a, b = first.proposals()
    one = Log()
    await chat.decide(sale1, a["id"], "confirm", a["input"], "k-a", emit=one, model=model)
    assert one.events[0]["resuming"] is False and model.turns == ["Xong hai việc."]                          # còn B chưa quyết
    two = Log()
    await chat.decide(sale1, b["id"], "dismiss", {}, "k-b", emit=two, model=model)
    assert two.events[0]["resuming"] is True and two.of("done")[-1]["text"] == "Xong hai việc."
    assert await owner_val("SELECT count(*) FROM flow.tasks WHERE title IN ('Việc A chờ đủ', 'Việc B chờ đủ')") == 1


async def test_after_moving_on_a_confirm_still_runs_but_does_not_wake_the_old_turn(who: Who) -> None:
    sale1 = await who("sale1")
    phone = fresh_phone()
    model = Scripted([[call("crm.create_lead", name="Khách lượt cũ", phone=phone, offering_code="HSR")], "Dạ, anh hỏi gì ạ?"])
    first = Log()
    out = await chat.chat_turn(sale1, "Thêm khách", emit=first, model=model)
    await chat.chat_turn(sale1, "Thôi, hỏi chuyện khác", emit=Log(), thread_id=out["thread_id"], model=model)   # lịch sử chờ ⇒ dạng chữ
    late = Log()
    await chat.decide(sale1, first.proposals()[0]["id"], "confirm", first.proposals()[0]["input"], "k-late", emit=late, model=model)
    assert late.events[0]["resuming"] is False and await _customers(phone) == 1 and model.turns == []


async def test_pressing_again_never_runs_twice_and_others_cannot_decide(who: Who) -> None:
    sale1, sale2 = await who("sale1"), await who("sale2")
    phone = fresh_phone()
    model = Scripted([[call("crm.create_lead", name="Khách bấm hai lần", phone=phone, offering_code="HSR")], "Đã tạo."])
    first = Log()
    await chat.chat_turn(sale1, "Thêm khách", emit=first, model=model)
    card = first.proposals()[0]
    stranger = Log()
    await chat.decide(sale2, card["id"], "confirm", card["input"], "k-x", emit=stranger, model=model)
    assert stranger.of("error") and await _customers(phone) == 0                                             # cuộc trò chuyện của người khác
    await chat.decide(sale1, card["id"], "confirm", card["input"], "k-once", emit=Log(), model=model)
    again = Log()
    await chat.decide(sale1, card["id"], "confirm", card["input"], "k-once-2", emit=again, model=model)
    assert again.events[0]["replayed"] is True and await _customers(phone) == 1 and len(again.of("decided")) == 1


async def test_assistant_proposal_and_the_persons_confirmation_share_one_trace(who: Who) -> None:
    """Nhật ký thao tác nối được "trợ lý soạn ở lượt nào → ai xác nhận" (mã vết, migration 001) — màn Hành động của trợ lý đọc chuỗi đó."""
    from barista.kernel import executor

    sale1, admin = await who("sale1"), await who("admin")
    phone = fresh_phone()
    model = Scripted([[call("crm.create_lead", name="Khách vết", phone=phone, offering_code="HSR")], "Đã thêm ạ."])
    first = Log()
    out = await chat.chat_turn(sale1, "Thêm khách vết", emit=first, model=model)
    lead = first.proposals()[0]
    await chat.decide(sale1, lead["id"], "confirm", lead["input"], "k-trace-1", emit=Log(), model=model)
    trail = await owner_val("""SELECT jsonb_agg(jsonb_build_array(via, status) ORDER BY id) FROM core.audit_log
                               WHERE command = 'crm.create_lead' AND trace = (SELECT 'chat:' || (t->>'trace') FROM core.chat_threads c,
                               jsonb_array_elements(c.turns) t WHERE c.id = %s AND t->>'role' = 'assistant' LIMIT 1)""", (out["thread_id"],))
    assert trail == [["agent", "proposed"], ["human", "ok"]]                                     # cùng một vết: trợ lý soạn → người xác nhận
    page = await executor.query(admin, "admin.agent_actions", {"days": 1})
    row = next(r for r in page.output["rows"] if r["command"] == "crm.create_lead" and r["step"] == "Người xác nhận" and r["who"] == sale1.name)
    chain = (await executor.query(admin, "admin.trace", {"audit_id": row["id"]})).output
    assert [i["title"].split(":")[0] for i in chain["blocks"][0]["items"]] == ["Trợ lý soạn đề xuất", "Người xác nhận"]
