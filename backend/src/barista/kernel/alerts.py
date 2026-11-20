"""BÁO LỖI HỆ THỐNG CHO NHÀ VẬN HÀNH (ROADMAP A4) — người làm / vận hành phần mềm, không phải công ty khách.

Lỗi hệ thống là lỗi của phần mềm (lỗi lập trình, hạ tầng), nên báo về MỘT kênh của nhà vận hành cho mọi công ty: cấu hình triển khai
(``BARISTA_ALERT_TELEGRAM_TOKEN`` + ``BARISTA_ALERT_TELEGRAM_CHAT``), không nằm trong app — công ty khách không thấy lỗi của công ty khác. Không đặt
⇒ tắt. Mỗi (thao tác, loại lỗi) tối đa một tin / ``alert_quiet_s`` trên một tiến trình — lỗi lặp không thành bão tin. Tin chỉ có mã công ty,
thao tác, loại + nội dung lỗi rút gọn — không dữ liệu nghiệp vụ. Phía công ty: Nhật ký thao tác + thông báo cho quản trị (sự kiện
``core.system_error``).

LỖI DỒN DẬP (``Burst``, 18/11/2026 — hai đợt 500 hàng loạt vì kết nối pool hỏng mà không ai biết): mọi lỗi hệ thống của executor (``operator``) và mọi
phản hồi HTTP 5xx / lỗi chưa bắt (lớp ASGI ``ErrorWatch``) đếm vào cửa sổ trượt ``alert_burst_window_s`` (ô 10 giây). Vượt ngưỡng — ≥
``alert_burst_errors`` lỗi, hoặc ≥ ``alert_burst_ratio`` số yêu cầu khi cửa sổ có ≥ ``alert_burst_min_requests`` yêu cầu — ⇒ MỘT tin (tên bản cài, số
lỗi, loại lỗi nhiều nhất, thời điểm theo múi mặc định của bản cài), tối đa một tin / ``alert_burst_quiet_s``; dưới ngưỡng liên tục ``alert_burst_calm_s``
⇒ tin "đã hết lỗi". Trong lúc dồn dập, tin từng lỗi (``operator``) im — tin tổng thay cho bão tin. Tin KHÔNG mang nội dung yêu cầu, đường dẫn, mã công ty
hay chữ của lỗi — chỉ tên loại lỗi (``OperationalError``, ``HTTP 502``…). Đếm theo tiến trình (container api chạy một tiến trình uvicorn); kênh chưa
cấu hình ⇒ vẫn đếm và ghi log cảnh báo, chỉ không gửi. ``/health`` không tính là yêu cầu."""

import asyncio
import logging
import math
import time
from collections import Counter, deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx

from barista.config import settings
from barista.kernel import clock

log = logging.getLogger(__name__)
_last: dict[str, float] = {}
_tasks: set[asyncio.Task[None]] = set()


def _configured() -> bool:
    return bool(settings.alert_telegram_token and settings.alert_telegram_chat)


def operator(tenant_id: UUID, command: str, kind: str, message: str) -> None:
    """Gửi nền (không chặn lượt đang lỗi); kênh chưa cấu hình ⇒ không làm gì. Luôn đếm vào ``burst`` (trong request HTTP: gắn loại lỗi cho request)."""
    note_error(kind)
    if not _configured() or burst.firing:   # đang dồn dập: tin tổng của ``burst`` thay cho từng tin
        return
    key, now = f"{command}:{kind}", time.monotonic()
    if now - _last.get(key, float("-inf")) < settings.alert_quiet_s:
        return
    _last[key] = now
    _spawn(f"Barista {settings.release[:12]} — lỗi hệ thống\n{command}: {kind}\nCông ty {tenant_id}")   # KHÔNG kèm nội dung lỗi (``message``): có thể chứa dữ liệu khách — chi tiết ở nhật ký máy chủ


def notice(tenant_id: UUID, key: str, text: str) -> None:
    """Tin THƯỜNG (không phải lỗi) cho nhà vận hành — vd công ty xin mở gói (0163). Cùng kênh + luật im lặng như ``operator`` (một tin / ``alert_quiet_s``
    cho mỗi ``key``); chưa cấu hình kênh ⇒ không làm gì (yêu cầu vẫn nằm ở ``core.pack_requests`` cho nhà cung cấp đọc)."""
    if not _configured():
        return
    now = time.monotonic()
    if now - _last.get(f"notice:{key}", float("-inf")) < settings.alert_quiet_s:
        return
    _last[f"notice:{key}"] = now
    _spawn(f"Barista {settings.release[:12]}\n{text[:400]}\nCông ty {tenant_id}")


def _spawn(text: str) -> None:
    try:
        task = asyncio.get_running_loop().create_task(_send(text))
    except RuntimeError:   # ngoài vòng sự kiện (lệnh dòng lệnh) ⇒ chỉ log
        log.warning("không gửi được tin cho nhà vận hành: không có vòng sự kiện")
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _send(text: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(f"https://api.telegram.org/bot{settings.alert_telegram_token}/sendMessage",
                                  json={"chat_id": settings.alert_telegram_chat, "text": text})
            r.raise_for_status()
    except Exception as e:   # báo lỗi hỏng không được làm hỏng thêm gì — chỉ ghi log (không in token)
        log.warning("không gửi được báo lỗi cho nhà vận hành: %s", type(e).__name__)


# ── Lỗi dồn dập ──

BUCKET_S = 10.0


@dataclass
class _Request:
    """Loại lỗi executor báo trong một request HTTP. ``open`` tắt khi request xong: việc nền tách từ request (chép ngữ cảnh) lỗi về sau ⇒ đếm riêng."""
    kinds: list[str] = field(default_factory=list)
    open: bool = True


_REQUEST: ContextVar[_Request | None] = ContextVar("barista_alert_request", default=None)
QUIET_PATHS = frozenset({"/health"})   # kiểm sống của Swarm / Traefik — không phải yêu cầu của người dùng


def instance_name() -> str:
    return settings.alert_instance or urlparse(settings.public_api_url).hostname or "Barista"


def _clock(mono: float) -> str:
    """Giờ (múi mặc định của bản cài — ``BARISTA_DEFAULT_TIMEZONE``) của một thời điểm ``time.monotonic``."""
    wall = time.time() - (time.monotonic() - mono)
    return datetime.fromtimestamp(wall, clock.default_zone()).strftime("%H:%M %d/%m")


@dataclass
class Burst:
    """Cửa sổ trượt theo ô ``BUCKET_S`` giây: mỗi ô [bắt đầu, số yêu cầu, số lỗi, đếm loại lỗi]. Hàm nhận ``now`` (monotonic) để test giả thời gian."""
    buckets: deque[list[Any]] = field(default_factory=deque)
    firing: bool = False
    since: float = 0.0               # lúc bắt đầu đợt dồn dập
    peak: int = 0                    # số lỗi lớn nhất trong cửa sổ của đợt
    announced: bool = False          # đợt này đã gửi tin "dồn dập" (chưa gửi — do im lặng — thì cũng không gửi "đã hết")
    calm_from: float | None = None
    last_sent: float = -math.inf

    def _bucket(self, now: float) -> list[Any]:
        start = now - now % BUCKET_S
        if not self.buckets or self.buckets[-1][0] != start:
            self.buckets.append([start, 0, 0, Counter()])
        self._trim(now)
        return self.buckets[-1]

    def _trim(self, now: float) -> None:
        while self.buckets and self.buckets[0][0] <= now - settings.alert_burst_window_s:
            self.buckets.popleft()

    def record(self, now: float, *, request: bool, kind: str | None) -> None:
        b = self._bucket(now)
        b[1] += int(request)
        if kind:
            b[2] += 1
            b[3][kind] += 1

    def stats(self, now: float) -> tuple[int, int, Counter[str]]:
        self._trim(now)
        kinds: Counter[str] = Counter()
        for b in self.buckets:
            kinds.update(b[3])
        return sum(b[1] for b in self.buckets), sum(b[2] for b in self.buckets), kinds

    def over(self, requests: int, errors: int) -> bool:
        return errors >= settings.alert_burst_errors or (
            errors > 0 and requests >= settings.alert_burst_min_requests and errors / requests >= settings.alert_burst_ratio)

    def check(self, now: float) -> str | None:
        """Tin cần gửi lúc ``now`` (hoặc None): vào đợt dồn dập (tối đa một tin / ``alert_burst_quiet_s``), hết đợt (dưới ngưỡng liên tục ``calm_s``)."""
        requests, errors, kinds = self.stats(now)
        window = round(settings.alert_burst_window_s / 60)
        if self.over(requests, errors):
            self.calm_from = None
            if not self.firing:
                self.firing, self.since, self.peak, self.announced = True, now, 0, False
                log.warning("lỗi hệ thống dồn dập: %d lỗi / %d phút (%d yêu cầu)", errors, window, requests)
            self.peak = max(self.peak, errors)
            if now - self.last_sent < settings.alert_burst_quiet_s:
                return None
            self.last_sent, self.announced = now, True
            kind, n = kinds.most_common(1)[0]
            share = f" ({errors * 100 // requests}% của {requests} yêu cầu)" if requests else ""
            return (f"⚠ {instance_name()} — lỗi hệ thống dồn dập\n{errors} lỗi / {window} phút{share}\n"
                    f"Loại nhiều nhất: {kind} ({n})\nTừ {_clock(self.since)} ({clock.zone_label(clock.default_zone())}) · bản {settings.release[:12]}")
        if not self.firing:
            return None
        if self.calm_from is None:
            self.calm_from = now
        if now - self.calm_from < settings.alert_burst_calm_s:
            return None
        self.firing = False
        log.warning("hết lỗi hệ thống dồn dập (đỉnh %d lỗi / %d phút)", self.peak, window)
        if not self.announced:
            return None
        return (f"✓ {instance_name()} — đã hết lỗi dồn dập\n{_clock(self.since)} → {_clock(self.calm_from)} ({clock.zone_label(clock.default_zone())}), "
                f"đỉnh {self.peak} lỗi / {window} phút")


burst = Burst()


def _after(now: float) -> None:
    if (text := burst.check(now)) and _configured():
        _spawn(text)


def note_error(kind: str) -> None:
    """Một lỗi hệ thống. Trong request HTTP ⇒ gắn loại cho request (``ErrorWatch`` đếm MỘT lần khi request xong); ngoài request (việc nền) ⇒ đếm ngay."""
    req = _REQUEST.get()
    if req is not None and req.open:
        req.kinds.append(kind)
        return
    now = time.monotonic()
    burst.record(now, request=False, kind=kind)
    _after(now)


def note_request(kind: str | None) -> None:
    now = time.monotonic()
    burst.record(now, request=True, kind=kind)
    _after(now)


async def watch_forever(interval_s: float = 30.0) -> None:
    """Nhịp kiểm (lifespan): không còn yêu cầu nào tới vẫn nhận ra "đã hết lỗi" / lỗi của việc nền."""
    while True:
        await asyncio.sleep(interval_s)
        _after(time.monotonic())


class ErrorWatch:
    """Lớp ASGI: mỗi request HTTP (trừ ``QUIET_PATHS``) đếm một lần — lỗi khi phản hồi 5xx, lỗi chưa bắt, hoặc executor báo lỗi hệ thống trong request.
    Loại lỗi = tên lớp lỗi đầu tiên / ``HTTP <mã>``. Người dùng đóng kết nối (huỷ) không phải lỗi."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") in QUIET_PATHS:
            await self.inner(scope, receive, send)
            return
        req = _Request()
        token = _REQUEST.set(req)
        status = 0

        async def watched(msg: Any) -> None:
            nonlocal status
            if msg["type"] == "http.response.start":
                status = int(msg.get("status", 0))
            await send(msg)

        try:
            await self.inner(scope, receive, watched)
        except Exception as e:
            req.kinds.append(type(e).__name__)
            raise
        finally:
            req.open = False
            _REQUEST.reset(token)
            note_request(req.kinds[0] if req.kinds else f"HTTP {status}" if status >= 500 else None)
