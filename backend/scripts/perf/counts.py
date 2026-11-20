"""Số dòng mỗi vai THẤY qua RLS thật (db.tenant_tx READ ONLY, claims do lõi ký) — so hai bản bằng ``diff``.

uv run python scripts/perf/counts.py scripts/perf/counts.sql
"""

import asyncio
import pathlib
import sys
from typing import Any

import barista.modules  # noqa: F401
from barista import seed
from barista.config import settings
from barista.db import db

ROLES = ["kdd_1", "kdd_0", "gdkhoi1", "qlvung", "giamdoc", "ketoan"]


async def main(sql: Any) -> None:
    await db.open(settings.database_url, settings.owner_database_url, max_size=1)
    try:
        async with db.owner.connection() as c:
            row = await (await c.execute("SELECT id FROM core.tenants ORDER BY created_at LIMIT 1")).fetchone()
        assert row is not None, "DB chưa seed"
        for local in ROLES:
            p = await seed.who(row["id"], local)
            async with db.tenant_tx(p, read_only=True) as conn:
                await conn.execute("SET LOCAL statement_timeout='120s'")
                counts = await (await conn.execute(sql)).fetchone()
                print(local, dict(counts or {}))
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main(pathlib.Path(sys.argv[1]).read_text()))
