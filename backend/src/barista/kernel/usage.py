"""MỨC DÙNG: đếm LƯỢT ĐỌC thành công theo (công ty, người, gói, ngày, kênh) để bổ sung cho nhật ký thao tác (0163).

Nhật ký ``core.audit_log`` chỉ có lệnh; truy vấn thành công không vào đó ⇒ người chỉ xem không để lại dấu. Ở đây chỉ ghi SỐ ĐẾM (không nội dung, không tên truy vấn):
``note_query`` chỉ tăng một bộ đếm trong bộ nhớ (không SQL, không chờ), một việc nền ghi cả lô bằng ``upsert`` mỗi ``FLUSH_S`` giây. Mọi lỗi (DB, tham số lạ) bị
nuốt có log — truy vấn của người dùng KHÔNG BAO GIỜ hỏng hay chậm vì bộ đếm; lô ghi hỏng thì bỏ (số đếm là ước lượng sử dụng, không phải sổ tiền).
Chỉ tính người thật (``user_id``) qua kênh ``human`` / ``agent``; hệ thống, link công khai, app từ xa không tính.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date
from uuid import UUID

from barista.db import db
from barista.kernel import clock, registry
from barista.kernel.principal import Principal

log = logging.getLogger(__name__)
FLUSH_S = 10.0
MAX_PENDING = 20_000   # trần số khoá chờ ghi (bảo vệ bộ nhớ khi DB chết lâu): quá trần ⇒ bỏ số đếm mới, không bỏ truy vấn

type Key = tuple[UUID, date, str, UUID, str]   # (công ty, ngày, gói, người, kênh)
_pending: dict[Key, int] = defaultdict(int)
_lock: asyncio.Lock | None = None
_armed = False
_tasks: set[asyncio.Task[int]] = set()


def note_query(p: Principal, module: str) -> None:
    """Gọi sau một truy vấn THÀNH CÔNG. Không ném, không chờ."""
    global _armed
    try:
        if p.system or p.user_id is None or p.via not in ("human", "agent"):
            return
        key: Key = (p.tenant_id, clock.today(), registry.MODULE_PACK.get(module, ""), p.user_id, p.via)
        if key not in _pending and len(_pending) >= MAX_PENDING:
            return
        _pending[key] += 1
        if not _armed and FLUSH_S > 0:
            _armed = True
            asyncio.get_running_loop().call_later(FLUSH_S, _kick)
    except Exception:   # bộ đếm không được làm hỏng truy vấn
        log.exception("không đếm được lượt đọc")


def _kick() -> None:
    global _armed
    _armed = False
    try:
        task = asyncio.get_running_loop().create_task(flush())
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def flush() -> int:
    """Ghi hết số đếm đang chờ (mỗi công ty một giao dịch — công ty hỏng không kéo công ty khác). Trả số khoá đã ghi; không bao giờ ném."""
    global _lock
    _lock = _lock or asyncio.Lock()
    written = 0
    async with _lock:   # cờ "đang ghi dở" cho lần gọi sau: ai gọi flush() xong là mọi lô trước đó đã nằm trong DB
        batch = dict(_pending)
        _pending.clear()
        by_tenant: dict[UUID, list[tuple[date, str, UUID, str, int]]] = defaultdict(list)
        for (tenant, day, pack, user, via), n in batch.items():
            by_tenant[tenant].append((day, pack, user, via, n))
        for tenant, rows in by_tenant.items():
            try:
                async with db.owner_tx() as conn:
                    cur = conn.cursor()
                    await cur.executemany(
                        """INSERT INTO core.usage_reads (tenant_id, day, pack, user_id, via, queries) VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (tenant_id, day, pack, user_id, via) DO UPDATE SET queries = core.usage_reads.queries + EXCLUDED.queries""",
                        [(tenant, d, pk, u, v, n) for d, pk, u, v, n in rows])
                written += len(rows)
            except Exception as e:   # công ty đã xoá, DB bận… ⇒ bỏ lô này
                log.warning("không ghi được mức dùng của một công ty (bỏ lô %d dòng): %s", len(rows), type(e).__name__)
    return written
