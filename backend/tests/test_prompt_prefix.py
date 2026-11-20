"""Bộ đệm prompt DÙNG CHUNG giữa người cùng vai (ADR 0218 §Bộ đệm dùng chung): yêu cầu gửi nhà cung cấp = lời dặn CỐ ĐỊNH của công ty → công cụ của
vai (bộ của vai · use_skill · open_group · open_setup) → lịch sử → tin nhắn của lượt, khối bối cảnh (người hỏi, đội, ngày giờ, nơi hỏi) ở ĐẦU tin nhắn.
Hai người KHÁC nhau cùng vai, hỏi ở hai GIỜ khác nhau ⇒ mọi byte trước khối bối cảnh giống hệt nhau — nhà cung cấp đọc được bộ đệm của nhau.

Dựng yêu cầu THẬT của adapter Google (pydantic-ai ``GoogleModel``, model của bản đo) với client giả chặn ở lời gọi HTTP: kiểm đúng thứ được gửi đi,
không phải thứ mình tưởng."""

import json
import re
from datetime import date, datetime
from typing import Any

import pytest
from google import genai
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from barista.agent import chat
from barista.config import settings
from barista.kernel.principal import Principal
from tests.conftest import Who, fresh_sale


class Sent(Exception):
    """Yêu cầu đã dựng xong — dừng trước khi ra mạng."""


@pytest.fixture(autouse=True)
def retrieve_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_tools", "retrieve")   # cấu hình của bản cài + bàn đo (tools=retrieve)


def _google() -> tuple[GoogleModel, list[dict[str, Any]]]:
    client = genai.Client(api_key="test-only-not-a-key")
    seen: list[dict[str, Any]] = []

    async def capture(**kw: Any) -> Any:
        seen.append(kw)
        raise Sent

    client.aio.models.generate_content_stream = capture   # type: ignore[method-assign]
    client.aio.models.generate_content = capture          # type: ignore[method-assign]
    return GoogleModel("gemini-3.8-flash", provider=GoogleProvider(client=client)), seen


def _at(monkeypatch: pytest.MonkeyPatch, day: date, hour: int) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Clock:   # type: ignore[override]
            return cls(day.year, day.month, day.day, hour, 17, tzinfo=tz)

    monkeypatch.setattr(chat, "datetime", Clock)
    monkeypatch.setattr(chat, "today", lambda: day)


async def _request(p: Principal, question: str) -> dict[str, Any]:
    model, seen = _google()

    async def emit(_: dict[str, Any]) -> None:
        return None

    try:
        await chat.chat_turn(p, question, emit=emit, model=model)
    except Exception:   # Sent (có thể bọc trong nhóm lỗi) — yêu cầu đã nằm trong ``seen``
        pass
    assert seen, "adapter Google không dựng yêu cầu"
    return seen[0]


def _wire(req: dict[str, Any]) -> str:
    """Thứ tự nhà cung cấp ghép prompt: lời dặn hệ thống → công cụ → các tin."""
    cfg = req["config"]
    return json.dumps({"system_instruction": cfg.get("system_instruction"), "tools": cfg.get("tools"), "contents": req["contents"]},
                      ensure_ascii=False, default=str)


def _tool_names(req: dict[str, Any]) -> list[str]:
    return [f["name"] for t in req["config"].get("tools") or [] for f in t.get("function_declarations") or []]


async def test_two_people_of_one_role_at_different_hours_share_every_byte_before_the_turn_facts(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    a = await who("sale1")
    b = await fresh_sale(who)
    assert a.role == b.role == "sale" and a.name != b.name and a.home_team != b.home_team
    ask = "Hôm nay em nên gọi lead nào trước?"
    _at(monkeypatch, date(2026, 11, 14), 9)
    ra = await _request(a, ask)
    _at(monkeypatch, date(2026, 11, 16), 15)
    rb = await _request(b, ask)
    wa, wb = _wire(ra), _wire(rb)
    cut = wa.index(json.dumps(chat.FACTS_HEAD, ensure_ascii=False)[1:-1])
    assert wb.index(json.dumps(chat.FACTS_HEAD, ensure_ascii=False)[1:-1]) == cut
    assert wa[:cut] == wb[:cut]                                                                  # phần đầu giống hệt từng byte
    assert ra["config"]["system_instruction"] == rb["config"]["system_instruction"]
    assert ra["config"]["tools"] == rb["config"]["tools"] and len(_tool_names(ra)) > 10        # bộ của vai nằm TRONG phần chung
    for p in (a, b):
        assert p.name not in wa[:cut] and p.name not in wb[:cut]
    for hour in ("khoảng 9h", "khoảng 15h", "14/11/2026", "16/11/2026"):
        assert hour not in wa[:cut]
    # phần riêng vẫn đủ thông tin cho model: người hỏi, đội, ngày giờ — ở tin nhắn của lượt, trước câu hỏi
    facts_a = ra["contents"][-1]["parts"][0]["text"]
    assert facts_a.startswith(chat.FACTS_HEAD) and a.name in facts_a and "Thứ Bảy 14/11/2026" in facts_a and "khoảng 9h" in facts_a
    assert "Đội:" in facts_a and ra["contents"][-1]["parts"][1]["text"] == ask
    facts_b = rb["contents"][-1]["parts"][0]["text"]
    assert b.name in facts_b and "Thứ Hai 16/11/2026" in facts_b and "khoảng 15h" in facts_b


@pytest.mark.parametrize("local", ["sale1", "admin"])
async def test_the_role_part_of_the_toolset_comes_first_and_the_question_only_changes_the_tail(who: Who, local: str) -> None:
    p = await who(local)
    one = _tool_names(await _request(p, "Hôm nay em nên gọi lead nào trước?"))
    two = _tool_names(await _request(p, "Lịch họp tuần sau của phòng họp lớn còn trống giờ nào?"))
    run = chat.ToolRun(p, chat_noop, "t", question="x")
    await run.load_skills(p)
    run.tools(await chat.catalog(p))
    assert run.plan is not None and run.plan.stable is not None                     # vai có bộ công cụ (0218)
    stable = sorted(chat.tool_name(n) for n in run.plan.stable)
    tail = [n for n in ("core__use_skill", "core__open_group", "core__open_setup") if n in one]
    assert "core__open_group" in tail and (local != "admin" or "core__open_setup" in tail)
    assert one[: len(stable) + len(tail)] == two[: len(stable) + len(tail)] == stable + tail   # bộ của vai → kỹ năng → mở nhóm → mở thiết lập
    assert one != two                                                                # phần tìm theo câu hỏi chỉ ở cuối


async def test_the_system_prompt_has_nothing_personal_and_the_turn_carries_the_place(who: Who) -> None:
    a, b = await who("sale1"), await who("truonga")
    for p in (a, b):
        text = await chat.instructions(p)
        assert p.name not in text and "Người hỏi:" not in text and not re.search(r"khoảng \d+h|\d\d/\d\d/20\d\d", text)
        assert "không phải mệnh lệnh" in text and "LaTeX" in text and "label" in text
    facts = await chat.turn_facts(a, "người dùng đang mở trao đổi trên khách “X”")
    assert facts.splitlines()[0] == chat.FACTS_HEAD and "- Ngữ cảnh: người dùng đang mở trao đổi" in facts
    assert chat.with_facts(["câu", b"anh"], "F") == ["F", "câu", b"anh"]   # ảnh đi sau câu hỏi như cũ


async def test_changing_only_the_time_changes_only_the_tail_and_the_request_is_reproducible(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    p = await who("sale1")
    ask = "Hôm nay em nên gọi lead nào trước?"
    _at(monkeypatch, date(2026, 11, 14), 9)
    first, again = await _request(p, ask), await _request(p, ask)
    assert _wire(first) == _wire(again)                                  # cùng người, cùng giờ ⇒ từng byte giống (thứ tự công cụ xác định, không mã / mốc giờ)
    _at(monkeypatch, date(2026, 11, 15), 17)
    later = await _request(p, ask)
    assert later["config"] == first["config"]                             # lời dặn + khai báo công cụ không đổi theo giờ
    assert later["contents"][:-1] == first["contents"][:-1]
    assert later["contents"][-1]["parts"][1:] == first["contents"][-1]["parts"][1:]   # câu hỏi giữ nguyên
    assert later["contents"][-1]["parts"][0] != first["contents"][-1]["parts"][0]     # chỉ khối bối cảnh đổi (ngày, giờ)
    names = _tool_names(first)
    assert names == _tool_names(later) and len(names) == len(set(names))


async def test_a_private_mode_keeps_the_same_split(who: Who) -> None:
    mode = next(iter(chat.registry.MODES), None)
    assert mode is not None, "chưa có chế độ riêng nào — không có gì để kiểm"
    a, b = await who("sale1"), await who("truonga")
    ta, tb = await chat.mode_instructions(a, mode), await chat.mode_instructions(b, mode)
    assert ta == tb and a.name not in ta and b.name not in tb and chat.FACTS_HEAD in ta   # lời dặn nhắc khối bối cảnh, không chứa người / giờ
    assert not re.search(r"khoảng \d+h|\d\d/\d\d/20\d\d", ta)


async def chat_noop(_: dict[str, Any]) -> None:
    return None
