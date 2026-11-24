"""Người ↔ đội: MỘT chỗ trả lời ba câu. Ý nghĩa riêng, không dùng thay nhau (quyết định 0062):

- ``home``: đội CHÍNH — đơn vị MẶC ĐỊNH gắn vào hồ sơ người đó tạo / phụ trách, điểm bắt đầu tuyến duyệt "trưởng nhóm".
- ``member_of``: các đội người đó THAM GIA (thấy việc chung chưa ai nhận, được xác nhận mục ``[xác nhận: @ĐỘI]``).
- ``managed``: các đội người đó QUẢN LÝ (làm trưởng) + toàn bộ cây con — phạm vi quyền "team" (RLS đọc qua biến phiên).

Chưa thuộc đội nào (quản trị, tài khoản dịch vụ) là hợp lệ: ``None`` / ``()``, không lỗi. Một người có thể ở nhiều đội, tối đa một đội CHÍNH
(``is_home``, ràng buộc DB). Hồ sơ thuộc đơn vị nào do LỆNH quyết định — hôm nay mặc định đội chính của người phụ trách; đây không phải "cơ sở của nghiệp vụ" (0062: đơn vị phát sinh ≠ đơn vị phụ trách ≠ phạm vi được xem).

Module KHÔNG tự truy vấn ``core.team_members`` để lấy đội của một người (``tests/test_meta.py`` chặn) — gọi ``ctx.home_team`` hoặc
đọc ``ctx.p`` (đã nạp bằng chính các hàm này). Mọi hàm lọc rõ công ty: dùng được cả lúc đăng nhập (kết nối owner, chưa có biến phiên).
"""

from datetime import date, timedelta
from uuid import UUID

import psycopg

from barista.db import Conn
from barista.kernel.errors import Invalid

# SQL của ``member_of`` / ``managed`` (tham số ``t`` công ty, ``u`` người) — ``auth.load`` gửi chung một lượt mạng với các câu khác (pipeline)
MEMBER_OF_SQL = "SELECT team_id FROM core.team_members WHERE tenant_id = %(t)s AND user_id = %(u)s ORDER BY is_home DESC, team_id"
MANAGED_SQL = """WITH RECURSIVE t AS (
                     SELECT tm.team_id AS id FROM core.team_members tm
                     WHERE tm.tenant_id = %(t)s AND tm.user_id = %(u)s AND tm.is_lead
                     UNION
                     SELECT c.id FROM core.teams c JOIN t ON c.parent_id = t.id WHERE c.tenant_id = %(t)s
                 ) SELECT id FROM t ORDER BY id"""


async def member_of(conn: Conn, tenant_id: UUID, user_id: UUID | None) -> tuple[UUID, ...]:
    if user_id is None:
        return ()
    cur = await conn.execute(MEMBER_OF_SQL, {"t": tenant_id, "u": user_id})
    return tuple(r["team_id"] for r in await cur.fetchall())


def home_of(member: tuple[UUID, ...]) -> UUID | None:
    """Quy tắc chọn đội chính từ các đội tham gia — MỘT chỗ: ``member_of`` xếp đội chính lên đầu."""
    return member[0] if member else None


async def home(conn: Conn, tenant_id: UUID, user_id: UUID | None) -> UUID | None:
    return home_of(await member_of(conn, tenant_id, user_id))


async def led(conn: Conn, tenant_id: UUID, user_id: UUID | None) -> tuple[UUID, ...]:
    """Đội người này làm trưởng TRỰC TIẾP (không gồm cây đội con) — việc chung của một đội chỉ gợi ý cho trưởng của chính đội đó."""
    if user_id is None:
        return ()
    cur = await conn.execute("""SELECT tm.team_id FROM core.team_members tm WHERE tm.tenant_id = %s AND tm.user_id = %s AND tm.is_lead
                                ORDER BY tm.team_id""", (tenant_id, user_id))
    return tuple(r["team_id"] for r in await cur.fetchall())


async def managed(conn: Conn, tenant_id: UUID, user_id: UUID | None) -> tuple[UUID, ...]:
    if user_id is None:
        return ()
    cur = await conn.execute(MANAGED_SQL, {"t": tenant_id, "u": user_id})
    return tuple(r["id"] for r in await cur.fetchall())


# ── Cơ cấu có ngày (ADR 0256): ``core.team_members`` là hiện tại, mọi thay đổi của nó được trigger DB chép thành dòng có ngày ở ``core.team_terms``. ──
# Sửa LÙI NGÀY là sửa lịch sử (hoa hồng dựng người hưởng theo cơ cấu tại ngày ký — 0160) ⇒ quá hạn mức phải có lý do, ghi người sửa.
BACKDATE_DAYS = 7


def backdate_reason(valid_from: date, reason: str, today: date) -> str | None:
    """Lý do phải lưu khi lùi ngày quá hạn mức; ``None`` = thiếu lý do (nơi gọi từ chối). Trong hạn mức ⇒ chuỗi rỗng."""
    if valid_from >= today - timedelta(days=BACKDATE_DAYS):
        return ""
    return reason.strip() or None


async def _history(conn: Conn, sql: str, params: tuple[object, ...]) -> None:
    """Hàm lùi ngày của DB từ chối bằng lời cho người (lịch sử ở giữa đã có thay đổi khác…) ⇒ lỗi nhập liệu, không phải lỗi hệ thống."""
    try:
        async with conn.transaction():
            await conn.execute(sql, params)   # type: ignore[arg-type]
    except psycopg.errors.RaiseException as e:
        raise Invalid(e.diag.message_primary or "Không sửa được lịch sử cơ cấu") from None


async def since(conn: Conn, user_id: UUID, team_id: UUID, valid_from: date, reason: str) -> None:
    """Người này ở đội này (với vai trò đang ghi) TỪ ``valid_from``. Gọi SAU khi đã ghi hiện tại vào ``core.team_members`` trong cùng giao dịch."""
    await _history(conn, "SELECT core.team_term_since(%s, %s, %s, %s)", (user_id, team_id, valid_from, reason))


async def until(conn: Conn, user_id: UUID, team_id: UUID, valid_to: date, reason: str) -> None:
    """Người này thôi ở đội / thôi vai trò đó TỪ ``valid_to`` (dòng vừa đóng hôm nay trong cùng giao dịch)."""
    await _history(conn, "SELECT core.team_term_until(%s, %s, %s, %s)", (user_id, team_id, valid_to, reason))
