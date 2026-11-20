"""LINK CÔNG KHAI (quyết định 0064) — khách bấm link (Zalo), không đăng nhập, làm ĐÚNG một việc trên ĐÚNG một đối tượng.

* Xác thực bằng quyền sở hữu link: mã ngẫu nhiên 256 bit, DB chỉ giữ mã băm SHA-256 (lộ bản sao lưu / nhật ký không lộ link); có hạn; thu hồi được;
  tạo lại = thu hồi link cũ. Mở xem (kể cả trình xem trước của Zalo) không tiêu link.
* ``resolve`` dựng người gọi ``via="link"``: không phải thành viên, không quyền theo vai, không trợ lý AI; executor chỉ cho chạy thao tác khai
  ``public=<loại link>`` (``executor.allowed``), RLS chỉ mở đúng đối tượng qua biến phiên ``app.link_ref`` (chính sách ``p_link`` của bảng đó).
"""

import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from barista.db import db
from barista.kernel.executor import Ctx
from barista.kernel.principal import Principal, edge

MAX_DAYS = 60


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


async def issue(ctx: Ctx, target: str, ref_id: UUID, *, days: int = 0, ttl: timedelta | None = None) -> str:
    """Link mới cho ``ref_id`` (thu hồi link cũ của cùng đối tượng). Trả mã RÕ — chỉ một lần, không lưu. Hạn theo ngày (``days``) hoặc ngắn hơn theo
    giờ (``ttl`` — vd link vào một cuộc gọi); không bao giờ quá ``MAX_DAYS``."""
    life = min(ttl if ttl is not None else timedelta(days=days), timedelta(days=MAX_DAYS))
    if life <= timedelta(0):
        raise ValueError("link phải có hạn dương")
    await revoke(ctx, target, ref_id)
    token = secrets.token_urlsafe(32)
    await ctx.exec("""INSERT INTO core.public_links (token_hash, target, ref_id, expires_at, created_by)
                      VALUES (%s, %s, %s, now() + %s, %s)""", (_hash(token), target, ref_id, life, ctx.p.user_id))
    return token


async def revoke(ctx: Ctx, target: str, ref_id: UUID) -> int:
    return await ctx.exec("UPDATE core.public_links SET revoked_at = now() WHERE target = %s AND ref_id = %s AND revoked_at IS NULL",
                          (target, ref_id))


async def matches(ctx: Ctx, target: str, ref_id: UUID, token: str) -> bool:
    """Mã này có đúng là link đang hiệu lực của đối tượng này không (để thẻ của nhân sự hiện lại link vừa tạo — không tin chuỗi từ app)."""
    return bool(await ctx.val("""SELECT 1 FROM core.public_links WHERE token_hash = %s AND target = %s AND ref_id = %s AND revoked_at IS NULL
                                 AND expires_at > now()""", (_hash(token), target, ref_id)))


async def resolve(token: str) -> Principal | None:
    """Mã link ⇒ người gọi qua link (None = sai / hết hạn / đã thu hồi — không nói lý do nào, tránh dò)."""
    if not 20 <= len(token) <= 100:
        return None
    async with db.owner_tx() as conn:
        cur = await conn.execute("""UPDATE core.public_links SET last_used_at = now() WHERE token_hash = %s AND revoked_at IS NULL
                                    AND expires_at > now() RETURNING tenant_id, target, ref_id""", (_hash(token),))
        row = await cur.fetchone()
    if row is None:
        return None
    return edge(Principal(tenant_id=row["tenant_id"], user_id=None, name="Khách (qua link)", via="link", link_target=row["target"],
                          link_ref=row["ref_id"], ai=False))   # cấp ở cửa HTTP cho request đang chạy (0130) — route của gói chạy executor bằng người này
