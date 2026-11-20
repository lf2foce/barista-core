"""Chữ và việc phải khớp (eval DeepSeek 11/11 §4.4): kết quả công cụ + tựa thẻ nói rõ "Đã chạy" hay "Đề xuất — chờ duyệt" — suy từ ``status`` của kết quả
executor (``chat.outcome_of``, MỘT nguồn), và lời dặn của trợ lý nhắc tới nhãn đó thay vì một mẫu câu cố định."""

import uuid
from datetime import timedelta
from typing import Any

import pytest

from barista import seed, seed_pay
from barista.agent import chat
from barista.config import settings
from barista.kernel import executor, registry
from barista.kernel.clock import today
from tests.conftest import Who, approved
from tests.test_assistant import Scripted, _turn, call
from tests.test_autonomy_chat import PEOPLE

PROPOSED, RAN = "Đề xuất — chờ duyệt", "Đã chạy"


def test_one_source_maps_executor_status_to_label_and_flag() -> None:
    assert chat.outcome_of("ok") == {"label": RAN, "executed": True}
    assert chat.outcome_of("proposed") == {"label": PROPOSED, "executed": False}
    assert chat.outcome_of("pending_approval")["executed"] is False and "duyệt" in chat.outcome_of("pending_approval")["label"]   # chưa có hiệu lực
    assert chat.outcome_of("denied") == {} and chat.outcome_of("conflict") == {}   # không thành công ⇒ không nhãn nào nói "đã làm"


def test_rules_tell_the_model_to_follow_the_label_not_a_fixed_template() -> None:
    assert "label" in chat.RULES and PROPOSED in chat.RULES and RAN in chat.RULES
    assert 'nói "em đã soạn đề xuất, anh/chị kiểm tra rồi bấm Xác nhận"' not in chat.RULES   # mẫu câu cố định cũ


async def test_proposed_result_and_card_say_proposal_not_done(who: Who) -> None:
    sale1 = await who("sale1")
    phone = f"010{uuid.uuid4().int % 10**7:07d}"
    cards: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        if ev["type"] == "card":
            cards.append(ev["card"])

    # defer=False (việc theo dõi chạy nền): kết quả "proposed" trả thẳng cho model — chính cái model đọc ở mọi bề mặt không dừng chờ người
    r = await chat.ToolRun(sale1, emit, uuid.uuid4().hex).call(registry.OPS["crm.create_lead"], {"name": "Khách nhãn", "phone": phone, "offering_code": "HSR"})
    assert r["status"] == "proposed" and r["label"] == PROPOSED and r["executed"] is False and "CHƯA ghi" in r["message"]
    card = cards[0]
    assert card["kind"] == "proposal" and card["state"] == "proposed" and card["state_label"] == PROPOSED


async def test_autorun_result_and_card_say_ran_and_the_reply_stops_inviting_a_click() -> None:
    d = f"u{uuid.uuid4().hex[:8]}.demo"
    tid = await seed.create_tenant(f"Công ty nhãn {d}", d, people=PEOPLE, packs=(), about="công ty thử (test)")
    admin, nv = [await seed.who(tid, x, d) for x in ("admin", "nv")]
    await approved(admin, "admin.set_agent_preset", preset="balanced")
    wrong = "Em đã soạn đề xuất ngay bên dưới — anh/chị bấm Xác nhận nhé."   # đúng cái lỗi eval: lệnh đã chạy thẳng mà lời vẫn mời bấm
    out, events, model = await _turn(nv, "Tạo việc", [
        [call("flow.create_task", title=f"Việc nhãn {uuid.uuid4().hex[:4]}", assignee_email=nv.email, due_on=today().isoformat())], wrong])
    ret = model.returns[0]
    assert ret["status"] == "ok" and ret["label"] == RAN and ret["executed"] is True and "KHÔNG" not in ret["label"] and "Xác nhận" in ret["message"]
    card = next(e["card"] for e in events if e["type"] == "card")
    assert card["kind"] == "result" and card["title"].startswith(RAN) and card["state"] == "executed"
    assert "Xác nhận" not in out["text"] and "đề xuất" not in out["text"].lower()   # câu lệch với việc đã làm bị bỏ, không để người bấm tìm thẻ không có


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    return await seed_pay.office(f"ol{uuid.uuid4().hex[:6]}.demo", "Văn phòng nhãn thử (test)")


async def test_leave_card_does_not_claim_the_request_was_sent(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_tools", "all")
    p = co["who"]["nvchi"]
    friday = today() + timedelta(days=(4 - today().weekday()) % 7 or 7)
    _, events, _ = await _turn(p, "Cho tôi xin nghỉ phép thứ Sáu này",
                            [[call("timekeeping.request_leave", leave_type="PHEP_NAM", from_day=friday.isoformat(), reason="Việc gia đình")], "Soạn rồi."])
    card = next(e["card"] for e in events if e["type"] == "card" and e["card"]["kind"] == "proposal")
    assert not card["title"].startswith("Đã gửi") and card["title"].startswith("Sẽ gửi đơn"), card["title"]
    done = await executor.run(p, "timekeeping.request_leave", card["input"])                           # người bấm ⇒ chạy thật: lúc này mới "Đã gửi"
    assert done.output["summary"].startswith("Đã gửi đơn")


async def test_decided_card_result_carries_the_ran_label(who: Who) -> None:
    sale1 = await who("sale1")
    phone = f"010{uuid.uuid4().int % 10**7:07d}"
    _, events, _ = await _turn(sale1, "Thêm khách", [[call("crm.create_lead", name="Khách xác nhận nhãn", phone=phone, offering_code="HSR")]])
    card = next(e["card"] for e in events if e["type"] == "card")
    after: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        after.append(ev)

    model = Scripted(["Đã thêm khách."])
    await chat.decide(sale1, card["id"], "confirm", card["input"], f"k-label-{phone}", emit=emit, model=model)
    seen = model.returns[0]
    assert seen["label"] == RAN and seen["executed"] is True and "confirmed_by_user" in seen


def test_only_genuinely_unsent_summaries_are_rewritten() -> None:
    assert executor._unsent({"summary": "Đã gửi đơn nghỉ 1 ngày"}) == {"summary": "Sẽ gửi đơn nghỉ 1 ngày"}
    already = {"summary": "Đã xác nhận GD-1 trước đó"}
    assert executor._unsent(already) == already and executor._unsent({"summary": "7 việc"}) == {"summary": "7 việc"}
