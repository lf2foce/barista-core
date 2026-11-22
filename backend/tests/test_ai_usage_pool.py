"""Ghi lượt dùng AI khi pool kết nối đã kín (review 12/11 F10, ADR 0200).

Tái hiện của Codex: giữ đủ mọi kết nối của pool app trong giao dịch, mỗi giao dịch gọi ghi lượt dùng như một handler gọi model ⇒ trước đây mỗi lần ghi
chờ kết nối thứ hai tới hết hạn rồi BỎ dòng. Nay: bên đang giữ kết nối không mượn thêm (ghi ngay sau khi trả), bên không giữ mà pool kín thì chờ có hạn
rồi xếp hàng ghi lại — không dòng nào mất. Lệnh soạn câu hỏi gọi model NGOÀI giao dịch (bước chuẩn bị của lệnh)."""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import pytest
import pytest_asyncio
from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RunUsage

from barista import db as dbmod
from barista.agent import usage as metering
from barista.config import settings
from barista.db import db
from barista.kernel.principal import Principal
from barista.modules import training
from tests.conftest import Who, ok, owner_val


def _usage() -> RunUsage:
    return RunUsage(requests=1, input_tokens=10, output_tokens=5)


@pytest_asyncio.fixture(autouse=True)
async def _stop_background_flusher() -> AsyncIterator[None]:
    """Diệt task ``metering._flusher`` còn ngủ từ bài test trước (vd sau ``test_a_full_pool_held_by_others``): nếu không huỷ, nó thức dậy giữa
    bài sau (dù bài sau đã monkeypatch ``_retry_forever``) và ``popleft`` dòng đang kiểm ``pending()`` ⇒ lỗi chập chờn."""
    async def _cancel() -> None:
        task = metering._flusher
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if isinstance(metering._flusher, asyncio.Task):
            metering._flusher = None

    await _cancel()
    yield
    await _cancel()


async def _count(category: str) -> int:
    return int(await owner_val("SELECT count(*) FROM core.ai_usage WHERE category = %s", (category,)))


async def _fill(p: Principal, n: int, release: asyncio.Event, body: Any = None) -> list[asyncio.Task[None]]:
    """``n`` tác vụ, mỗi tác vụ giữ một kết nối pool app trong giao dịch tới khi ``release``; ``body(i)`` chạy trong giao dịch trước khi chờ."""
    inside = asyncio.Semaphore(0)

    async def hold(i: int) -> None:
        async with db.tenant_tx(p):
            if body is not None:
                await body(i)
            inside.release()
            await release.wait()

    tasks = [asyncio.create_task(hold(i)) for i in range(n)]
    for _ in range(n):
        await asyncio.wait_for(inside.acquire(), 10)
    return tasks


async def test_every_connection_held_by_handlers_that_meter_inside_their_transaction_loses_no_usage(who: Who) -> None:
    sale1 = await who("sale1")
    cat = f"test:pool:{uuid.uuid4().hex[:6]}"
    release = asyncio.Event()

    async def meter(i: int) -> None:   # đúng mẫu lồng: đang giữ kết nối, gọi ghi lượt dùng
        assert dbmod.holding()
        await metering.record(sale1, surface="work", category=cat, modules=["test"], usage=_usage(), model="fake")

    started = time.monotonic()
    tasks = await _fill(sale1, db.app.max_size, release, meter)   # cả pool kín — mỗi bên giữ một kết nối
    assert time.monotonic() - started < 5                          # không ai chờ kết nối thứ hai
    assert await _count(cat) == 0                                   # chưa ghi khi bên gọi còn giữ kết nối
    release.set()
    await asyncio.gather(*tasks)
    assert await _count(cat) == db.app.max_size                     # trả kết nối xong ⇒ đủ mọi dòng


async def test_a_full_pool_held_by_others_queues_the_row_and_writes_it_later(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    sale1 = await who("sale1")
    cat = f"test:queue:{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(settings, "ai_usage_wait_s", 0.3)
    release = asyncio.Event()
    tasks = await _fill(sale1, db.app.max_size, release)
    try:
        assert not dbmod.holding()
        started = time.monotonic()
        await metering.record(sale1, surface="work", category=cat, modules=["test"], usage=_usage(), model="fake")
        assert time.monotonic() - started < 3 and metering.pending() >= 1   # chờ có hạn rồi xếp hàng — lượt trả lời không treo
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert await metering.flush() and metering.pending() == 0
    assert await _count(cat) == 1


async def test_generating_questions_calls_the_model_outside_any_transaction_even_with_the_pool_nearly_full(who: Who) -> None:
    gd = await who("giamdoc")
    code = f"T{uuid.uuid4().hex[:5].upper()}"
    await ok(gd, "training.upsert_course", code=code, title="Khoá thử pool")
    doc = await owner_val("SELECT id FROM kb.documents WHERE title = 'Chính sách bán hàng và chiết khấu 2026 (mẫu)' LIMIT 1")
    await ok(gd, "training.add_lesson", course_code=code, document_id=doc)
    seen: list[bool] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(dbmod.holding())
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"questions": [
            {"prompt": "Bảo hành bao lâu?", "choices": ["6 tháng", "12 tháng"], "answer": 1, "explanation": "",
             "quote": "Bảo hành 12 tháng kể từ ngày nghiệm thu; phản hồi yêu cầu hỗ trợ trong 4 giờ làm việc."}]})])

    before = await _count("training:quiz")
    release = asyncio.Event()
    others = await _fill(gd, db.app.max_size - 1, release)   # chỉ còn MỘT kết nối rảnh: lệnh cũ giữ nó rồi chờ thêm một cái để ghi lượt dùng
    training.QUIZ_MODEL = FunctionModel(reply, model_name="quiz-fake")
    try:
        out = await asyncio.wait_for(ok(gd, "training.generate_questions", course_code=code, count=3), 20)
    finally:
        training.QUIZ_MODEL = None
        release.set()
        await asyncio.gather(*others)
    assert seen == [False]                                           # model chạy khi lệnh không giữ giao dịch nào
    assert out["created"] == 1
    assert await _count("training:quiz") == before + 1               # lượt dùng đã ghi


async def test_the_model_is_not_called_again_for_a_replayed_key_nor_when_the_read_fails(who: Who) -> None:
    from barista.kernel import executor

    gd = await who("giamdoc")
    code = f"T{uuid.uuid4().hex[:5].upper()}"
    await ok(gd, "training.upsert_course", code=code, title="Khoá thử lặp")
    doc = await owner_val("SELECT id FROM kb.documents WHERE title = 'Chính sách bán hàng và chiết khấu 2026 (mẫu)' LIMIT 1")
    await ok(gd, "training.add_lesson", course_code=code, document_id=doc)
    calls: list[int] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"questions": []})])

    training.QUIZ_MODEL = FunctionModel(reply, model_name="quiz-fake")
    try:
        key = uuid.uuid4().hex
        first = await executor.run(gd, "training.generate_questions", {"course_code": code}, key=key)
        again = await executor.run(gd, "training.generate_questions", {"course_code": code}, key=key)
        missing = await executor.run(gd, "training.generate_questions", {"course_code": "KHONG-CO"})
    finally:
        training.QUIZ_MODEL = None
    assert first.status == "ok" and again.replayed and calls == [1]   # phát lại theo khoá: không gọi model lần hai (khoá không bị "theo người" hai lần)
    assert missing.status == "not_found" and calls == [1]            # khoá học không có: lỗi ở bước đọc, model không chạy


async def test_a_row_the_database_refuses_is_dropped_at_once_and_does_not_block_the_queue(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """19/11/2026 (test trên chập chờn ở lượt phát hành): dòng DB từ chối VĨNH VIỄN (ở đây: công ty không tồn tại ⇒ RLS từ chối) bị bỏ ngay, có log —
    không vào hàng chờ, và dòng hỏng đã nằm sẵn trong hàng chờ không chặn dòng tốt phía sau."""
    from barista.kernel.principal import system_principal

    sale1 = await who("sale1")
    ghost = system_principal(uuid.uuid4())   # công ty không có thật
    cat_bad, cat_good = f"test:ghost:{uuid.uuid4().hex[:6]}", f"test:after:{uuid.uuid4().hex[:6]}"
    before = metering.pending()
    await metering.record(ghost, surface="work", category=cat_bad, modules=["test"], usage=_usage(), model="fake")
    assert metering.pending() == before                                  # bị từ chối ⇒ không xếp hàng
    monkeypatch.setattr(settings, "ai_usage_wait_s", 0.3)

    async def _idle() -> None:   # vòng thử lại nền (đợi 1 s rồi xả) đứng ngoài: máy tải thì nó xả giữa chừng và ``pending()`` rơi dưới 2 — test tự điều khiển lúc xả
        return

    monkeypatch.setattr(metering, "_retry_forever", _idle)
    release = asyncio.Event()
    tasks = await _fill(sale1, db.app.max_size, release)                 # pool kín ⇒ cả hai dòng vào hàng chờ, dòng hỏng đứng TRƯỚC
    try:
        await metering.record(ghost, surface="work", category=cat_bad, modules=["test"], usage=_usage(), model="fake")
        await metering.record(sale1, surface="work", category=cat_good, modules=["test"], usage=_usage(), model="fake")
        assert metering.pending() >= 2
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert await metering.flush()                                         # một lượt: dòng hỏng bị bỏ, dòng tốt được ghi
    assert await _count(cat_good) == 1 and await _count(cat_bad) == 0


async def test_a_full_queue_never_drops_the_row_being_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rà 21/11/2026: vòng xả đang chờ ghi dòng đầu, một lượt khác thấy hàng đầy ⇒ trước đây bỏ ĐÚNG dòng đang ghi, rồi sau khi ghi xong ``popleft`` lấy
    mất một dòng CHƯA ghi (mất lượt, không log). Nay dòng đang ghi rời hàng chờ trước khi chờ: hàng 2 chỗ, đang ghi a, thêm c ⇒ a, b, c đều được ghi."""
    from types import SimpleNamespace

    def row(name: str) -> Any:
        return SimpleNamespace(surface="work", category=name, p=SimpleNamespace(tenant_id="t"), tries=0)

    a, b, c = row("a"), row("b"), row("c")
    written: list[str] = []
    gate, started = asyncio.Event(), asyncio.Event()

    async def fake_write(r: Any, *, wait: float | None = None) -> str:
        if r is a:
            started.set()
            await gate.wait()
        written.append(r.category)
        return "ok"

    monkeypatch.setattr(metering, "_write", fake_write)
    monkeypatch.setattr(metering, "BACKLOG_MAX", 2)
    monkeypatch.setattr(metering, "_backlog", __import__("collections").deque([a, b]))
    monkeypatch.setattr(metering, "_flusher", asyncio.get_running_loop().create_future())   # vòng nền "đang chạy" ⇒ _queue không tự xả
    flushing = asyncio.create_task(metering.flush())
    await started.wait()
    metering._queue(c)            # đúng lúc a đang ghi
    gate.set()
    assert await flushing is True
    assert written == ["a", "b", "c"] and not metering._backlog
