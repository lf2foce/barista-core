"""Chỉ mục từng phần của nhật ký thao tác (migration 099): ba truy vấn "hiếm trên bảng lớn" của màn Sức khoẻ dữ liệu / Tổng quan điều hành / Hành động của trợ lý
phải ĐỌC được chỉ mục — nếu ai đổi chữ điều kiện của truy vấn (``modules/admin.py``) mà quên chỉ mục, Postgres quay lại đọc cả bảng (~3 giây sau một đợt nạp lịch
sử ~520.000 dòng nhật ký) và test này đỏ. Ép planner bỏ đọc tuần tự để thấy nó CÓ THỂ dùng chỉ mục (bảng test nhỏ, planner tự chọn đọc tuần tự là đúng)."""

import re
from pathlib import Path
from typing import Any

from tests.conftest import owner_val

ADMIN = Path(__file__).resolve().parents[1] / "src/barista/modules/admin.py"

QUERIES = {
    "idx_audit_errors": "SELECT count(*) FROM core.audit_log WHERE status = 'error' AND at > now() - interval '7 days'",
    "idx_audit_agent_trail": "SELECT a.id FROM core.audit_log a WHERE (a.via = 'agent' OR a.trace <> '') AND a.at > now() - make_interval(days => 7) "
                             "ORDER BY a.at DESC LIMIT 500",
}


async def test_partial_indexes_exist_and_planner_can_use_them():
    from barista.db import db

    for name, sql in QUERIES.items():
        assert await owner_val("SELECT 1 FROM pg_indexes WHERE schemaname = 'core' AND indexname = %s", (name,)) == 1, name
        async with db.owner.connection() as conn:
            await conn.execute("SET enable_seqscan = off")
            await conn.execute("SET enable_bitmapscan = off")
            explain: Any = conn.execute  # EXPLAIN + str: the driver types want a literal / template
            plan = "\n".join(next(iter(r.values())) for r in await (await explain("EXPLAIN " + sql)).fetchall())
            await conn.execute("RESET enable_seqscan")
            await conn.execute("RESET enable_bitmapscan")
        assert name in plan, (name, plan)


def test_the_queries_in_the_test_are_the_ones_the_screens_run():
    src = re.sub(r"\s+", " ", ADMIN.read_text())
    assert "FROM core.audit_log WHERE status = 'error' AND at > now() - interval '7 days'" in src
    assert "WHERE (a.via = 'agent' OR a.trace <> '') AND a.at > now() - make_interval(days => %s)" in src
