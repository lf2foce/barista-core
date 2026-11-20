"""Nhận lượt trợ lý + tắt máy chủ giữa lượt (review 12/11 F5 / F6, ADR 0200): trần thân request / độ dài câu hỏi / số lượt cùng lúc (người, công ty,
tiến trình) trả 429 / 503 có câu tiếng Việt + Retry-After; lượt quá giờ và lượt còn chạy lúc tắt máy chủ dừng có kiểm soát, lưu phần đã làm kèm trạng
thái "bị ngắt" để người dùng thấy lượt dở thay vì mất trắng."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from barista.agent import chat, turns
from barista.config import settings
from barista.http_api import app
from barista.kernel.principal import Principal
from tests.conftest import Who, fresh_phone, owner_val
from tests.test_assistant import Scripted, call


@pytest.fixture(autouse=True)
def _clean() -> Any:
    turns.reset()
    yield
    turns.reset()


class Slow(Scripted):
    """Lần gọi model đầu chạy một công cụ ngay; lần sau treo (model rất chậm) — để lượt bị ngắt GIỮA chừng, sau một bước đã làm."""

    async def _stream(self, messages: Any, info: Any) -> AsyncIterator[Any]:
        if not self.turns:
            await asyncio.sleep(60)
        async for x in super()._stream(messages, info):
            yield x


class Pause(Scripted):
    """Model trả lời sau nửa giây — lượt còn đang chạy lúc bắt đầu tắt máy chủ nhưng xong trong hạn chờ."""

    async def _stream(self, messages: Any, info: Any) -> AsyncIterator[Any]:
        await asyncio.sleep(0.5)
        async for x in super()._stream(messages, info):
            yield x


class Log:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, ev: dict[str, Any]) -> None:
        self.events.append(ev)

    def done(self) -> dict[str, Any]:
        return next(e for e in self.events if e["type"] == "done")


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _h(p: Principal) -> dict[str, str]:
    return {"X-Dev-User": p.email, "X-Tenant-Id": str(p.tenant_id)}


async def _hold(p: Principal) -> asyncio.Task[Any]:
    """Một lượt "đang chạy" giả của ``p`` (chiếm chỗ đếm)."""
    t = turns.start(asyncio.sleep(30), tenant=str(p.tenant_id), user=str(p.user_id))
    await asyncio.sleep(0)
    return t


async def _release(*tasks: asyncio.Task[Any]) -> None:
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_too_long_question_and_too_big_body_are_refused_before_any_turn_starts(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    sale1 = await who("sale1")
    monkeypatch.setattr(settings, "chat_message_max_chars", 50)
    monkeypatch.setattr(settings, "chat_body_max_bytes", 4096)
    async with _client() as c:
        r = await c.post("/v1/assistant/chat/stream", json={"message": "x" * 51}, headers=_h(sale1))
        assert r.status_code == 422 and "quá dài" in r.json()["detail"] and "tối đa 50" in r.json()["detail"]
        r = await c.post("/v1/assistant/chat/stream", json={"message": "hỏi", "images": [{"media_type": "image/png", "data": "A" * 8000}]},
                         headers=_h(sale1))
        assert r.status_code == 413 and "quá lớn" in r.json()["detail"]                   # khai Content-Length quá trần ⇒ chặn trước khi đệm

        async def chunks() -> AsyncIterator[bytes]:   # không khai Content-Length: đếm khi đọc, quá trần ⇒ dừng đọc
            yield b'{"message": "'
            for _ in range(10):
                yield b"y" * 1000
            yield b'"}'
        r = await c.post("/v1/assistant/chat/stream", content=chunks(), headers={**_h(sale1), "Content-Type": "application/json"})
        assert r.status_code == 413
    assert turns.running() == 0


async def test_concurrent_turns_are_capped_per_person_company_and_process_with_a_retry_hint(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    sale1, sale2 = await who("sale1"), await who("sale2")
    monkeypatch.setattr(settings, "chat_turns_per_person", 1)
    held = [await _hold(sale1)]
    try:
        async with _client() as c:
            r = await c.post("/v1/assistant/chat/stream", json={"message": "Hôm nay tôi gọi ai?"}, headers=_h(sale1))
            assert r.status_code == 429 and r.headers["retry-after"] == "10" and "Bạn đang có 1 lượt" in r.json()["detail"]
            monkeypatch.setattr(settings, "chat_turns_per_company", 2)
            held.append(await _hold(sale2))
            with pytest.raises(turns.Busy) as e:
                turns.admit(str(sale2.tenant_id), "người-khác")                                  # cùng công ty, người thứ ba
            assert e.value.status == 429 and "Công ty đang có 2 lượt" in str(e.value)
            turns.admit("công-ty-khác", "x")                                                     # công ty khác vẫn nhận
            monkeypatch.setattr(settings, "chat_turns_per_process", 2)
            with pytest.raises(turns.Busy) as e:
                turns.admit("công-ty-khác", "x")
            assert e.value.status == 503 and e.value.retry_after == 30
    finally:
        await _release(*held)
    turns.admit(str(sale1.tenant_id), str(sale1.user_id))                                     # lượt xong ⇒ có chỗ lại


async def test_a_turn_over_the_time_cap_stops_and_keeps_what_it_did(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    sale1 = await who("sale1")
    # 2 giây: đủ rộng cho bước đầu (gọi crm.leads) xong cả khi máy đang tải nặng lúc phát hành (0,5 giây từng hụt); lần gọi model sau treo 60 giây nên
    # lượt vẫn bị ngắt GIỮA chừng.
    monkeypatch.setattr(settings, "chat_turn_max_s", 2.0)
    log = Log()
    task = turns.start(chat.chat_turn(sale1, "Lead nào đang mở?", emit=log, model=Slow([[call("crm.leads", stage="open")]])),
                       tenant=str(sale1.tenant_id), user=str(sale1.user_id))
    await asyncio.wait_for(task, 10)
    done = log.done()
    assert done["stopped"] is True and done["interrupted"] == "timeout" and "quá thời gian" in done["text"]
    saved = (await chat.load_thread(sale1, done["thread_id"]))["turns"][-1]                  # type: ignore[index]
    assert saved["interrupted"] == "timeout" and [s["tool"] for s in saved["steps"]] == ["crm.leads"]   # bước đã chạy vẫn còn
    assert turns.running() == 0


async def test_shutdown_waits_for_turns_then_interrupts_the_rest_and_records_it_before_the_pool_closes(who: Who) -> None:
    sale1 = await who("sale1")
    fast, slow = Log(), Log()
    quick = turns.start(chat.chat_turn(sale1, "Chào em", emit=fast, model=Pause(["Chào anh/chị."])),
                        tenant=str(sale1.tenant_id), user=str(sale1.user_id))
    stuck = turns.start(chat.chat_turn(sale1, "Lead nào đang mở?", emit=slow, model=Slow([[call("crm.leads", stage="open")]])),
                        tenant=str(sale1.tenant_id), user=str(sale1.user_id))
    await asyncio.sleep(0.3)   # lượt chậm đã chạy xong bước công cụ, đang chờ model; lượt nhanh còn chờ nửa giây
    finished, cut = await turns.drain(grace=2.0, settle=5.0)
    assert (finished, cut) == (1, 1) and quick.done() and stuck.done()
    assert fast.done()["text"].strip() == "Chào anh/chị." and "interrupted" not in fast.done()
    done = slow.done()
    assert done["interrupted"] == "shutdown" and "khởi động lại" in done["text"]
    saved = (await chat.load_thread(sale1, done["thread_id"]))["turns"][-1]                  # type: ignore[index]
    assert saved["interrupted"] == "shutdown" and saved["steps"] and "khởi động lại" in saved["text"]
    with pytest.raises(turns.Busy) as e:                                                      # đang tắt: không nhận lượt mới
        turns.admit(str(sale1.tenant_id), str(sale1.user_id))
    assert e.value.status == 503


async def test_stop_cancels_only_the_callers_own_turn(who: Who) -> None:
    sale1, sale2 = await who("sale1"), await who("sale2")
    log = Log()
    task = turns.start(chat.chat_turn(sale1, "Lead nào đang mở?", emit=log, model=Slow([[call("crm.leads", stage="open")]])),
                       tenant=str(sale1.tenant_id), user=str(sale1.user_id), stop_key=(str(sale1.user_id), "k1"))
    await asyncio.sleep(0.3)
    async with _client() as c:
        r = await c.post("/v1/assistant/chat/stop", json={"turn_key": "k1"}, headers=_h(sale2))
        assert r.json() == {"stopped": False}                                                  # mã lượt của người khác
        r = await c.post("/v1/assistant/chat/stop", json={"turn_key": "k1"}, headers=_h(sale1))
        assert r.json() == {"stopped": True}
    await asyncio.wait_for(task, 10)
    assert log.done()["stopped"] is True and "interrupted" not in log.done()                  # dừng theo yêu cầu: không phải "bị ngắt"


class Hang(Scripted):
    """Mục ``"HANG"`` trong kịch bản ⇒ lần gọi model đó treo (model rất chậm)."""

    async def _stream(self, messages: Any, info: Any) -> AsyncIterator[Any]:
        if self.turns and self.turns[0] == "HANG":
            await asyncio.sleep(60)
        async for x in super()._stream(messages, info):
            yield x


async def test_shutdown_during_the_continuation_after_a_confirmed_proposal_keeps_the_committed_work_visible(who: Who) -> None:
    sale1 = await who("sale1")
    phone = fresh_phone()
    model = Hang([[call("crm.create_lead", name="Khách chạy tiếp bị ngắt", phone=phone, offering_code="HSR")], "HANG"])
    first = Log()
    out = await chat.chat_turn(sale1, "Thêm khách rồi làm tiếp", emit=first, model=model)
    card = next(e["card"] for e in first.events if e["type"] == "card" and e["card"]["kind"] == "proposal")
    second = Log()
    task = turns.start(chat.decide(sale1, card["id"], "confirm", card["input"], "k-cut-1", emit=second, model=model),
                       tenant=str(sale1.tenant_id), user=str(sale1.user_id))
    for _ in range(150):       # chờ lệnh đã chạy + COMMIT (bằng quyền người bấm) — ngủ cố định 0,5s hụt khi máy bận
        if await owner_val("SELECT count(*) FROM crm.customers WHERE phone = %s", (phone,)) == 1:
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.3)   # phần chạy tiếp đang chờ model (HANG)
    assert await turns.drain(grace=0.1, settle=5.0) == (0, 1) and task.done()
    assert await owner_val("SELECT count(*) FROM crm.customers WHERE phone = %s", (phone,)) == 1   # việc người đã xác nhận: giữ
    assert second.events[-1]["interrupted"] == "shutdown"
    turn = (await chat.load_thread(sale1, out["thread_id"]))["turns"][-1]                          # type: ignore[index]
    assert turn["interrupted"] == "shutdown" and "pending" not in turn and len(turn["decided"]) == 1 and "khởi động lại" in turn["text"]


async def test_lifespan_shutdown_interrupts_running_turns_before_the_pool_closes(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đường tắt máy chủ thật (``http_api.lifespan``): lượt còn chạy được dừng + lưu TRƯỚC khi đóng pool (pool đóng trước thì phần lưu hỏng)."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from barista import http_api, mcp_server

    sale1 = await who("sale1")
    order: list[str] = []
    for k, v in (("migrate_on_start", False), ("leadflow_sweep_s", 0), ("webhook_interval_s", 0), ("files_sweep_s", 0), ("morning_interval_s", 0),
                 ("notify_listen", False), ("work_engine", False), ("shutdown_turn_grace_s", 0.2), ("shutdown_turn_settle_s", 5.0)):
        monkeypatch.setattr(settings, k, v)

    async def opened(*_: Any, **__: Any) -> None:
        order.append("open")

    async def closed() -> None:   # pool thật của bộ test giữ nguyên — chỉ ghi thứ tự
        order.append("close")

    async def idle(*_: Any) -> None:
        await asyncio.sleep(3600)

    @asynccontextmanager
    async def mcp_run() -> AsyncIterator[None]:
        yield

    real_drain = turns.drain

    async def drain(grace: float, settle: float) -> tuple[int, int]:
        order.append("drain")
        return await real_drain(grace, settle)

    monkeypatch.setattr(http_api, "db", SimpleNamespace(open=opened, close=closed))
    monkeypatch.setattr(http_api.events, "run_forever", idle)
    monkeypatch.setattr(http_api.files, "check_config", opened)   # kho tệp của bộ test không có khoá ký — không phải điều đang kiểm
    monkeypatch.setattr(mcp_server, "server", SimpleNamespace(session_manager=SimpleNamespace(run=mcp_run)))
    monkeypatch.setattr(turns, "drain", drain)
    log = Log()
    async with http_api.lifespan(http_api.app):
        task = turns.start(chat.chat_turn(sale1, "Lead nào đang mở?", emit=log, model=Slow([[call("crm.leads", stage="open")]])),
                           tenant=str(sale1.tenant_id), user=str(sale1.user_id))
        await asyncio.sleep(0.3)
    assert order == ["open", "open", "drain", "close"] and task.done()
    turn = (await chat.load_thread(sale1, log.done()["thread_id"]))["turns"][-1]                  # type: ignore[index]
    assert turn["interrupted"] == "shutdown"
