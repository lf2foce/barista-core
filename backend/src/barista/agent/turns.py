"""LƯỢT TRỢ LÝ CHẠY TÁCH KHỎI HTTP (review 12/11 F5 / F6, ADR 0200).

Lượt chat / chạy tiếp sau khi quyết đề xuất / ghi chú hiện trường chạy trong một tác vụ nền: người hỏi đóng app giữa chừng thì lượt vẫn chạy xong và được
lưu. Ở đây là phần QUẢN LÝ các tác vụ đó trong tiến trình:

* NHẬN VIỆC: trước khi mở lượt, đếm lượt đang chạy của người hỏi / công ty / cả tiến trình (``admit``) — vượt ⇒ ``Busy`` (429 người / công ty, 503
  tiến trình hoặc máy chủ đang tắt) kèm câu tiếng Việt + số giây nên chờ. Mức là cấu hình của bản cài (``settings.chat_*``), không theo từng khách.
* THỜI GIAN: mỗi lượt có trần ``chat_turn_max_s`` — quá ⇒ huỷ với lý do ``timeout``.
* DỪNG: người bấm "Dừng" (``stop``, chỉ lượt của chính họ) ⇒ lý do ``stopped``.
* TẮT MÁY CHỦ (``drain``): thôi nhận lượt mới, chờ lượt đang chạy xong trong hạn; còn chạy ⇒ huỷ với lý do ``shutdown`` rồi chờ thêm một chút cho phần
  lưu "bị ngắt" của lượt — rồi mới đóng pool DB.

Mã bên trong lượt bắt ``CancelledError`` đọc LÝ DO bằng ``reason()`` để lưu đúng câu ("Đã dừng theo yêu cầu" / quá giờ / máy chủ khởi động lại).
Đếm trong bộ nhớ của tiến trình: chạy nhiều bản API song song thì mỗi bản đếm riêng (như ``kernel/ratelimit.py``).
"""

import asyncio
import contextvars
import logging
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

from barista.config import settings

log = logging.getLogger("barista.agent.turns")

STOPPED, TIMEOUT, SHUTDOWN = "stopped", "timeout", "shutdown"


def interrupted_text(why: str) -> str:
    """Câu báo người dùng khi lượt bị ngắt (dòng ``done`` + lượt lưu trong cuộc trò chuyện)."""
    if why == TIMEOUT:
        return (f"Lượt trả lời quá thời gian cho phép ({round(settings.chat_turn_max_s / 60) or 1} phút) nên trợ lý đã dừng. "
                "Phần đã làm vẫn giữ — hỏi tiếp hoặc chia nhỏ câu hỏi.")
    if why == SHUTDOWN:
        return "Máy chủ vừa khởi động lại giữa lượt nên trợ lý đã dừng. Phần đã làm vẫn giữ — hỏi lại để trợ lý làm tiếp."
    return "Đã dừng theo yêu cầu."


class Busy(Exception):
    """Không nhận thêm lượt: ``status`` 429 / 503, ``retry_after`` giây, câu cho người đọc ở ``str(e)``."""

    def __init__(self, status: int, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


@dataclass
class _Turn:
    tenant: str
    user: str
    started: float
    why: list[str] = field(default_factory=list)   # lý do huỷ (đặt TRƯỚC khi huỷ) — mã trong lượt đọc qua ``reason()``
    watchdog: asyncio.TimerHandle | None = None


_live: dict[asyncio.Task[Any], _Turn] = {}
_keyed: dict[tuple[str, str], asyncio.Task[Any]] = {}   # (người, mã lượt app gửi) ⇒ lượt — để người đó bấm "Dừng" đúng lượt của mình
_WHY: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("barista_turn_why", default=None)
closing = False   # máy chủ đang tắt: không nhận lượt mới


def reason() -> str:
    """Trong lượt vừa bị huỷ: vì sao (``stopped`` / ``timeout`` / ``shutdown``). Ngoài lượt quản lý ở đây ⇒ ``stopped``."""
    why = _WHY.get()
    return why[0] if why else STOPPED


def running(*, tenant: str | None = None, user: str | None = None) -> int:
    return sum(1 for task, t in _live.items() if not task.done() and (tenant is None or t.tenant == tenant) and (user is None or t.user == user))


def admit(tenant: str, user: str) -> None:
    """Còn chỗ cho một lượt nữa của người này không — không ⇒ ``Busy``. Gọi TRƯỚC khi mở luồng trả lời."""
    if closing:
        raise Busy(503, "Máy chủ đang khởi động lại — thử lại sau ít giây.", 15)
    if (cap := settings.chat_turns_per_process) and running() >= cap:
        raise Busy(503, "Trợ lý đang quá tải — thử lại sau khoảng 30 giây.", 30)
    if (cap := settings.chat_turns_per_company) and running(tenant=tenant) >= cap:
        raise Busy(429, f"Công ty đang có {cap} lượt trợ lý chạy cùng lúc (mức tối đa) — thử lại sau ít giây.", 10)
    if (cap := settings.chat_turns_per_person) and running(tenant=tenant, user=user) >= cap:
        raise Busy(429, f"Bạn đang có {cap} lượt trợ lý chạy cùng lúc (mức tối đa) — đợi lượt trước xong hoặc bấm Dừng rồi hỏi lại.", 10)


def start(coro: Coroutine[Any, Any, Any], *, tenant: str, user: str, stop_key: tuple[str, str] | None = None) -> asyncio.Task[Any]:
    """Chạy ``coro`` thành một lượt được quản lý (đếm, trần thời gian, dừng theo mã, chờ khi tắt máy chủ)."""
    turn = _Turn(tenant, user, time.monotonic())
    ctx = contextvars.copy_context()
    ctx.run(_WHY.set, turn.why)
    task = asyncio.get_running_loop().create_task(coro, context=ctx)
    _live[task] = turn
    if settings.chat_turn_max_s > 0:
        turn.watchdog = asyncio.get_running_loop().call_later(settings.chat_turn_max_s, _cancel, task, TIMEOUT)
    if stop_key:
        _keyed[stop_key] = task

    def done(t: asyncio.Task[Any]) -> None:
        gone = _live.pop(t, None)
        if gone is not None and gone.watchdog is not None:
            gone.watchdog.cancel()
        if stop_key and _keyed.get(stop_key) is t:
            _keyed.pop(stop_key, None)

    task.add_done_callback(done)
    return task


def _cancel(task: asyncio.Task[Any], why: str) -> bool:
    turn = _live.get(task)
    if task.done() or turn is None or turn.why:   # đã xong / đã huỷ một lần (không huỷ chồng — phần lưu "bị ngắt" đang chạy)
        return False
    turn.why.append(why)
    if why == TIMEOUT:
        log.warning("lượt trợ lý quá %.0f giây — dừng", settings.chat_turn_max_s)
    task.cancel()
    return True


def stop(stop_key: tuple[str, str]) -> bool:
    """Người bấm "Dừng": huỷ lượt mang mã đó CỦA CHÍNH họ (khoá gồm mã người)."""
    task = _keyed.get(stop_key)
    return task is not None and _cancel(task, STOPPED)


async def drain(grace: float, settle: float) -> tuple[int, int]:
    """Tắt máy chủ: thôi nhận lượt mới; chờ tối đa ``grace`` giây cho lượt đang chạy xong; lượt còn chạy ⇒ huỷ (lý do ``shutdown``) và chờ tối đa ``settle``
    giây cho phần lưu "bị ngắt". Trả (số lượt xong tự nhiên, số lượt bị ngắt). Gọi TRƯỚC khi đóng pool DB."""
    global closing
    closing = True
    tasks = [t for t in _live if not t.done()]
    if not tasks:
        return 0, 0
    _, pending = await asyncio.wait(tasks, timeout=max(grace, 0))
    cut = [t for t in pending if _cancel(t, SHUTDOWN)]
    if cut:
        log.warning("tắt máy chủ: %d lượt trợ lý còn chạy sau %.0f giây — dừng và lưu trạng thái bị ngắt", len(cut), grace)
        _, stuck = await asyncio.wait(cut, timeout=max(settle, 0))
        if stuck:
            log.error("tắt máy chủ: %d lượt trợ lý không lưu kịp trạng thái bị ngắt trong %.0f giây", len(stuck), settle)
    return len(tasks) - len(pending), len(cut)


def reset() -> None:
    """Cho test."""
    global closing
    closing = False
    _live.clear()
    _keyed.clear()
