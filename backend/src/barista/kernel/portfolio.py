"""Đọc NHIỀU công ty cho một người (màn tổng quan của công ty dịch vụ — quyết định 0062 B / 0063): hỏi cùng một truy vấn ở từng công ty bằng quyền
CỦA CHÍNH NGƯỜI ĐÓ tại công ty đó (nạp như lúc đăng nhập: thành viên đang hoạt động + vai + quyền), gộp ở tầng ứng dụng.

Không có đường đọc xuyên công ty trong database: mỗi lượt là một giao dịch RLS của đúng công ty đó; không phải thành viên ⇒ "no_access" (không đọc gì);
công ty chưa bật gói của truy vấn / vai không có quyền ⇒ trạng thái của executor, không lỗi cả trang. Quyền nạp tuần tự trên MỘT kết nối owner (pool
owner nhỏ), truy vấn chạy song song có giới hạn để không chiếm hết kết nối của người khác.
"""

import asyncio
from dataclasses import replace
from typing import Any
from uuid import UUID

from barista.db import db
from barista.kernel import auth, executor
from barista.kernel.principal import Principal, issued

CONCURRENCY = 4


async def gather(viewer: Principal, tenants: list[tuple[UUID, str]], query: str, inp: dict[str, Any] | None = None,
                 *, concurrency: int = CONCURRENCY) -> list[dict[str, Any]]:
    """Mỗi công ty một dòng ``{tenant_id, name, status, output, error}`` theo đúng thứ tự ``tenants``. Trợ lý hỏi ⇒ mỗi lượt cũng ở chế độ trợ lý."""
    if viewer.user_id is None:
        return [{"tenant_id": t, "name": n, "status": "no_access", "output": None, "error": ""} for t, n in tenants]
    people: dict[UUID, Principal | None] = {}
    async with db.owner.connection() as conn:
        for tid, _ in tenants:
            p = await auth.load(conn, tid, user_id=viewer.user_id)
            people[tid] = issued(replace(p, via=viewer.via)) if p is not None else None
    sem = asyncio.Semaphore(concurrency)

    async def one(tid: UUID, name: str) -> dict[str, Any]:
        p = people[tid]
        if p is None:
            return {"tenant_id": tid, "name": name, "status": "no_access", "output": None, "error": ""}
        if viewer.via == "agent" and not p.ai:   # trợ lý hỏi hộ: công ty đó tắt AI ⇒ dữ liệu không đi qua đây vào model
            return {"tenant_id": tid, "name": name, "status": "ai_off", "output": None, "error": ""}
        async with sem:
            r = await executor.query(p, query, inp or {})
        return {"tenant_id": tid, "name": name, "status": r.status, "output": r.output if r.ok else None, "error": r.error}

    return list(await asyncio.gather(*(one(t, n) for t, n in tenants)))
