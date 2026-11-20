"""Giọng nói (quyết định 0042): Gemini Live chỉ là lớp giọng — câu hỏi công việc đi qua MỘT lượt chat thật (cùng quyền, cùng cuộc trò chuyện).
Ở đây thay Gemini bằng "live" giả và model chat bằng kịch bản; đường nối thật với Gemini kiểm bằng script probe (chạy tay, cần khoá)."""

import asyncio
import json
from typing import Any

import pytest
from google.genai import types
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from barista.agent import llm, voice
from barista.http_api import app
from tests.conftest import Who
from tests.test_assistant import Scripted, call


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def receive_text(self) -> str:
        await asyncio.sleep(3600)
        return ""

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


class FakeLive:
    def __init__(self) -> None:
        self.responses: list[Any] = []

    async def send_tool_response(self, function_responses: list[types.FunctionResponse]) -> None:
        self.responses.extend(function_responses)


@pytest.fixture
def voice_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Các test cầu nối giọng nói ↔ chat dùng sale1 (hạng "Cơ bản" mặc định KHÔNG giọng nói — 0193): mở cửa giọng nói cho riêng test này; cửa thật
    kiểm ở tests/test_ai_tiers.py."""
    monkeypatch.setattr(llm, "voice_for", lambda _p: "khoa-test")


def ask(question: str) -> types.FunctionCall:
    return types.FunctionCall(id="c1", name="ask_assistant", args={"question": question})


def test_live_config_does_not_send_unsupported_thinking_budget() -> None:
    config = voice.live_config("Trợ lý vận hành", None)
    assert config.thinking_config is None
    assert config.tools and config.response_modalities == [types.Modality.AUDIO]


async def test_voice_question_runs_a_real_chat_turn_with_cards_and_keeps_the_thread(who: Who, voice_on: None) -> None:
    sale1 = await who("sale1")
    ws, live = FakeWS(), FakeLive()
    model = Scripted([[call("crm.leads", stage="open")], "Anh có vài khách đến hạn.",
                      "Dạ, khách đầu tiên là người em vừa nêu."])
    bridge = voice.Bridge(ws, sale1, "x", None, model=model)  # type: ignore[arg-type]
    await bridge.answer(live, ask("Khách nào đến hạn gọi lại?"))
    types_seen = [m["type"] for m in ws.sent]
    assert types_seen[0] == "chat_start" and "chat" in types_seen
    events = [m["event"]["type"] for m in ws.sent if m["type"] == "chat"]
    assert "card" in events and "step" in events and events[-1] == "reply"          # thẻ + bước stream về app như chat chữ
    assert live.responses and "đến hạn" in live.responses[0].response["answer"]     # Gemini nhận tóm tắt để đọc
    assert bridge.thread_id is not None
    first = bridge.thread_id
    await bridge.answer(live, ask("Còn khách thứ hai?"))                              # câu nối tiếp: cùng cuộc trò chuyện
    assert bridge.thread_id == first and len(live.responses) == 2
    assert model.history_len[-1] > model.history_len[0]                                # model thấy lịch sử của lượt trước


async def test_voice_asks_for_short_spoken_answers_and_never_widens_the_toolset(who: Who, voice_on: None) -> None:
    sale1 = await who("sale1")
    seen: list[str] = []

    class Spy(Scripted):
        def _next(self, messages: Any, info: Any) -> Any:
            seen.append(info.instructions or "")
            return super()._next(messages, info)

    model = Spy(["Dạ."])
    bridge = voice.Bridge(FakeWS(), sale1, "x", None, model=model)  # type: ignore[arg-type]
    await bridge.answer(FakeLive(), ask("Chào em"))
    assert "BẰNG GIỌNG NÓI" in seen[0]                                                  # dặn trả lời ngắn, không bảng
    assert "leadflow__set_rule" not in model.tools_seen[0]                              # cùng bộ công cụ theo quyền như chat chữ
    assert "leadflow__intake" not in model.tools_seen[0]                                # sale không có công cụ nạp / chia lead


async def test_same_question_twice_at_once_is_answered_once(who: Who) -> None:
    sale1 = await who("sale1")
    live = FakeLive()
    model = Scripted([[call("crm.leads", stage="open")], "Xong."])
    bridge = voice.Bridge(FakeWS(), sale1, "x", None, model=model)  # type: ignore[arg-type]
    bridge.pending.add("Khách nào?")                                                     # Gemini gọi lặp do tiếng vọng
    await bridge.answer(live, ask("Khách nào?"))
    assert len(live.responses) == 1 and "đang được xử lý" in live.responses[0].response["answer"]
    assert not model.turns or len(model.turns) == 2                                      # lượt chat KHÔNG chạy lần hai


def test_voice_socket_refuses_callers_without_identity() -> None:
    client = TestClient(app)
    try:
        with client.websocket_connect("/v1/assistant/voice") as ws:
            ws.receive_text()
    except WebSocketDisconnect as e:
        assert e.code == 4401
    else:
        raise AssertionError("phải từ chối khi chưa đăng nhập")
