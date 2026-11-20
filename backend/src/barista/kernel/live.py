"""Báo "có thông báo mới" tức thì (quyết định 0022 + 0044): một kết nối LISTEN tới Postgres ⇒ chuyển cho các kết nối SSE của đúng
người + đánh thức bộ gửi đẩy. Tin chỉ mang "công ty:người" — app nhận rồi đọc lại hộp thông báo qua RLS.

Chạy nhiều bản server: mỗi bản tự LISTEN, nên app nối vào bản nào cũng nhận được. Mất kết nối DB ⇒ nối lại sau 5 giây; app vẫn
có hộp thông báo đúng (đọc từ bảng), chỉ chậm hơn tới lần tải lại.
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterator

import psycopg

from barista.kernel import push

log = logging.getLogger("barista.live")
CHANNEL = "barista_notifications"   # payload "công_ty:người" — hộp thông báo đổi
TALK_CHANNEL = "barista_talk"        # payload "thread:<mã>" — luồng trao đổi có tin mới (0046)
# payload "inbox:<công ty>:<người>:<luồng>" — hộp tin của một người có luồng vừa đổi (0115). Kênh theo người chỉ nhận MÃ luồng và kiểm lại quyền
# đọc luồng đó (RLS người nghe) trước khi báo xuống app ⇒ người đã mất quyền không nhận gì, kể cả mã.
FEED = "inbox:"
# payload "<module>:<công ty>:<mã>" — luồng TÍN HIỆU thời gian thực của một đối tượng có tin mới (vd chuyển offer / ICE của cuộc gọi trực tiếp).
# Chỉ đánh thức; bên nghe đọc lại qua executor bằng quyền của chính mình (RLS) rồi mới gửi xuống.
SIGNAL_CHANNEL = "barista_signal"
RESYNC = "*"   # hàng đợi tràn ⇒ báo "đọc lại cả hộp" (không mang mã nào)
# payload "<công ty>" hoặc "*" — quyền / thành viên / phiên của công ty vừa đổi (lệnh quản trị, đăng xuất) ⇒ các kênh tức thì đang mở nạp lại quyền
# người nghe ngay ở tín hiệu kế tiếp thay vì chờ hết hạn giữ (0115).
AUTH_CHANNEL = "barista_auth"


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[None]]] = {}
        self._feeds: dict[str, set[asyncio.Queue[str]]] = {}
        self._epochs: dict[str, int] = {}
        self.wake = asyncio.Event()

    def auth_epoch(self, tenant_id: str) -> int:
        """Đổi mỗi khi quyền / phiên của công ty (hoặc mọi công ty — "*") có thể vừa đổi; người giữ quyền nạp sẵn so số này để biết phải nạp lại."""
        return self._epochs.get(tenant_id, 0) + self._epochs.get("*", 0)

    def bump_auth(self, tenant_id: str) -> None:
        self._epochs[tenant_id] = self._epochs.get(tenant_id, 0) + 1

    @contextlib.contextmanager
    def subscribe(self, tenant_id: str, user_id: str) -> Iterator[asyncio.Queue[None]]:
        with self.subscribe_key(f"{tenant_id}:{user_id}") as q:
            yield q

    @contextlib.contextmanager
    def subscribe_key(self, key: str) -> Iterator[asyncio.Queue[None]]:
        q = asyncio.Queue[None](maxsize=1)
        self._subs.setdefault(key, set()).add(q)
        try:
            yield q
        finally:
            self._subs[key].discard(q)
            if not self._subs[key]:
                del self._subs[key]

    @contextlib.contextmanager
    def feed(self, key: str) -> Iterator[asyncio.Queue[str]]:
        """Như ``subscribe_key`` nhưng giữ TỪNG mã (luồng nào đổi) — bên nghe kiểm quyền theo từng mã trước khi báo."""
        q = asyncio.Queue[str](maxsize=256)
        self._feeds.setdefault(key, set()).add(q)
        try:
            yield q
        finally:
            self._feeds[key].discard(q)
            if not self._feeds[key]:
                del self._feeds[key]

    def publish(self, key: str, *, push: bool = True, data: str | None = None) -> None:
        for q in self._subs.get(key, ()):
            with contextlib.suppress(asyncio.QueueFull):   # đã có tín hiệu chờ ⇒ một lần tải lại là đủ
                q.put_nowait(None)
        if data is not None:
            for f in self._feeds.get(key, ()):
                try:
                    f.put_nowait(data)
                except asyncio.QueueFull:   # bên nghe chậm: bỏ các mã đang chờ, thay bằng một lần "đọc lại cả hộp"
                    while not f.empty():
                        f.get_nowait()
                    f.put_nowait(RESYNC)
        if push:
            self.wake.set()


hub = Hub()


async def listen_forever(owner_url: str, ready: asyncio.Event | None = None) -> None:
    """``ready``: đặt khi MỌI kênh đã LISTEN (chỉ test dùng — chờ tín hiệu này thay cho đoán bằng giờ; tin NOTIFY trước lúc LISTEN không tới ai)."""
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(owner_url, autocommit=True) as conn:
                await conn.execute(f"LISTEN {CHANNEL}")
                await conn.execute(f"LISTEN {TALK_CHANNEL}")
                await conn.execute(f"LISTEN {SIGNAL_CHANNEL}")
                await conn.execute(f"LISTEN {AUTH_CHANNEL}")
                if ready is not None:
                    ready.set()
                async for n in conn.notifies():
                    if n.channel == AUTH_CHANNEL:
                        hub.bump_auth(n.payload)
                    elif n.channel == SIGNAL_CHANNEL:
                        hub.publish(n.payload, push=False)
                    elif n.channel == TALK_CHANNEL and n.payload.startswith(FEED):
                        key, _, data = n.payload.rpartition(":")
                        hub.publish(key, push=False, data=data)
                    else:
                        hub.publish(n.payload, push=n.channel == CHANNEL)
        except asyncio.CancelledError:
            raise
        except Exception:   # mất kết nối LISTEN không được giết vòng nền — log rồi nối lại
            log.exception("mất kết nối LISTEN — nối lại sau 5 giây")
            await asyncio.sleep(5)


async def push_forever(sender: push.Sender | None, idle_s: float = 30.0) -> None:
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(hub.wake.wait(), idle_s)
        hub.wake.clear()
        try:
            await push.deliver_pending(sender)
        except asyncio.CancelledError:
            raise
        except Exception:   # một lượt đẩy lỗi không được giết vòng nền — log.exception
            log.exception("gửi đẩy lỗi")
