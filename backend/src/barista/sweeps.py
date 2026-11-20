"""Vòng quét định kỳ của các module (hạn xử lý lead — quyết định 0034; hạn giữ căn — 0035; nhắc việc đến hạn — 0044; khoản phải thu
quá hạn — 0057; kỳ sổ sách, phí dịch vụ — 0063).

Bộ điều phối KHÔNG biết module nào: lệnh nội bộ tự khai ``sweep=`` (câu SQL chọn công ty đang có việc tới hạn, nằm trong module — quyết định 0066).
Mỗi công ty một lượt, bằng quyền hệ thống, qua đúng cửa executor. Trạng thái nằm ở bảng nên tiến trình chết / chạy hai bản cũng không sai
(``FOR UPDATE SKIP LOCKED`` + khoá chia lead theo công ty); lượt không có gì xảy ra thì không ghi nhật ký.
"""

import asyncio
import logging
from uuid import UUID

from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.principal import system_principal

log = logging.getLogger("barista.sweeps")


async def sweep_once() -> int:
    """Chạy mọi lệnh đã khai ``sweep=`` cho các công ty câu SQL của lệnh đó chọn. Trả số lượt đã quét."""
    jobs: list[tuple[str, UUID]] = []
    async with db.owner.connection() as conn:
        for command, due in registry.SWEEPS.items():
            cur = await conn.execute(due)   # SQL hằng khai trong module, không ghép dữ liệu ngoài
            jobs += [(command, r["tenant_id"]) for r in await cur.fetchall()]
    for command, tid in jobs:
        r = await executor.run(system_principal(tid), command, {}, quiet=True, background=True)   # lượt việc nền (job_for được — 0144)
        if not r.ok and r.status != "not_found":   # not_found = công ty chưa bật gói đó (0045): im lặng
            log.error("quét %s của công ty %s lỗi: %s", command, tid, r.error)
    return len(jobs)


async def run_forever(interval_s: float) -> None:
    while True:
        try:
            await sweep_once()
        except Exception:   # vòng nền không được chết vì một lượt lỗi — log.exception rồi chạy lượt sau
            log.exception("vòng quét định kỳ lỗi")
        await asyncio.sleep(interval_s)
