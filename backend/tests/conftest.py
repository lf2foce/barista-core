"""Test chạy trên Postgres THẬT (barista_erp_test) — RLS / khoá / ràng buộc chỉ chứng minh được ở đúng ranh giới, không mock DB.

Mỗi phiên: xoá sạch → migrate → nạp dữ liệu mẫu qua chính các lệnh (seed) + một công ty thứ hai để thử cô lập.
Test tự tạo dữ liệu riêng (căn / khách mới) thay vì sửa dữ liệu dùng chung, nên thứ tự chạy không ảnh hưởng.
Chạy SONG SONG (pytest-xdist, ``-n 4 --dist loadfile`` mặc định): mỗi tiến trình một DB riêng ``<db>_gw0…`` — tự tạo qua kết nối quản trị
Postgres máy local (``BARISTA_TEST_ADMIN_URL``); test trong một file vẫn chạy tuần tự trên cùng tiến trình. Chạy tuần tự: ``-n0``.

TÊN DB RIÊNG MỖI LẦN CHẠY: không đặt ``BARISTA_TEST_DATABASE_URL`` / ``BARISTA_TEST_OWNER_DATABASE_URL`` / ``BARISTA_TEST_UPGRADE_DATABASE_URL`` ⇒
DB mặc định mang mã lần chạy (``barista_erp_test_<mã>``, ``barista_erp_upgrade_<mã>`` — mã từ ``BARISTA_TEST_RUN_ID`` hoặc tự sinh, các tiến trình
con của cùng lần chạy dùng chung), tự tạo và tự XOÁ khi xong. Hai lần chạy cùng lúc trên một máy (kiểm phát hành + phiên khác) không còn xoá
DB của nhau (review 08/11: 562 / 215 lỗi dây chuyền khi dùng chung tên mặc định). Đặt biến ⇒ dùng đúng DB đó, không xoá. Lần chạy chết giữa chừng
để lại DB có dấu ⇒ lần chạy sau xoá khi quá ``BARISTA_TEST_STALE_HOURS`` giờ (mặc định 6).

BỘ TEST NÀY LÀ CỦA LÕI (ADR 0187 / 0191): không gói ngành nào nằm trong repo — test của một gói ngành ở bản phân phối của gói (repo ``barista-packs``,
``<gói>/tests``), chạy trong môi trường này với ``-p tests.conftest`` sau khi cài gói. Cơ chế lõi cần một gói ngành làm ví dụ ⇒ ``tests/standin/`` (gói
thế vai ``orderdesk`` — 0190). Bộ test chạy được cả khi môi trường đang cài gói ngoài (các test cấu trúc khi đó quét cả mã của gói —
``tests/module_src.py``, ``tests/pack_guards.txt``).
"""

import os
import secrets
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from barista import seed
from barista.config import settings
from barista.db import db
from barista.kernel import executor, files, principal
from barista.kernel.migrate import migrate
from barista.kernel.principal import Principal

WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")   # gw0, gw1… khi chạy song song
ADMIN_URL = os.environ.get("BARISTA_TEST_ADMIN_URL", "postgresql://localhost:5432/postgres")
# mã lần chạy: đặt ở tiến trình chính khi nạp file này (trước khi xdist dựng tiến trình con) ⇒ tiến trình con thừa hưởng cùng mã
RUN_ID = os.environ.setdefault("BARISTA_TEST_RUN_ID", f"r{secrets.token_hex(4)}")


def for_worker(url: str) -> str:
    """DB riêng của tiến trình test (``barista_erp_test`` ⇒ ``barista_erp_test_gw1``); chạy tuần tự giữ nguyên."""
    if not WORKER:
        return url
    base, _, name = url.rpartition("/")
    name, sep, query = name.partition("?")
    return f"{base}/{name}_{WORKER}{sep}{query}"


async def ensure_db(url: str) -> None:
    """Tạo DB test của tiến trình nếu chưa có (chủ barista_owner, barista_app được kết nối); commit không chờ ghi đĩa — CHỈ DB test."""
    from psycopg import AsyncConnection, sql

    name = url.rpartition("/")[2].partition("?")[0]
    async with await AsyncConnection.connect(ADMIN_URL, autocommit=True) as c:
        if not await (await c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))).fetchone():
            await c.execute(sql.SQL("CREATE DATABASE {} OWNER barista_owner").format(sql.Identifier(name)))
            await c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO barista_app").format(sql.Identifier(name)))
            await c.execute(sql.SQL("ALTER DATABASE {} SET synchronous_commit = off").format(sql.Identifier(name)))
            if f"_{RUN_ID}" in name:   # DB của lần chạy (tên tự đặt): ghi dấu + giờ tạo ⇒ lần chạy sau dọn được nếu lần này chết giữa chừng
                await c.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(name), sql.Literal(f"{RUN_MARK} {int(time.time())}")))
        has_vector = await (await c.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).fetchone()
    if has_vector:   # tìm theo nghĩa (0141): pgvector không "trusted" ⇒ quản trị cài sẵn như bản cài thật; máy không có ⇒ migration dùng real[]
        async with await AsyncConnection.connect(ADMIN_URL.rpartition("/")[0] + "/" + name, autocommit=True) as c:
            await c.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")


# DB do lần chạy này tự đặt tên ⇒ tự tạo (cả khi chạy tuần tự) và tự xoá khi xong; DB người dùng chỉ định qua biến môi trường thì để nguyên
OWN_TEST_DB = not (os.environ.get("BARISTA_TEST_DATABASE_URL") or os.environ.get("BARISTA_TEST_OWNER_DATABASE_URL"))
OWN_UPGRADE_DB = not os.environ.get("BARISTA_TEST_UPGRADE_DATABASE_URL")
APP_URL = for_worker(os.environ.get("BARISTA_TEST_DATABASE_URL", f"postgresql://barista_app:barista_app@localhost:5432/barista_erp_test_{RUN_ID}"))
OWNER_URL = for_worker(os.environ.get("BARISTA_TEST_OWNER_DATABASE_URL",
                                      f"postgresql://barista_owner:barista_owner@localhost:5432/barista_erp_test_{RUN_ID}"))
UPGRADE_URL = for_worker(os.environ.get("BARISTA_TEST_UPGRADE_DATABASE_URL",
                                        f"postgresql://barista_owner:barista_owner@localhost:5432/barista_erp_upgrade_{RUN_ID}"))
MAKE_UPGRADE_DB = bool(WORKER) or OWN_UPGRADE_DB   # test nâng cấp tự tạo DB của mình khi tên có hậu tố tiến trình / mã lần chạy


def _db_name(url: str) -> str:
    return url.rpartition("/")[2].partition("?")[0]


RUN_MARK = "barista-test-run"   # dấu trên DB do lần chạy test tự tạo (COMMENT ON DATABASE "<dấu> <giờ tạo epoch>")
STALE_HOURS = float(os.environ.get("BARISTA_TEST_STALE_HOURS", "6"))


def pytest_configure(config: pytest.Config) -> None:
    """Đăng ký marker dùng chung ở conftest để cả bộ test gói (đọc pyproject.toml riêng, nạp ``-p tests.conftest``) không báo cảnh báo marker lạ."""
    config.addinivalue_line("markers", "upgrade: diễn tập nâng cấp migration & nạp/gỡ gói (test_upgrade, test_pack_loading, test_packkit)")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Dọn DB của lần chạy CŨ chết giữa chừng (không tới được pytest_sessionfinish): chỉ DB mang dấu ``RUN_MARK`` tạo quá ``STALE_HOURS`` giờ —
    DB người dùng tự tạo / chỉ định không có dấu nên không bao giờ bị đụng. Chỉ tiến trình chính làm (tiến trình con xdist bỏ qua)."""
    if WORKER:
        return
    import psycopg
    from psycopg import sql

    try:
        with psycopg.connect(ADMIN_URL, autocommit=True) as c:
            rows = c.execute("""SELECT d.datname, shobj_description(d.oid, 'pg_database') FROM pg_database d
                                WHERE shobj_description(d.oid, 'pg_database') LIKE %s""", (f"{RUN_MARK} %",)).fetchall()
            for name, mark in rows:
                born = mark.removeprefix(RUN_MARK).strip()
                if born.isdigit() and time.time() - int(born) > STALE_HOURS * 3600:
                    c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    except psycopg.Error as e:
        print(f"\n[conftest] không dọn được DB test cũ: {e}")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Xoá DB test do CHÍNH tiến trình này tự đặt tên (tiến trình con xoá DB ``_gwN`` của nó). DB chỉ định bằng biến môi trường giữ nguyên."""
    drop = [_db_name(u) for u, own in ((OWNER_URL, OWN_TEST_DB), (UPGRADE_URL, OWN_UPGRADE_DB)) if own]
    if not drop:
        return
    import psycopg
    from psycopg import sql

    try:
        with psycopg.connect(ADMIN_URL, autocommit=True) as c:
            for name in drop:
                c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    except psycopg.Error as e:   # không xoá được thì báo, không làm đỏ cả lần chạy
        print(f"\n[conftest] không xoá được DB test {drop}: {e}")
OTHER_DOMAIN = "khac.demo"
# Test của GÓI đăng ký ở đây (``conftest.py`` trong thư mục test của gói — nạp lúc thu thập, trước fixture phiên) thứ CÔNG TY KHÁC phải có TRƯỚC khi
# công ty mẫu được tạo: ``async (tenant_id) -> None``. Lõi không biết gói nào đăng ký gì.
OTHER_SETUP: list[Callable[[UUID], Awaitable[None]]] = []


@pytest.fixture(autouse=True)
def _fresh_error_burst(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bộ đếm lỗi dồn dập (``kernel/alerts.py``) là của TIẾN TRÌNH: test cố ý gây lỗi hệ thống không được làm test sau thấy "đang dồn dập"."""
    from barista.kernel import alerts

    monkeypatch.setattr(alerts, "burst", alerts.Burst())


@pytest_asyncio.fixture(scope="session", autouse=True)
async def demo() -> AsyncIterator[dict[str, Any]]:
    settings.dev_auth = True  # API test đi qua ASGITransport (client 127.0.0.1)
    # mọi thứ đọc settings (DBOS, migrate…) phải trỏ DB test — không bao giờ ghi vào DB dev
    settings.database_url, settings.owner_database_url, settings.dbos_database_url = APP_URL, OWNER_URL, OWNER_URL
    # kho tệp (0122): thư mục tạm RIÊNG của tiến trình test (như DB riêng) — không bao giờ thư mục dev
    settings.files_store, settings.files_dir = "local", tempfile.mkdtemp(prefix=f"barista-files-{WORKER or 'main'}-")
    files.use_store(None)
    settings.connector_key = "test-connector-key-test-connector-key-0244"   # khung kết nối (0244): mã hoá khoá kết nối trong test
    settings.embed_model = ""   # không bao giờ gọi model embedding thật trong test (khoá trong môi trường máy dev) — test tìm theo nghĩa tiêm model giả
    if WORKER or OWN_TEST_DB:
        await ensure_db(OWNER_URL)
    await seed.reset_schemas(OWNER_URL)
    await migrate(OWNER_URL)
    await db.open(APP_URL, OWNER_URL)
    # Công ty KHÁC tạo TRƯỚC công ty mẫu ⇒ dòng của nó nằm trước trong bảng: mọi truy vấn của test qua kết nối owner (bỏ qua RLS) mà tra theo mã
    # (khách, chiến dịch, và thứ test của gói đăng ký ở ``OTHER_SETUP`` — vd một dự án trùng mã) mà quên lọc công ty sẽ đọc nhầm dòng của công ty
    # này — thất bại MỌI lần, không tuỳ thứ tự file trong một worker (gốc của hai test chập chờn 10/11).
    other = await seed.create_tenant("Công ty khác (test)", OTHER_DOMAIN,
                                     people=[("admin", "Quản trị ngoài", "admin", "QT", True), ("sale1", "Sale ngoài", "sale", "KD-A", False)])
    for setup in OTHER_SETUP:
        await setup(other)
    tid = await seed.create_tenant(seed.TENANT, seed.DOMAIN)
    out = await seed.seed_business(tid)
    async with db.owner.connection() as c:
        seeded_at = (await (await c.execute("SELECT clock_timestamp() AS t")).fetchone())["t"]   # type: ignore[index]
    yield {"tenant": tid, "other": other, "seeded_at": seeded_at, **out}
    await db.close()
    shutil.rmtree(settings.files_dir, ignore_errors=True)


# Cơ cấu MẪU (đội có từ lúc nạp mẫu: ai thuộc đội nào, ai là trưởng, ai đang làm) là thứ MỌI test trong một tiến trình dùng chung. Test nào để lại thay đổi
# trên đó làm test KHÁC đỏ tuỳ thứ tự chạy — và người nhìn thấy đỏ là nạn nhân, không phải thủ phạm (24/11/2026: ba lần trong một ngày). Bất biến: mỗi test
# trả cơ cấu mẫu về như lúc nhận. Cần đội để thử ⇒ tạo đội riêng (``admin.upsert_team``) + ``fresh_sale``; buộc phải đụng đội mẫu ⇒ trả lại trước khi xong.
_SAMPLE_STRUCTURE = """SELECT coalesce(string_agg(t.code || ':' || u.email || ':' || tm.is_lead || ':' || tm.is_home || ':' || mb.active, ' ' ORDER BY t.code, u.email), '')
                       FROM core.teams t JOIN core.team_members tm ON tm.team_id = t.id JOIN core.users u ON u.id = tm.user_id
                       JOIN core.memberships mb ON mb.user_id = tm.user_id AND mb.tenant_id = t.tenant_id
                       WHERE t.tenant_id = %s AND t.created_at <= %s"""


def _note_offender(path: str, line: str) -> None:
    with open(path, "a") as f:
        f.write(line)


async def _sample_structure(demo: dict[str, Any]) -> str:
    async with db.owner.connection() as c:
        row = await (await c.execute(_SAMPLE_STRUCTURE, (demo["tenant"], demo["seeded_at"]))).fetchone()
    return str(next(iter(row.values())) if isinstance(row, dict) else row[0])   # type: ignore[union-attr,index]


@pytest_asyncio.fixture(autouse=True)
async def _sample_structure_is_left_as_found(demo: dict[str, Any], request: pytest.FixtureRequest) -> AsyncIterator[None]:
    before = await _sample_structure(demo)
    yield
    after = await _sample_structure(demo)
    if after != before:
        diff = sorted(set(after.split()) ^ set(before.split()))
        report = os.environ.get("BARISTA_TEST_STRUCTURE_REPORT")
        if report:   # chế độ đo: ghi thủ phạm ra file thay vì làm đỏ (dùng một lần khi rà)
            _note_offender(report, f"{request.node.nodeid}\t{' '.join(diff[:6])}\n")
        else:
            pytest.fail(f"test để lại thay đổi trên cơ cấu mẫu dùng chung: {' '.join(diff[:8])} — tạo đội riêng cho test, hoặc trả lại trước khi xong")


type Who = Callable[..., Awaitable[Principal]]


# Test đóng vai NGƯỜI BẤM ở cửa HTTP (quyết định duyệt chỉ nhận tay cầm EDGE còn hạn — 0144): một hợp đồng thuê mở suốt phiên test.
_EDGE_LEASE = principal.Lease()


def edge(p: Principal) -> Principal:
    """Bản EDGE (như ``web.current`` cấp cho request) của một người gọi test — dùng cho ``core.decide``."""
    import dataclasses

    return principal.issued(dataclasses.replace(p), kind=principal.EDGE, lease=_EDGE_LEASE)


@pytest.fixture
def who(demo: dict[str, Any]) -> Who:
    async def f(local: str, *, other: bool = False) -> Principal:
        return await seed.who(demo["other"] if other else demo["tenant"], local, OTHER_DOMAIN if other else seed.DOMAIN)

    return f


async def q(p: Principal, name: str, **inp: Any) -> Any:
    r = await executor.query(p, name, inp)
    assert r.status == "ok", (name, r.status, r.error)
    return r.output


async def ok(p: Principal, name: str, /, **inp: Any) -> Any:
    r = await executor.run(p, name, inp)
    assert r.status in ("ok", "pending_approval"), (name, r.status, r.error)
    return r.output


async def approved(p: Principal, name: str, /, **inp: Any) -> Any:
    """Lệnh đổi quyền (0029) thành phiếu chờ chủ tài khoản ⇒ người duyệt từng bước bấm Duyệt; trả kết quả lệnh sau duyệt. Lệnh không thành
    phiếu (lần gọi không nhạy cảm, chủ tài khoản duy nhất tự đổi) ⇒ trả kết quả như ``ok``. Test thiết lập quyền dùng hàm này để nói rõ: thay
    đổi này đi qua chủ tài khoản."""
    from barista.kernel import auth

    r = await executor.run(p, name, inp)
    assert r.status in ("ok", "pending_approval"), (name, r.status, r.error)
    while r.status == "pending_approval" or (r.output or {}).get("status") == "pending":
        aid = r.approval_id or (r.output or {}).get("approval_id")
        approver = await owner_val("SELECT approver_ids[1] FROM core.approvals WHERE id = %s", (UUID(str(aid)),))
        async with db.owner.connection() as conn:
            who_ = await auth.load(conn, p.tenant_id, user_id=approver)
        assert who_ is not None, (name, "không có người duyệt")
        r = await executor.run(edge(who_), "core.decide", {"approval_id": str(aid), "approve": True})
        assert r.status == "ok", (name, r.status, r.error)
        r.approval_id = r.approval_id or str(aid)
    if isinstance(r.output, dict) and "result" in r.output:
        assert r.output["status"] == "executed", (name, r.output)
        return r.output["result"]
    return r.output


async def owner_val(sql: Any, params: Any = None) -> Any:
    async with db.owner.connection() as conn:
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
    return None if row is None else next(iter(row.values()))


_phone_seq = iter(range(1, 100_000))


def fresh_phone() -> str:
    """SĐT mẫu mới (đầu 010 — bất biến 9) ở dạng ĐÃ LƯU (E.164, 0199) ⇒ so thẳng với cột / định danh. Đường gõ số nội địa ``0…``: test_phone_parse."""
    return f"+84109{next(_phone_seq):06d}"


__all__ = ["UUID", "approved", "fresh_phone", "ok", "owner_val", "q"]


async def own_team(who: Who, kind: str = "sales") -> str:
    """Đội RIÊNG của một test (đội gốc, ngoài cây đội mẫu) — mã trả về dùng cho ``team_code``. Test cần xếp người vào một đội thì dùng đội này, không dùng
    đội mẫu (KD-A, KD-B, QT…): cơ cấu mẫu là của chung (``_sample_structure_is_left_as_found``)."""
    code = f"T{uuid4().hex[:8].upper()}"
    await ok(await who("admin"), "admin.upsert_team", code=code, name=f"Đội thử {code}", kind=kind, parent_code="")
    return code


async def fresh_sale(who: Who, team: str = "") -> Principal:
    """Sale mới tinh, không thuộc đội mẫu: doanh số cuốn chiếu = 0, không dính giao dịch của test khác và không làm đổi số của đội mẫu
    (bộ test dùng chung một DB, thứ tự file không cố định)."""
    local = f"t{uuid4().hex[:10]}"
    await ok(await who("admin"), "admin.invite_user", email=f"{local}@{seed.DOMAIN}", name=f"Sale thử {local}", role="sale", team_code=team, is_lead=False)
    return await who(local)


@pytest.fixture
def freeze_clock(monkeypatch: pytest.MonkeyPatch) -> Callable[[datetime], None]:
    """Đồng hồ CHIA LEAD (``leadflow.clock()``) đứng yên ở một mốc — test về giờ làm việc / đêm / đội trực không được đọc giờ máy (nếu không,
    chạy lúc 01:50 hay 18:01 cho kết quả khác nhau). Gọi lại để dời mốc. Giờ của DB (``now()``) không đổi — test đặt hạn bằng SQL dùng mốc tuyệt đối."""
    import barista.modules.leadflow as lf

    def freeze(at: datetime) -> None:
        monkeypatch.setattr(lf, "clock", lambda: at)

    return freeze


@pytest.fixture
def tomorrow_is_today(monkeypatch: pytest.MonkeyPatch) -> Callable[[], date]:
    """Ghim ``today()`` (đồng hồ demo — ``kernel/clock``) sang NGÀY MAI thật: lịch đặt vào "hôm nay" (đã ghim) luôn nằm trong tương lai của giờ máy, nên test
    "hôm nay có hẹn" chạy được lúc 23:50 cũng như 09:00 — không phải bỏ qua gần nửa đêm. Gọi hàm để ghim; trả ngày đã ghim."""
    def pin() -> date:
        from barista.kernel.clock import VN as tz

        d = datetime.now(tz).date() + timedelta(days=1)
        monkeypatch.setattr(settings, "demo_today", d.isoformat())
        return d

    return pin
