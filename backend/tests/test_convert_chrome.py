"""Chỗ giới hạn lượt của trình duyệt không giao diện (kernel/convert.py): huỷ tác vụ đang chờ / đang dựng KHÔNG được làm hai trình duyệt chạy cùng lúc — chỗ chỉ nhả khi
trình duyệt thật sự đã thoát. Phần thuần, không cần trình duyệt thật (dùng luồng giả và một tệp chạy giả)."""

import asyncio
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from barista.kernel import convert

PAGE = "<html><head></head><body></body></html>"


class Fake:
    """Luồng dựng giả: ghi số lần chạy đồng thời; ``obey`` ⇒ dừng sớm khi bị báo huỷ (như diệt trình duyệt); không thì chạy hết ``secs`` (trình duyệt chậm thoát)."""

    def __init__(self, secs: float, obey: bool) -> None:
        self.secs, self.obey = secs, obey
        self.live = self.peak = self.started = 0
        self.lock = threading.Lock()

    def __call__(self, exe: str, html: str, mode: list[str], out_name: str, script: str = "", cancel: threading.Event | None = None) -> bytes:
        with self.lock:
            self.live += 1
            self.started += 1
            self.peak = max(self.peak, self.live)
        try:
            end = time.monotonic() + self.secs
            while time.monotonic() < end and not (self.obey and cancel is not None and cancel.is_set()):
                time.sleep(0.01)
            return b"%PDF-fake"
        finally:
            with self.lock:
                self.live -= 1


@pytest.fixture
def fresh_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert, "_chrome_slot", asyncio.Semaphore(1))
    monkeypatch.setattr(convert, "chrome_binary", lambda: "fake-chrome")


@pytest.mark.parametrize("obey", [True, False])
def test_cancelling_a_render_never_lets_a_second_browser_run_at_once(fresh_slot: None, monkeypatch: pytest.MonkeyPatch, obey: bool) -> None:
    fake = Fake(0.6, obey)
    monkeypatch.setattr(convert, "_run_chrome", fake)

    async def go() -> None:
        a = asyncio.create_task(convert.html_to_pdf(PAGE))
        await asyncio.sleep(0.1)
        assert fake.live == 1
        a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await a
        b = asyncio.create_task(convert.html_to_pdf(PAGE))   # vào ngay sau khi A bị huỷ, khi luồng của A có thể CHƯA thoát
        c = asyncio.create_task(convert.html_to_pdf(PAGE))
        assert (await b).startswith(b"%PDF") and (await c).startswith(b"%PDF")

    asyncio.run(go())
    assert fake.peak == 1 and fake.started == 3 and fake.live == 0


def test_cancelling_a_task_still_waiting_for_the_slot_holds_and_releases_nothing(fresh_slot: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake(0.3, True)
    monkeypatch.setattr(convert, "_run_chrome", fake)

    async def go() -> None:
        a = asyncio.create_task(convert.html_to_pdf(PAGE))
        await asyncio.sleep(0.05)
        waiting = asyncio.create_task(convert.html_to_pdf(PAGE))
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert fake.live == 1                           # chỉ A đang chạy; huỷ người chờ không nhả chỗ của A
        await a
        assert not convert._chrome_slot.locked()

    asyncio.run(go())
    assert fake.peak == 1 and fake.started == 1


def test_the_slot_is_released_only_after_the_real_process_group_is_dead(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    exe = tmp_path / "fake-chrome.sh"
    exe.write_text(f"#!/bin/sh\nsleep 60 &\necho $! > {pidfile}\nwait\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(convert.settings, "chrome_path", str(exe))
    monkeypatch.setattr(convert, "_chrome_slot", asyncio.Semaphore(1))

    async def go() -> int:
        a = asyncio.create_task(convert.html_to_pdf(PAGE))
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            await asyncio.sleep(0.05)
        child = int(pidfile.read_text())
        a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await a
        await asyncio.wait_for(convert._chrome_slot.acquire(), 10)   # chỗ chỉ nhả sau khi nhóm tiến trình bị diệt + thu
        return child

    child = asyncio.run(go())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)                                            # tiến trình con của trình duyệt giả cũng đã chết, không mồ côi


def test_a_runner_failure_still_releases_the_slot(fresh_slot: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> bytes:
        raise convert.Unavailable("lỗi giả")

    monkeypatch.setattr(convert, "_run_chrome", boom)

    async def go() -> None:
        with pytest.raises(convert.Unavailable):
            await convert.html_to_pdf(PAGE)
        assert not convert._chrome_slot.locked()

    asyncio.run(go())
