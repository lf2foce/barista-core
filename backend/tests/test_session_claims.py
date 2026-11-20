"""N11b (ADR 0146) — claims phiên do LÕI cấp, ký bằng khoá app không đọc được; SQL của gói (kể cả bị chèn) không đổi được công ty / người / quyền hệ
thống; không có claims hợp lệ ⇒ đóng. Mọi đầu dò chạy SQL như mã của một gói đang chạy trong handler (quyền barista_app, cùng giao dịch)."""

import hashlib
import hmac
import json
import os
import time
from typing import Any

import psycopg
import pytest

from barista import db as dbmod
from barista.db import db
from barista.kernel import executor
from tests.conftest import Who, owner_val
from tests.test_pack_surface import _in_handler


async def _one(conn: Any, sql: str) -> Any:
    row = await (await conn.execute(sql)).fetchone()
    assert row is not None
    return next(iter(row.values()))


async def test_hmac_in_sql_matches_python_hmac() -> None:
    for n in (0, 1, 31, 32, 63, 64, 65, 200):
        key, msg = os.urandom(n or 1), os.urandom(77)
        got = await owner_val("SELECT encode(core.hmac_sha256(%s, %s), 'hex')", (key, msg))
        assert got == hmac.new(key, msg, hashlib.sha256).hexdigest(), n


async def test_app_role_cannot_read_the_session_key(who: Who) -> None:
    sale = await who("sale1")
    async with db.tenant_tx(sale) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT count(*) FROM core.session_key")


async def test_set_config_no_longer_changes_what_rls_sees(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đầu dò A của test_pack_surface dưới dạng dương: set_config app.* là vô nghĩa; core.tenant() vẫn là công ty của người gọi."""
    sale, other = await who("sale1"), await who("admin", other=True)

    async def body(ctx: Any, out: dict[str, object]) -> None:
        await ctx.exec("SELECT set_config('app.tenant_id', %s, true), set_config('app.system', 'on', true)", (str(other.tenant_id),))
        out["tenant"] = await ctx.val("SELECT core.tenant()")
        out["system"] = await ctx.val("SELECT core.is_system()")
        out["other_members"] = await ctx.val("SELECT count(*) FROM core.memberships WHERE tenant_id = %s", (other.tenant_id,))

    out = await _in_handler(monkeypatch, sale, body)
    assert out["tenant"] == sale.tenant_id and out["system"] is False and out["other_members"] == 0


async def test_forged_open_session_is_refused_and_the_transaction_fails_closed(who: Who) -> None:
    sale, other = await who("sale1"), await who("admin", other=True)
    forged = json.dumps({**dbmod.claims_of(sale), "tenant_id": str(other.tenant_id), "system": "on"}, separators=(",", ":"), sort_keys=True)

    async with db.tenant_tx(sale, read_only=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="không do lõi cấp"):
            await conn.execute("SELECT core.open_session(%s, %s, %s)", (forged, "00" * 32, int(time.time())))


async def test_discard_temp_or_a_fake_claims_table_means_no_rows(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gói xoá bảng tạm (DISCARD TEMP) ⇒ hàm biến phiên trả NULL ⇒ không thấy dòng nào (đóng); gói KHÔNG tạo nổi bảng tạm cùng tên với claims giả
    (057 thu hồi quyền TEMP của barista_app — nhờ vậy ``core.claims()`` không phải kiểm chủ bảng từng dòng); lõi mở lại phiên thì bình thường."""
    sale = await who("sale1")

    async def body(ctx: Any, out: dict[str, object]) -> None:
        out["mine_before"] = await ctx.val("SELECT count(*) FROM core.memberships")
        await ctx.exec("DISCARD TEMP")
        out["tenant_after_discard"] = await ctx.val("SELECT core.tenant()")
        out["mine_after_discard"] = await ctx.val("SELECT count(*) FROM core.memberships")

    out = await _in_handler(monkeypatch, sale, body)
    assert int(str(out["mine_before"])) > 0
    assert out["tenant_after_discard"] is None and out["mine_after_discard"] == 0
    async with db.tenant_tx(sale) as conn:   # giao dịch ghi — kể cả ở đây app cũng không tạo được bảng tạm
        await conn.execute("DISCARD TEMP")
        assert await _one(conn, "SELECT core.tenant()") is None
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied"):
            async with conn.transaction():
                await conn.execute("CREATE TEMP TABLE barista_claims (k smallint PRIMARY KEY, xact timestamptz NOT NULL, claims jsonb NOT NULL)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied"):
            async with conn.transaction():
                await conn.execute("CREATE TEMP TABLE pg_class (oid oid, relowner oid)")   # cũng không che được pg_catalog bằng bảng tạm
        assert await _one(conn, "SELECT core.is_system()") is False
        assert await _one(conn, "SELECT count(*) FROM core.memberships") == 0
        await dbmod.set_session(conn, sale)   # lõi (owner) tạo lại bảng và mở phiên bình thường
        assert await _one(conn, "SELECT core.me()") == sale.user_id
        assert await _one(conn, "SELECT count(*) FROM core.memberships") > 0


async def test_open_session_in_the_same_transaction_switches_claims_for_nested_calls(who: Who) -> None:
    """Gọi lồng bằng quyền khác (executor.invoke) mở lại claims trong CÙNG giao dịch — hợp lệ vì có chữ ký; trả về xong là thấy lại đúng người cũ."""
    sale, truonga = await who("sale1"), await who("truonga")
    async with db.tenant_tx(sale, read_only=True) as conn:
        assert await _one(conn, "SELECT core.me()") == sale.user_id
        await dbmod.set_session(conn, truonga)
        assert await _one(conn, "SELECT core.me()") == truonga.user_id
        await dbmod.set_session(conn, sale)
        assert await _one(conn, "SELECT core.me()") == sale.user_id


async def test_claims_are_per_transaction_not_per_connection(who: Who) -> None:
    """Bản đã kiểm gắn với transaction_timestamp: giao dịch kế trên cùng kết nối mà không mở lại ⇒ NULL (đóng), không kế thừa."""
    sale = await who("sale1")
    async with db.app.connection() as conn:
        async with conn.transaction():
            await dbmod.set_session(conn, sale)
            assert await _one(conn, "SELECT core.tenant()") == sale.tenant_id
        async with conn.transaction():
            assert await _one(conn, "SELECT core.tenant()") is None
            assert await _one(conn, "SELECT count(*) FROM core.memberships") == 0


async def test_queries_still_work_end_to_end_as_every_role(who: Who) -> None:
    for local in ("sale1", "truonga", "giamdoc", "ketoan", "admin"):
        p = await who(local)
        r = await executor.query(p, "core.notifications", {})
        assert r.ok, (local, r.error)


async def test_a_session_without_claims_is_nobody_not_null(who: Who) -> None:
    """Lỗ đóng kèm 0146: ``core.is_system()`` cũ = ``current_setting('app.system', true) = 'on'`` trả NULL khi phiên không có biến ⇒ ``IF NOT sys``
    trong trigger 033 thành NULL ⇒ không raise ⇒ kết nối owner / psql không claims ghi được cột kiểm. Giờ không claims = false ⇒ chặn."""
    sale = await who("sale1")
    assert await owner_val("SELECT core.is_system()") is False
    assert await owner_val("SELECT core.tenant() IS NULL AND core.me() IS NULL AND core.scope('crm') = 'none'") is True
    async with db.tenant_tx(sale) as conn:
        cur = await conn.execute("""INSERT INTO flow.tasks (title, assignee_id, subject_kind, subject_id, done_check, done_args)
                                    VALUES ('Việc có bộ kiểm', %s, 'deals.deal', gen_random_uuid(), 'deals.installment_paid', '{"seq": 1}') RETURNING id""",
                                 (sale.user_id,))
        tid = (await cur.fetchone() or {})["id"]
    with pytest.raises(psycopg.errors.CheckViolation, match="chỉ hệ thống ghi"):
        await owner_val("UPDATE flow.tasks SET checked_at = now() WHERE id = %s RETURNING 1", (tid,))


async def test_claims_functions_qualify_catalog_and_search_pg_temp_last() -> None:
    """Review 10/11 (HIGH): pg_temp được tìm TRƯỚC pg_catalog khi search_path không ghi nó. App nay không tạo được bảng tạm (REVOKE TEMP) nên không
    che được pg_class; vẫn giữ phòng tuyến thứ hai trong chính hàm: mọi pg_class ghi pg_catalog.pg_class, search_path ghi pg_temp CUỐI."""
    for fn in ("core.open_session(text,text,bigint)", "core.hmac_sha256(bytea,bytea)"):
        src = str(await owner_val("SELECT pg_get_functiondef(%s::regprocedure)", (fn,)))
        assert "SET search_path TO 'pg_catalog'" in src and src.index("'pg_temp'") > src.index("'pg_catalog'"), fn
        assert " pg_class" not in src.replace("pg_catalog.pg_class", ""), fn
    # core.claims() chạy từng dòng nên KHÔNG có SET search_path (tốn lưu/khôi phục GUC mỗi lần gọi) — bù bằng tên đủ schema + OPERATOR(pg_catalog.=)
    src = str(await owner_val("SELECT pg_get_functiondef('core.claims()'::regprocedure)"))
    assert "SET search_path" not in src
    assert "pg_catalog.to_regclass('pg_temp.barista_claims')" in src and "FROM pg_temp.barista_claims" in src
    assert "OPERATOR(pg_catalog.=)" in src and " = " not in src.split("BEGIN", 1)[1].replace("OPERATOR(pg_catalog.=)", "")
    # bất biến mà core.claims() dựa vào: barista_app không có quyền TEMP trên DB này
    assert await owner_val("SELECT has_database_privilege('barista_app', current_database(), 'TEMP')") is False
    assert await owner_val("SELECT has_database_privilege('barista_owner', current_database(), 'TEMP')") is True


async def test_a_stale_signature_is_refused(who: Who) -> None:
    """Review 10/11 (LOW): chữ ký phủ cả giờ ký; lệch quá 5 phút ⇒ từ chối (không dùng lại chữ ký cũ ở giao dịch sau trên cùng backend)."""
    sale = await who("sale1")
    claims = json.dumps(dbmod.claims_of(sale), separators=(",", ":"), sort_keys=True)
    async with db.tenant_tx(sale, read_only=True) as conn:
        old = int(time.time()) - 3600
        mac = hmac.new(await dbmod.session_key(), f"{claims}:{conn.info.backend_pid}:{old}".encode(), hashlib.sha256).hexdigest()
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="quá hạn"):
            await conn.execute("SELECT core.open_session(%s, %s, %s)", (claims, mac, old))


async def test_app_pool_refuses_to_open_when_the_app_role_can_create_temp_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chốt lúc khởi động: an toàn của claims() dựa vào "role app không có TEMP"; nối app bằng role có TEMP (ở đây: owner) ⇒ không mở pool, báo rõ;
    ``BARISTA_ALLOW_APP_TEMP=1`` (dev/test cố ý) mới cho qua."""
    from barista.config import settings as cfg
    from barista.db import Database

    other = Database()
    with pytest.raises(RuntimeError, match="quyền TEMP"):
        await other.open(cfg.owner_database_url, cfg.owner_database_url, max_size=1)
    monkeypatch.setattr(cfg, "allow_app_temp", True)
    await other.open(cfg.owner_database_url, cfg.owner_database_url, max_size=1)
    try:
        assert await _one_any(other, "SELECT 1") == 1
    finally:
        await other.close()


async def _one_any(d: Any, sql: Any) -> Any:
    async with d.app.connection() as conn:
        row = await (await conn.execute(sql)).fetchone()
    return None if row is None else next(iter(row.values()))


async def test_a_connection_returned_with_an_open_transaction_block_is_dropped_not_reused(who: Who) -> None:
    """Bản demo dùng chung 18/11/2026: một lượt bị huỷ giữa chừng để lại kết nối mà máy chủ thấy IDLE nhưng psycopg còn đếm khối giao dịch đang mở;
    pool chỉ nhìn trạng thái máy chủ nên giữ lại ⇒ mọi request mượn phải nó lỗi ``AssertionError`` ở ``conn.transaction()`` (500 xen kẽ tới khi khởi
    động lại). Hàm ``check`` của pool (``db.drop_half_open``) bỏ kết nối đó ⇒ các request sau chạy bình thường."""
    sale = await who("sale1")
    poisoned = None
    with pytest.raises(TimeoutError):
        async with db.app.connection() as conn:   # lượt bị huỷ / hết hạn: ra khỏi khối bằng lỗi (pool thử rollback — psycopg từ chối, chỉ ghi cảnh báo)
            poisoned = conn
            conn._num_transactions += 1   # đúng trạng thái đã gặp: máy chủ IDLE, bộ đếm của psycopg còn một khối
            raise TimeoutError
    for _ in range(db.app.max_size + 1):   # đủ vòng để chắc không còn kết nối hỏng nào trong pool
        assert (await executor.query(sale, "core.approvals", {})).ok
    assert poisoned is not None and poisoned.closed                                # pool đóng kết nối đó, không giữ lại
