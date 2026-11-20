"""Phiên đăng nhập (quyết định 0040): gắn danh tính Google theo `sub`, cấp phiên lưu Postgres, đọc / thu hồi phiên.

Chạy bằng kết nối owner (đăng nhập chưa có công ty để gắn RLS). Quyền làm gì vẫn do ``auth.load`` nạp lại MỖI request theo
công ty đang chọn — phiên chỉ trả lời "đây là ai", không mang quyền.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from barista.config import settings
from barista.db import Conn
from barista.kernel.oidc import GoogleIdentity


class LoginRefused(Exception):
    """Đăng nhập hợp lệ về mặt Google nhưng hệ thống không cho vào (chưa được mời, email đã gắn tài khoản khác…)."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def bind_google(conn: Conn, ident: GoogleIdentity) -> UUID:
    """Tìm người theo (google, sub). Lần đầu: gắn theo email đã được mời. Email đã gắn MỘT sub khác ⇒ từ chối, không tự nối
    (email công ty cấp lại cho người mới không được thừa kế ví hoa hồng / lịch sử của người cũ) — quản trị gỡ liên kết trước."""
    cur = await conn.execute("SELECT user_id FROM core.identities WHERE provider = 'google' AND subject = %s", (ident.subject,))
    row = await cur.fetchone()
    if row:
        await conn.execute("UPDATE core.identities SET last_login_at = now() WHERE provider = 'google' AND subject = %s", (ident.subject,))
        return row["user_id"]
    cur = await conn.execute("SELECT id FROM core.users WHERE email = %s", (ident.email,))
    user = await cur.fetchone()
    if user is None:
        raise LoginRefused("Email này chưa được mời vào công ty nào trên hệ thống — nhờ quản trị thêm bạn")
    cur = await conn.execute("SELECT 1 FROM core.identities WHERE provider = 'google' AND user_id = %s", (user["id"],))
    if await cur.fetchone():
        raise LoginRefused("Email này đã gắn với một tài khoản Google khác — nhờ quản trị gỡ liên kết cũ rồi đăng nhập lại")
    await conn.execute("INSERT INTO core.identities (provider, subject, user_id, email_at_link) VALUES ('google', %s, %s, %s)",
                       (ident.subject, user["id"], ident.email))
    return user["id"]


async def tenants_of(conn: Conn, user_id: UUID) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """SELECT t.id, t.name, r.label AS role_label FROM core.memberships m JOIN core.tenants t ON t.id = m.tenant_id
           JOIN core.roles r ON r.tenant_id = m.tenant_id AND r.key = m.role WHERE m.user_id = %s AND m.active ORDER BY t.name""", (user_id,))
    return await cur.fetchall()


async def open_session(conn: Conn, user_id: UUID, *, provider: str, client: str = "") -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    await conn.execute("INSERT INTO core.sessions (token_hash, user_id, provider, expires_at, client) VALUES (%s, %s, %s, %s, %s)",
                       (_hash(token), user_id, provider, expires, client[:200]))
    return token, expires


SEEN_EVERY_S = 60   # ``last_seen_at`` ghi tối đa mỗi chừng này giây một lần cho một phiên


async def user_for(conn: Conn, token: str) -> UUID | None:
    """Người của phiên còn hiệu lực (chưa thu hồi, chưa hết hạn), hoặc None. Dấu ``last_seen_at`` chỉ GHI khi đã cũ hơn ``SEEN_EVERY_S`` giây: trước
    đây MỌI request ghi một dòng (một lần ghi nhật ký WAL + fsync khi commit, dòng chết cho vacuum) chỉ để giữ dấu không ai đọc theo giây (đo 12/11/2026).
    Phiên hết hạn / thu hồi vẫn bị từ chối NGAY ở request kế tiếp — phần kiểm hiệu lực không đổi."""
    cur = await conn.execute(
        """WITH s AS (SELECT token_hash, user_id, last_seen_at FROM core.sessions
                      WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()),
                touch AS (UPDATE core.sessions c SET last_seen_at = now() FROM s
                          WHERE c.token_hash = s.token_hash AND s.last_seen_at < now() - make_interval(secs => %s))
           SELECT user_id FROM s""", (_hash(token), SEEN_EVERY_S))
    row = await cur.fetchone()
    return row["user_id"] if row else None


async def provider_of(conn: Conn, token: str) -> str | None:
    """Cách đăng nhập của phiên THẬT còn hiệu lực (vd "google"), CHỈ ĐỌC — None nếu không có phiên đó (đăng nhập dev / chọn vai không có phiên ở đây)."""
    cur = await conn.execute("SELECT provider FROM core.sessions WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()", (_hash(token),))
    row = await cur.fetchone()
    return row["provider"] if row else None


async def alive(conn: Conn, token: str) -> UUID | None:
    """Như ``user_for`` nhưng CHỈ ĐỌC (không chạm ``last_seen_at``) — luồng dài kiểm lại phiên đã mở nó còn hiệu lực (``kernel/standing.py``)."""
    cur = await conn.execute("SELECT user_id FROM core.sessions WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()", (_hash(token),))
    row = await cur.fetchone()
    return row["user_id"] if row else None


async def revoke(conn: Conn, token: str) -> None:
    await conn.execute("UPDATE core.sessions SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL", (_hash(token),))
    # kênh tức thì giữ quyền nạp sẵn theo người nghe (0115) ⇒ báo nạp lại ngay, không chờ hết hạn giữ
    await conn.execute("SELECT pg_notify('barista_auth', '*')")
