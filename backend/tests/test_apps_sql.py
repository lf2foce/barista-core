"""NGUỒN SQL TỰ DO (0220, thử nghiệm): tắt mặc định; chỉ người quản trị; MỘT câu SELECT chỉ đọc; chỉ bảng của module nghiệp vụ (kiểm bằng kế hoạch chạy
của Postgres); không hàm hệ thống; khối dashboard ghi rõ "SQL tự do". Dữ liệu giả, Postgres thật."""

from collections.abc import AsyncIterator

import pytest_asyncio

from barista.kernel import executor
from tests.conftest import Who, ok, q

COUNT = "SELECT stage, count(*) AS n FROM crm.leads GROUP BY stage"


@pytest_asyncio.fixture
async def sql_on(who: Who) -> AsyncIterator[None]:
    admin = await who("admin")
    await ok(admin, "admin.set_setting", key="apps.freeform_sql", enabled=True)
    try:
        yield
    finally:
        await ok(admin, "admin.set_setting", key="apps.freeform_sql", enabled=False)


async def test_off_by_default(who: Who) -> None:
    r = await executor.query(await who("admin"), "apps.sql_select", {"sql": COUNT})
    assert r.status == "invalid" and "chưa bật" in (r.error or "")


async def test_only_admins_run_one_read_only_select_on_business_tables(who: Who, sql_on: None) -> None:
    admin = await who("admin")
    page = await q(admin, "apps.sql_select", sql=COUNT)
    assert page["total"] == len(page["rows"]) > 0 and page["source"]["kind"] == "sql"
    assert {c["key"]: c["format"] for c in page["columns"]} == {"stage": "text", "n": "number"}
    refused = {
        "UPDATE crm.leads SET stage = 'lost'": "SELECT",
        "SELECT 1; DELETE FROM crm.leads": "chỉ đọc",
        "SELECT * FROM core.session_key": "không được",
        "SELECT key, enabled FROM core.settings": "không được phép",
        "SELECT relname FROM pg_class": "không được phép",
        "SELECT spec FROM apps.items": "không được phép",
        "SELECT pg_sleep(10)": "hàm hệ thống",
        "SELECT set_config('statement_timeout', '0', false)": "hàm hệ thống",
        "WITH x AS (DELETE FROM crm.leads RETURNING 1) SELECT * FROM x": "chỉ đọc",
        # HÀM (rà 14/11): hàm của module / lõi (có thể SECURITY DEFINER, tự đọc bảng mà kế hoạch chạy không liệt kê) ⇒ chặn theo danh sách ĐƯỢC PHÉP
        "SELECT ledger.check_balanced()": "gọi hàm",
        "SELECT core.tenant()": "gọi hàm",
        "SELECT md5(stage) FROM crm.leads": "gọi hàm",
        'SELECT "ledger"."check_balanced"()': "ngoặc kép",
        "SELECT $$x$$": "ngoặc kép",
        # cột bí mật (băm / token…) — kể cả qua SELECT *
        "SELECT inputs_hash FROM pay.slips": "cột bí mật",
        "SELECT * FROM pay.slips": "cột bí mật",
    }
    for text, why in refused.items():
        r = await executor.query(admin, "apps.sql_select", {"sql": text})
        assert r.status == "invalid" and why in (r.error or ""), (text, r.status, r.error)
    ok_fns = await q(admin, "apps.sql_select", sql="SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS m, count(*) AS n, "
                                                "round(avg(length(stage)), 1) AS l FROM crm.leads WHERE stage IN ('lead', 'deal') GROUP BY 1")
    assert ok_fns["total"] >= 0                                                       # hàm sẵn có trong danh sách + từ khoá có ngoặc vẫn chạy
    bad = await executor.query(admin, "apps.sql_select", {"sql": "SELECT no_such_col FROM crm.leads"})
    assert bad.status == "invalid" and "no_such_col" not in (bad.error or "")          # không đưa nguyên thông báo Postgres
    for local in ("giamdoc", "sale1"):
        r = await executor.query(await who(local), "apps.sql_select", {"sql": COUNT})
        assert r.status == "denied", (local, r.status, r.error)


async def test_a_dashboard_block_can_use_sql_and_says_so(who: Who, sql_on: None) -> None:
    admin = await who("admin")
    app = await ok(admin, "apps.create", title="Thử SQL tự do")
    await ok(admin, "apps.add_block", app_id=app["app_id"], page="main",
             block={"kind": "chart", "title": "Lead theo chặng (SQL)", "chart": "bar", "by": "stage", "measure": "sum:n", "source": {"sql": COUNT}})
    page = await q(admin, "apps.open", app_id=app["app_id"])
    chart = next(b for b in page["blocks"] if b.get("type") == "chart")
    assert chart["source"]["kind"] == "sql" and sum(v or 0 for v in chart["series"][0]["values"]) > 0
    tables = await q(admin, "apps.sql_tables")
    names = {r["table"] for r in tables["rows"]}
    assert "crm.leads" in names and "core.users" in names and "core.session_key" not in names and "core.settings" not in names
