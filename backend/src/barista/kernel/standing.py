"""TƯ CÁCH CỦA NGƯỜI GỌI TRONG LUỒNG DÀI (review 12/11 F1, ADR 0193): lượt chat (model gọi nhiều công cụ trong một lượt), phiên giọng nói (một WebSocket
nhiều lượt) giữ người gọi nạp lúc BẮT ĐẦU. Khoá thành viên / đổi vai / đổi quyền / đăng xuất (thu hồi phiên) giữa chừng thì từ lần dùng quyền kế tiếp
luồng phải dừng — không đọc / ghi thêm bằng quyền đã cấp trước lúc thu hồi.

Cách kiểm RẺ, dùng lại cơ chế của kênh tức thì (``web.Listener``, 0115): lệnh quản trị và đăng xuất báo ``barista_auth`` (NOTIFY) ⇒ ``live.hub``
tăng số đếm của công ty. Trước MỖI lần dùng quyền, ``Standing.check`` so số đếm (trong bộ nhớ, không SQL); chỉ khi số đếm đổi hoặc đã quá ``TTL`` giây
từ lần nạp trước mới nạp lại người gọi từ DB (``auth.load`` + phiên còn hiệu lực) — một lần nạp trên pool chủ, không phải mỗi lần gọi công cụ.
Mất kết nối LISTEN thì ``TTL`` là giới hạn trên của độ trễ. Người gọi mới khác người gọi đang giữ ở bất cứ điều gì quyết định quyền ⇒ ``Revoked``:
luồng dừng (công cụ không chạy), người dùng hỏi lại thì lượt mới nạp quyền mới.
"""

import asyncio
import time
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from barista.db import db
from barista.kernel import auth, live, sessions
from barista.kernel.principal import Principal

TTL = 15.0   # giây — quá chừng này từ lần nạp trước thì nạp lại dù không có tín hiệu (phiên hết hạn theo giờ, mất kết nối LISTEN, công tắc AI đổi ngoài app)

GONE = "Tài khoản của bạn không còn hoạt động trong công ty này (hoặc phiên đăng nhập đã bị thu hồi) — trợ lý đã dừng, không làm thêm gì."
CHANGED = "Quyền của bạn vừa thay đổi — trợ lý dừng lượt này để không làm tiếp bằng quyền cũ. Hỏi lại để trợ lý dùng quyền mới."
AI_OFF = "Công ty vừa tắt trợ lý AI — trợ lý đã dừng."


class Revoked(Exception):
    """Người gọi của luồng dài đã bị thu hồi / đổi quyền — câu cho người đọc ở ``str(e)``."""


def _authority(p: Principal) -> tuple[Any, ...]:
    """Những gì quyết định người này LÀM / THẤY được gì: vai, ma trận quyền (phạm vi + hành động), đội, gói đang bật, công tắc AI. Hạn mức AI KHÔNG ở
    đây: hết hạn mức giữa lượt ⇒ lượt đang chạy xong, lượt sau bị từ chối (0092 / 0193)."""
    return (p.tenant_id, p.user_id, p.email, p.role, dict(p.grants), p.home_team, p.member_teams, p.managed_teams, p.packs, p.ai)


class Standing:
    """``session``: token phiên đăng nhập của request mở luồng (``Authorization: Bearer``) — có thì kiểm cả phiên chưa bị thu hồi / hết hạn; đăng nhập
    dev / việc nền không có phiên ⇒ chỉ kiểm thành viên + quyền. ``epoch``: số đếm đọc TRƯỚC khi nạp người gọi ở cửa HTTP (đổi quyền xen giữa lúc nạp ⇒
    lần kiểm đầu thấy lệch và nạp lại); không truyền ⇒ số đếm lúc dựng."""

    def __init__(self, p: Principal, *, session: str | None = None, epoch: int | None = None) -> None:
        self.p = p
        self.current = p   # người gọi mới nhất đã kiểm (cùng quyền với ``p``; hạn mức AI / model của hạng có thể mới hơn)
        self.session = session
        self.epoch = live.hub.auth_epoch(str(p.tenant_id)) if epoch is None else epoch
        self.at = time.monotonic()
        self.revoked = ""
        self.lock = asyncio.Lock()
        self.reloads = 0   # số lần nạp lại từ DB (đo chi phí — test)

    @property
    def watched(self) -> bool:
        """Người thật (không phải quyền hệ thống / người qua link)."""
        return not self.p.system and self.p.user_id is not None and self.p.link_ref is None

    async def check(self, *, force: bool = False) -> Principal:
        """Người gọi còn nguyên tư cách ⇒ trả người gọi mới nhất; không ⇒ ``Revoked`` (lần sau cũng vậy — luồng đã dừng là dừng)."""
        if self.revoked:
            raise Revoked(self.revoked)
        if not self.watched:
            return self.current
        tenant = str(self.p.tenant_id)
        if not force and live.hub.auth_epoch(tenant) == self.epoch and time.monotonic() - self.at <= TTL:
            return self.current
        async with self.lock:   # công cụ XEM chạy song song: một lần nạp cho cả nhóm
            if self.revoked:
                raise Revoked(self.revoked)
            epoch = live.hub.auth_epoch(tenant)
            if not force and epoch == self.epoch and time.monotonic() - self.at <= TTL:
                return self.current
            fresh = await self._load()
            self.reloads += 1
            self.epoch, self.at = epoch, time.monotonic()
            reason = GONE if fresh is None else AI_OFF if not fresh.ai and self.p.ai else CHANGED if _authority(fresh) != _authority(self.p) else ""
            if reason:
                self.revoked = reason
                raise Revoked(reason)
            assert fresh is not None
            self.current = fresh
            return fresh

    async def _load(self) -> Principal | None:
        async with db.owner.connection() as conn:
            if self.session is not None and await sessions.alive(conn, self.session) != self.p.user_id:
                return None
            return await auth.load(conn, self.p.tenant_id, user_id=self.p.user_id)


def epoch_of(tenant: str | None) -> int:
    """Số đếm "quyền đã đổi" của công ty trong tiêu đề ``X-Tenant-Id`` — đọc TRƯỚC khi nạp người gọi ở cửa HTTP (như ``web.require_caller``)."""
    try:
        return live.hub.auth_epoch(str(UUID(tenant or "")))
    except ValueError:
        return live.hub.auth_epoch("")


def for_request(headers: Mapping[str, str], p: Principal, epoch: int) -> Standing:
    """Tư cách của luồng một request HTTP / WebSocket mở: kèm token phiên đăng nhập (``Authorization: Bearer``) để kiểm cả việc phiên bị thu hồi."""
    bearer = headers.get("authorization", "")
    return Standing(p, session=bearer[7:].strip() or None if bearer[:7].lower() == "bearer " else None, epoch=epoch)
