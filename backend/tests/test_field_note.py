"""Nói ghi chú hiện trường (quyết định 0074 D): chép lời ⇒ hiện cho người xem ⇒ một lượt trợ lý kiểu ghi chú (đề xuất để bấm)."""

import dataclasses
from typing import Any

import pytest

from barista.agent import chat, field_note
from tests.conftest import Who


async def test_transcript_is_shown_then_the_assistant_runs_a_field_note_turn(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    sale = await who("sale1")
    events: list[dict[str, Any]] = []
    seen: dict[str, Any] = {}

    async def fake_transcribe(p: Any, audio: bytes, media_type: str) -> str:
        assert audio == b"RIFF..." and media_type == "audio/wav"
        return "Vừa gọi chị Lan, hẹn thứ Bảy xem nhà"

    async def fake_turn(p: Any, message: str, **kw: Any) -> dict[str, Any]:
        seen.update(message=message, **kw)
        return {"text": "ok"}

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)
    monkeypatch.setattr(field_note, "transcribe", fake_transcribe)
    monkeypatch.setattr(chat, "chat_turn", fake_turn)
    await field_note.turn(sale, b"RIFF...", "audio/wav", emit=emit)
    assert events[0] == {"type": "transcript", "text": "Vừa gọi chị Lan, hẹn thứ Bảy xem nhà"}                 # người thấy máy nghe được gì
    assert seen["message"] == "Vừa gọi chị Lan, hẹn thứ Bảy xem nhà" and seen["style"] == field_note.FIELD_STYLE
    assert seen["surface"] == "voice"                                                                         # tính chi phí như giọng nói


async def test_nothing_heard_or_ai_off_says_so_and_runs_nothing(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    sale = await who("sale1")
    events: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    async def silent(p: Any, audio: bytes, media_type: str) -> str:
        return ""
    monkeypatch.setattr(field_note, "transcribe", silent)
    await field_note.turn(sale, b"x", "audio/wav", emit=emit)
    assert events[-1]["type"] == "error" and "không nghe rõ" in events[-1]["message"]
    monkeypatch.undo()
    events.clear()
    await field_note.turn(dataclasses.replace(sale, ai=False), b"x", "audio/wav", emit=emit)
    assert events and events[-1]["type"] == "error"                                                           # công tắc AI tắt ⇒ không gọi model
