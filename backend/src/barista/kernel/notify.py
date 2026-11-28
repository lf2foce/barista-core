"""Thông báo cho người (quyết định 0044) — module gọi ``ctx.notify(...)`` trong CÙNG giao dịch với việc vừa làm.

* Hoàn lại (đề xuất chạy thử của trợ lý, lệnh lỗi) ⇒ không có thông báo nào: không bao giờ báo việc chưa xảy ra.
* Người gây ra việc không nhận thông báo về chính việc mình làm.
* ``push`` là dòng hiện trên màn khoá, đi qua máy chủ Apple: chỉ loại việc + giờ hạn, KHÔNG tên khách / SĐT / số tiền.
  Nội dung đầy đủ (``title``, ``body``) chỉ đọc được trong app, qua RLS của chính người nhận.
* ``key``: chống báo lặp (vòng quét chạy lại mỗi phút) — cùng người + cùng khoá thì chỉ một thông báo.
"""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from psycopg import errors as pg
from psycopg.types.json import Jsonb

from barista.db import Conn
from barista.kernel import vocab
from barista.kernel.mode import HISTORICAL


async def team_members(conn: Conn, team_id: UUID | None) -> list[UUID]:
    """Thành viên đang hoạt động của một đội (không gồm đội con)."""
    if team_id is None:
        return []
    cur = await conn.execute(
        """SELECT tm.user_id FROM core.team_members tm JOIN core.memberships m ON m.user_id = tm.user_id AND m.tenant_id = tm.tenant_id
           WHERE tm.team_id = %s AND m.active""", (team_id,))
    return [r["user_id"] for r in await cur.fetchall()]


async def send(conn: Conn, users: Iterable[UUID | None], *, kind: str, title: str, body: str = "", push: str = "",
               open: dict[str, Any] | None = None, key: str | None = None, actor: UUID | None = None) -> int:
    """Ghi thông báo cho từng người (bỏ ``actor``, bỏ trùng). Trả số thông báo mới. Nạp lịch sử (0185) ⇒ không báo ai, trả 0 — MỘT nơi chặn cho mọi đường
    gửi (``ctx.notify``, phiếu "xin duyệt" / "tự duyệt" của ``kernel/approvals``, gói gọi thẳng): dữ liệu hệ cũ là tham chiếu, không phải việc vừa xảy ra."""
    if HISTORICAL.get():
        return 0
    n = 0
    if vocab.has_token(f"{title}{body}{push}"):   # nơi GHI cuối cùng: dấu nhãn không bao giờ vào hộp thông báo / tin đẩy (0124)
        cur = await conn.execute("SELECT core.tenant() AS t")
        terms = await vocab.terms(conn, (await cur.fetchone())["t"])   # type: ignore[index]
        title, body, push = (vocab.resolve(x, terms) for x in (title, body, push))
    row = (kind, title[:300], body[:2000], push[:180], Jsonb(open) if open else None, key)
    wanted = list(dict.fromkeys(u for u in users if u is not None and u != actor))
    if not wanted:
        return 0
    # Chỉ thành viên ĐANG HOẠT ĐỘNG nhận thông báo (đúng điều kiện chính sách ghi của bảng): người đã nghỉ còn đứng tên việc / hồ sơ cũ thì bỏ qua —
    # không để một người nhận không hợp lệ làm hỏng cả lượt (vd vòng nhắc hạn: một việc giao cho người nghỉ ⇒ mọi người khác mất nhắc).
    cur = await conn.execute("SELECT user_id FROM core.memberships WHERE active AND user_id = ANY(%s)", (wanted,))
    active = {r["user_id"] for r in await cur.fetchall()}
    for u in (u for u in wanted if u in active):
        # Không dùng ON CONFLICT: có nó, Postgres đòi dòng mới qua cả chính sách ĐỌC ("hộp của mình") ⇒ không gửi được cho người khác.
        # Chống lặp bằng chỉ mục duy nhất + điểm lưu: trùng khoá thì bỏ qua đúng dòng đó.
        try:
            async with conn.transaction():
                await conn.execute("INSERT INTO core.notifications (user_id, kind, title, body, push, open, dedupe_key)"
                                   " VALUES (%s, %s, %s, %s, %s, %s, %s)", (u, *row))
            n += 1
        except pg.UniqueViolation:
            if key is None:
                raise
    return n
