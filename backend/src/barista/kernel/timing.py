"""ĐO TỪNG CHẶNG của một request HTTP — tiêu đề ``Server-Timing`` (W3C) trên mọi phản hồi của core (12/11/2026, đo "mở dòng chậm").

Chặng (mili giây, CỘNG DỒN nếu một request đi qua nhiều lần — vd manifest chạy vài truy vấn):

* ``owner-wait`` — chờ mượn kết nối pool OWNER (3 kết nối, chung mọi lượt xác thực): số lớn ⇒ pool kín, request xếp hàng.
* ``session``    — tra phiên đăng nhập (``core.sessions``).
* ``principal``  — nạp quyền người gọi (``auth.load``: vai, quyền, đội, gói, từ vựng, hạng AI).
* ``app-wait``   — chờ mượn kết nối pool APP (giao dịch nghiệp vụ).
* ``claims``     — mở claims phiên + múi giờ công ty ở đầu mỗi giao dịch (``db.tenant_tx``).
* ``handler``    — riêng hàm xử lý của TRUY VẤN (SQL + dựng trang của module).
* ``executor``   — cả lượt executor (truy vấn / lệnh), đã gồm ``app-wait`` + ``claims`` + ``handler``; phần còn lại = kiểm quyền, lọc nút, đổi từ vựng.
* ``json``       — dựng thân JSON của kết quả.
* ``app``        — tổng ở core, từ lúc nhận request tới lúc gửi tiêu đề phản hồi (luồng SSE / NDJSON: chỉ tới lúc mở luồng).

Chỉ thời lượng, không dữ liệu / tên người / lỗi. Trình duyệt hiện ở DevTools › Network › Timing; BFF web nối thêm chặng của nó (``web/src/lib/server/bff.ts``). Tên chặng không trùng khoá module / gói (``tests/test_meta.py``).
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_SPANS: ContextVar[dict[str, float] | None] = ContextVar("barista_server_timing", default=None)
HEADER = b"server-timing"


def add(name: str, seconds: float) -> None:
    """Cộng ``seconds`` vào chặng ``name`` của request đang chạy (ngoài request HTTP ⇒ không làm gì)."""
    spans = _SPANS.get()
    if spans is not None:
        spans[name] = spans.get(name, 0.0) + seconds


@contextmanager
def span(name: str) -> Iterator[None]:
    """Đo một khối vào chặng ``name`` (lỗi vẫn ghi thời lượng)."""
    if _SPANS.get() is None:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        add(name, time.perf_counter() - t0)


def header(spans: dict[str, float], total: float) -> bytes:
    parts = [f"{k};dur={v * 1000:.1f}" for k, v in spans.items()] + [f"app;dur={total * 1000:.1f}"]
    return ", ".join(parts).encode()


class ServerTiming:
    """Lớp ASGI ngoài cùng của app: mở sổ chặng cho mỗi request HTTP, gắn ``Server-Timing`` vào tiêu đề phản hồi lúc gửi."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        spans: dict[str, float] = {}
        token = _SPANS.set(spans)
        t0 = time.perf_counter()

        async def timed_send(msg: Any) -> None:
            if msg["type"] == "http.response.start":
                msg = {**msg, "headers": [*msg.get("headers", []), (HEADER, header(spans, time.perf_counter() - t0))]}
            await send(msg)

        try:
            await self.inner(scope, receive, timed_send)
        finally:
            _SPANS.reset(token)
