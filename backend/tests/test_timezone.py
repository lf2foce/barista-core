"""MÚI GIỜ CÔNG TY (0169): lưu UTC (timestamptz), tính / hiện theo múi giờ công ty, mặc định Việt Nam; giờ của TIẾN TRÌNH (``TZ``) và cấu hình
múi giờ của máy chủ Postgres không bao giờ đổi kết quả.

Ba nhóm: (1) luật tĩnh — chặn múi Việt Nam viết cứng / giờ địa phương ngầm trong code mới; (2) cùng mẫu logic chạy dưới ``TZ=UTC``,
``TZ=America/New_York``, ``TZ=Asia/Ho_Chi_Minh`` ra kết quả y hệt; (3) công ty đổi múi ⇒ hôm nay / ranh ngày / buổi sáng / phiên DB theo múi đó.
"""

import ast
import io
import os
import time
import tokenize
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
import pytest

from barista import seed
from barista.db import db
from barista.kernel import clock, executor, workhours
from barista.kernel.clock import VN
from barista.modules.schedule import rrule
from tests.conftest import OTHER_DOMAIN, Who, owner_val, q
from tests.module_src import declared, in_pack, py_files, sql_files
from tests.module_src import rel as rel_of

SRC = Path(__file__).resolve().parents[1] / "src" / "barista"
NY = ZoneInfo("America/New_York")

# ``VN`` (múi của LUẬT Việt Nam) chỉ được dùng ở đây. Thêm chỗ mới ⇒ chỗ đó phải là luật / quy định Việt Nam, ghi lý do bên cạnh.
VN_ALLOWED = {
    "kernel/clock.py": "định nghĩa",
    "api/__init__.py": "bề mặt gói công bố VN (múi của luật) — gói dùng cho bộ quy định vn-*",
    "seed.py": "dữ liệu mẫu của công ty mẫu Việt Nam",
    **declared("vn_allowed"),   # file của gói ngoài đang cài: gói tự khai trong core_guards.json của nó (0191)
    "seed_pay.py": "dữ liệu mẫu của công ty mẫu Việt Nam",
    "seed_devmock.py": "dữ liệu mẫu của công ty mẫu Việt Nam (lớp dev đầy đủ, runbook 18)",
}
# Tên múi Việt Nam viết thẳng trong chuỗi (SQL ``AT TIME ZONE '…'``, ZoneInfo("…")) — SQL dùng ``core.tz()``, Python dùng ``clock.zone()``.
LITERAL_ALLOWED = {"kernel/clock.py", "config.py"}
# Migration đã chạy không sửa được (0085): đây là các file có sẵn tên múi trước 0169; file MỚI không được thêm.
MIGRATION_LITERAL_ALLOWED = {"base/000_baseline/00_core.sql", "base/005_ai_budget.sql", "base/045_ledger_posted_on_period_locks.sql",
                             "timekeeping/065_timekeeping.sql", "base/074_company_timezone.sql"}
ZONEINFO_ALLOWED = {"kernel/clock.py"}


def _py_files() -> Iterator[tuple[str, Path]]:
    for p in py_files():   # lõi + gói ngoài đang cài (0191)
        yield rel_of(p), p


def test_vn_zone_only_where_it_means_vietnamese_law() -> None:
    bad = []
    for rel, p in _py_files():
        if rel in VN_ALLOWED:
            continue
        for t in tokenize.generate_tokens(io.StringIO(p.read_text(encoding="utf-8")).readline):
            if t.type == tokenize.NAME and t.string == "VN":
                bad.append(f"{rel}:{t.start[0]}")
    assert not bad, f"múi giờ công ty là clock.zone() (0169); VN chỉ cho luật Việt Nam — {bad}"


def test_no_hard_coded_zone_names_in_code_or_new_migrations() -> None:
    bad = [rel for rel, p in _py_files() if rel not in LITERAL_ALLOWED and "Asia/Ho_Chi_Minh" in p.read_text(encoding="utf-8")]
    root = SRC / "migrations"
    # tên = "<gói>/<file>" cho cả migration của lõi lẫn của gói ngoài đang cài (0191)
    names = [(got[1].removeprefix("migrations/") if (got := in_pack(m)) else m.relative_to(root).as_posix(), m) for m in sql_files()]
    bad += [name for name, m in names if name not in MIGRATION_LITERAL_ALLOWED and "Asia/Ho_Chi_Minh" in m.read_text(encoding="utf-8")]
    assert not bad, f"SQL dùng (x AT TIME ZONE core.tz()), Python dùng clock.zone() — {bad}"


def _naive_calls(tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Gọi phụ thuộc giờ địa phương của tiến trình: ``datetime.now()`` / ``datetime.today()`` / ``date.today()`` / ``utcnow`` /
    ``utcfromtimestamp`` / ``fromtimestamp(x)`` không múi / ``x.astimezone()`` không đối số / ``time.localtime`` / ``time.strftime`` / ``time.mktime``
    / ``ZoneInfo(…)`` ngoài clock."""
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
        nargs = len(n.args) + len(n.keywords)
        if name == "now" and owner == "datetime" and nargs == 0:
            yield n.lineno, "datetime.now() không múi"
        elif name == "today" and owner in ("datetime", "date"):
            yield n.lineno, f"{owner}.today() theo giờ máy"
        elif name in ("utcnow", "utcfromtimestamp", "localtime", "mktime") or (name == "strftime" and owner == "time"):
            yield n.lineno, f"{name} theo giờ máy"
        elif name == "fromtimestamp" and nargs < 2:
            yield n.lineno, "fromtimestamp không múi"
        elif name == "astimezone" and nargs == 0:
            yield n.lineno, "astimezone() không đối số = giờ máy"
        elif name == "ZoneInfo":
            yield n.lineno, "ZoneInfo(…) ngoài clock — dùng clock.zone() / clock.of()"


def test_no_process_local_time_in_code() -> None:
    bad = []
    for rel, p in _py_files():
        for line, why in _naive_calls(ast.parse(p.read_text(encoding="utf-8"))):
            if why.startswith("ZoneInfo") and rel in ZONEINFO_ALLOWED:
                continue
            bad.append(f"{rel}:{line} {why}")
    assert not bad, f"không dựa vào giờ địa phương của tiến trình (0169): {bad}"


def test_cross_company_sweeps_use_each_companys_zone() -> None:
    """Câu ``sweep=`` chạy bằng owner, XUYÊN công ty — không có phiên công ty nên ``core.today()`` / ``core.tz()`` là múi mặc định của bản cài, sai cho
    công ty khác múi. Ngày của từng dòng: ``core.today_of(tenant_id)`` / ``core.tz_of(tenant_id)``."""
    from barista.kernel import registry

    bad = [name for name, sql in registry.SWEEPS.items() if "core.today()" in sql or "core.tz()" in sql]
    assert not bad, bad


async def test_storage_is_absolute_time_only() -> None:
    """Đo 11/11/2026: mọi cột thời gian là timestamptz. Cột ``timestamp`` không múi (ngầm hiểu theo giờ ai?) không được thêm."""
    naive = await owner_val("""SELECT string_agg(table_schema || '.' || table_name || '.' || column_name, ', ') FROM information_schema.columns
                               WHERE data_type = 'timestamp without time zone'
                                 AND table_schema NOT IN ('pg_catalog', 'information_schema', 'dbos', 'public')""")
    assert naive is None, naive


# ── (2) Giờ của tiến trình không đổi kết quả ──

@contextmanager
def process_tz(name: str) -> Iterator[None]:
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def _sample() -> list[Any]:
    """Mẫu logic thời gian thuần: hạn giờ làm việc qua đêm / cuối tuần, lặp lịch, ranh ngày, định dạng — tính bằng múi công ty (mặc định VN)."""
    at = datetime(2026, 11, 13, 16, 30, tzinfo=UTC)   # thứ Sáu 23:30 giờ Việt Nam
    h = workhours.Hours(days=(1, 2, 3, 4, 5), open_min=8 * 60, close_min=17 * 60 + 30)
    rule = rrule.parse("FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4")
    return [
        workhours.add_working_minutes(at, 90, h).isoformat(),
        workhours.next_open(at, h).isoformat(),
        workhours.is_open(at, h),
        [t.isoformat() for t in rrule.starts(rule, clock.day_start(date(2026, 11, 2)).replace(hour=9), upto=at + timedelta(days=30))],
        clock.day_of(at).isoformat(),
        clock.day_start(date(2026, 3, 8)).isoformat(),
        f"{clock.local(at):%d/%m %H:%M}",
    ]


@pytest.mark.parametrize("company", ["Asia/Ho_Chi_Minh", "America/New_York"])
def test_same_results_under_any_process_tz(company: str) -> None:
    with clock.using(company):
        results = {}
        for tz in ("UTC", "America/New_York", "Asia/Ho_Chi_Minh"):
            with process_tz(tz):
                results[tz] = _sample()
    assert results["UTC"] == results["America/New_York"] == results["Asia/Ho_Chi_Minh"], results
    if company == "Asia/Ho_Chi_Minh":   # mặc định của công ty đang có: đúng như trước 0169
        assert results["UTC"][0] == "2026-11-16T09:30:00+07:00" and results["UTC"][4] == "2026-11-13" and results["UTC"][6] == "13/11 23:30"
    else:   # cùng mốc là 11:30 thứ Sáu ở New York: đồng hồ chạy ngay; New York đổi giờ mùa hè 08/03/2026 (lặp lịch giữ giờ đồng hồ)
        assert results["UTC"][1] == "2026-11-13T11:30:00-05:00" and results["UTC"][4] == "2026-11-13"
        assert results["UTC"][5] == "2026-03-08T00:00:00-05:00"


async def test_db_session_zone_ignores_server_default(who: Who) -> None:
    """Kết nối pool mở với múi mặc định của bản cài (không theo postgresql.conf); trong giao dịch công ty = múi công ty."""
    async with db.app.connection() as conn:
        assert (await (await conn.execute("SHOW TimeZone")).fetchone() or {})["TimeZone"] == clock.default_zone().key
    admin = await who("admin")
    async with db.tenant_tx(admin, read_only=True) as conn:
        row = await (await conn.execute("SELECT core.tz() AS tz, core.today() AS d, current_setting('TimeZone') AS s")).fetchone()
        assert row is not None and row["tz"] == row["s"] == "Asia/Ho_Chi_Minh" and clock.zone() == VN and row["d"] == clock.today()


# ── (3) Công ty đổi múi giờ ──

@pytest.fixture
async def ny_company(demo: dict[str, Any]) -> Any:
    """Công ty KHÁC (test) đổi sang New York trong test này, trả lại Việt Nam sau test (các test khác dùng công ty đó với múi mặc định)."""
    admin = await seed.who(demo["other"], "admin", OTHER_DOMAIN)
    r = await executor.run(admin, "admin.set_timezone", {"timezone": "America/New_York"})
    assert r.status == "ok", r.error
    try:
        yield admin
    finally:
        await owner_val("UPDATE core.tenants SET timezone = 'Asia/Ho_Chi_Minh' WHERE id = %s RETURNING 1", (demo["other"],))


async def test_company_zone_drives_today_sql_and_python(ny_company: Any) -> None:
    async with db.tenant_tx(ny_company, read_only=True) as conn:
        row = await (await conn.execute("SELECT core.tz() AS tz, core.today() AS d, now() AS n")).fetchone()
        assert row is not None and row["tz"] == "America/New_York"
        assert clock.zone().key == "America/New_York" and row["d"] == datetime.now(NY).date() == clock.today()
        assert row["n"].utcoffset() == datetime.now(NY).utcoffset()   # mốc đọc ra mang múi công ty (cùng mốc tuyệt đối)
    assert clock.zone() == clock.default_zone()   # ra khỏi giao dịch: về mặc định của bản cài
    page = await q(ny_company, "admin.timezone")
    assert page["rows"][0]["timezone"] == "America/New_York" and page["rows"][0]["default"] == "Công ty đặt"
    m = await owner_val("SELECT timezone FROM core.tenants WHERE id = %s", (ny_company.tenant_id,))
    assert m == "America/New_York"


async def test_two_companies_same_instant_different_days(ny_company: Any, who: Who) -> None:
    """Cùng một mốc tuyệt đối: 05:30 sáng thứ Sáu ở Việt Nam là 17:30 thứ Năm ở New York — ngày của mỗi công ty theo múi của nó."""
    vn_admin = await who("admin")
    instant = "2026-11-12T22:30:00+00:00"
    days = {}
    for p in (vn_admin, ny_company):
        async with db.tenant_tx(p, read_only=True) as conn:
            row = await (await conn.execute("SELECT (%s::timestamptz AT TIME ZONE core.tz())::date AS d", (instant,))).fetchone()
            assert row is not None
            days[p.tenant_id] = (row["d"], clock.day_of(datetime.fromisoformat(instant)))
    assert days[vn_admin.tenant_id] == (date(2026, 11, 13),) * 2 and days[ny_company.tenant_id] == (date(2026, 11, 12),) * 2


async def test_morning_run_is_six_local_per_company(ny_company: Any, demo: dict[str, Any]) -> None:
    from barista.agent import morning

    instant = datetime(2026, 11, 12, 22, 30, tzinfo=UTC)   # 05:30 Việt Nam (chưa tới 6h) · 17:30 New York (đã qua 6h)
    await owner_val("DELETE FROM core.proposal_runs WHERE tenant_id = ANY(%s) RETURNING 1", ([demo["tenant"], demo["other"]],))
    await morning.run_once(instant)
    runs = await owner_val("""SELECT jsonb_object_agg(tenant_id::text, day) FROM core.proposal_runs WHERE tenant_id = ANY(%s)""",
                           ([demo["tenant"], demo["other"]],))
    assert runs == {str(demo["other"]): "2026-11-12"}, runs


async def test_set_timezone_validates_and_is_admin_only(ny_company: Any, who: Who) -> None:
    for bad in ("UTC+7", "+07:00", "ICT", "Etc/GMT-7", "Mars/Olympus", ""):
        r = await executor.run(ny_company, "admin.set_timezone", {"timezone": bad})
        assert r.status == "invalid", (bad, r.status)
    r = await executor.run(ny_company.as_agent(), "admin.set_timezone", {"timezone": "Asia/Tokyo"})
    assert r.status == "denied"   # trợ lý không đổi múi giờ thay người
    r = await executor.run(await who("sale1"), "admin.set_timezone", {"timezone": "Asia/Tokyo"})
    assert r.status == "denied"
    assert "Asia/Singapore" in [o["value"] for o in await q(ny_company, "admin.timezone_options")]


async def test_app_role_can_change_only_its_own_timezone(ny_company: Any, demo: dict[str, Any]) -> None:
    """Quyền ghi của role app trên core.tenants: chỉ cột timezone, chỉ dòng của công ty phiên (RLS); tên múi lạ bị CHECK chặn."""
    async with db.tenant_tx(ny_company) as conn:
        n = (await conn.execute("UPDATE core.tenants SET timezone = 'Asia/Tokyo' WHERE id = %s", (demo["tenant"],))).rowcount
        assert n == 0   # công ty khác: không thấy dòng
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with db.tenant_tx(ny_company) as conn:
            await conn.execute("UPDATE core.tenants SET name = 'Đổi tên' WHERE id = core.tenant()")
    with pytest.raises(psycopg.errors.CheckViolation):
        async with db.tenant_tx(ny_company) as conn:
            await conn.execute("UPDATE core.tenants SET timezone = 'UTC+7' WHERE id = core.tenant()")
    assert await owner_val("SELECT timezone FROM core.tenants WHERE id = %s", (demo["tenant"],)) == "Asia/Ho_Chi_Minh"


def test_manifest_tells_apps_the_company_zone() -> None:
    from barista import workspaces
    from barista.kernel.principal import Principal

    p = Principal(tenant_id=UUID(int=1), user_id=UUID(int=2), packs=frozenset())
    assert workspaces.manifest(p, "Cty", timezone="Europe/London")["tenant"]["timezone"] == "Europe/London"
    assert workspaces.manifest(p, "Cty")["tenant"]["timezone"] == clock.default_zone().key

