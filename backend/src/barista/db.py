"""Kết nối Postgres. Hai pool tách bạch:

* ``app``   — role barista_app (không BYPASSRLS). Mọi lệnh / truy vấn nghiệp vụ chạy ở đây trong ``tenant_tx``: biến phiên
  (công ty, người, đội quản lý, phạm vi từng tài nguyên) đặt bằng ``set_config(..., true)`` — chỉ sống trong giao dịch.
* ``owner`` — role barista_owner (BYPASSRLS). Chỉ cho migration, tra danh tính lúc đăng nhập, liệt kê outbox xuyên công ty.

Múi giờ (0169): mọi kết nối mở với ``TimeZone`` = múi mặc định của bản cài (không theo cấu hình máy chủ Postgres); ``tenant_tx`` đặt lại theo múi CÔNG TY
cho riêng giao dịch đó (``SET LOCAL``) và đặt ``clock.zone()`` cùng lúc — một lần tra ``core.tenants.timezone``.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from barista.kernel import clock, timing
from barista.kernel.fields import using_country
from barista.kernel.principal import Principal

type Conn = AsyncConnection[dict[str, Any]]

log = logging.getLogger("barista.db")


async def drop_half_open(conn: AsyncConnection[Any]) -> None:
    """Hàm ``check`` của pool (chạy mỗi lần MƯỢN kết nối — không đặt ở ``reset``: reset chạy trên luồng nền của pool nên kết nối vừa trả chưa rảnh
    ngay, đo được ở test_kb_hybrid khi gom nhánh 18/11): kết nối mà psycopg còn ĐẾM một khối giao dịch đang mở (``_num_transactions`` — trạng thái phía
    máy chủ có thể đã IDLE) thì bỏ, pool mở kết nối khác cho người mượn. Gặp ở bản demo dùng chung 18/11/2026: lượt bị huỷ giữa chừng (hết hạn nguồn bàn việc, tiến trình
    tắt) để lại một kết nối IDLE như thế; pool chỉ nhìn trạng thái máy chủ nên giữ lại, MỌI request mượn phải nó sau đó lỗi ``AssertionError`` ở
    ``conn.transaction()`` (500 xen kẽ, mãi tới khi khởi động lại). Không có SQL — chỉ đọc một bộ đếm trong bộ nhớ."""
    if getattr(conn, "_num_transactions", 0):
        await conn.close()   # pool chỉ bỏ kết nối ĐÃ đóng — kiểm hỏng mà không đóng thì pool nhận lại rồi đưa đúng kết nối đó lần nữa
        raise psycopg.ProgrammingError("kết nối còn đếm khối giao dịch đang mở — đã đóng, pool mở kết nối mới")


@dataclass
class _Hold:
    task: asyncio.Task[Any] | None
    depth: int
    after: list[Callable[[], Awaitable[None]]] = field(default_factory=list)


# Kết nối pool mà TÁC VỤ hiện tại đang giữ (trong ``tenant_tx`` / ``owner_tx``) — review 12/11 F10, ADR 0200: việc phụ cần thêm một kết nối (ghi lượt
# dùng AI) trong khi tác vụ đang giữ một kết nối thì KHÔNG mượn thêm (pool kín ⇒ mọi tác vụ chờ nhau, việc phụ hết hạn chờ và bị bỏ) mà hẹn chạy NGAY SAU
# khi tác vụ trả kết nối (``after_release``). Gắn với đúng tác vụ: tác vụ con tạo bên trong giao dịch thừa hưởng biến ngữ cảnh nhưng không giữ kết nối.
_HOLD: ContextVar[_Hold | None] = ContextVar("barista_db_hold", default=None)


def _mine() -> _Hold | None:
    h = _HOLD.get()
    return h if h is not None and h.depth > 0 and h.task is asyncio.current_task() else None


def holding() -> bool:
    """Tác vụ hiện tại đang giữ một kết nối pool trong giao dịch."""
    return _mine() is not None


def after_release(fn: Callable[[], Awaitable[None]]) -> bool:
    """Hẹn ``fn`` chạy ngay sau khi tác vụ hiện tại trả kết nối ngoài cùng (giao dịch commit HAY hoàn đều chạy). False ⇒ tác vụ không giữ kết nối nào,
    bên gọi tự chạy ngay. ``fn`` lỗi chỉ ghi log — tự lo việc thử lại của mình."""
    h = _mine()
    if h is None:
        return False
    h.after.append(fn)
    return True


@asynccontextmanager
async def _held() -> AsyncIterator[None]:
    h = _mine()
    if h is not None:   # giao dịch lồng trong cùng tác vụ: việc hẹn chạy khi trả kết nối NGOÀI CÙNG
        h.depth += 1
        try:
            yield
        finally:
            h.depth -= 1
        return
    h = _Hold(asyncio.current_task(), 1)
    token = _HOLD.set(h)
    try:
        yield
    finally:
        h.depth = 0
        _HOLD.reset(token)
        for fn in h.after:
            try:
                await fn()
            except Exception:
                log.exception("việc hẹn sau khi trả kết nối lỗi")


class Database:
    def __init__(self) -> None:
        self._app: AsyncConnectionPool[Conn] | None = None
        self._owner: AsyncConnectionPool[Conn] | None = None
        self._session_key: bytes | None = None   # khoá ký claims phiên (N11b), nạp lười qua pool owner

    async def open(self, app_url: str, owner_url: str, *, max_size: int = 10) -> None:
        from barista.config import settings

        # múi phiên cố định theo bản cài ⇒ máy chủ Postgres đặt timezone gì (UTC trên VPS, giờ máy dev) cũng ra cùng kết quả (0169)
        kwargs = {"row_factory": dict_row, "autocommit": True, "options": f"-c TimeZone={clock.named(settings.default_timezone).key}"}
        self._app = AsyncConnectionPool(app_url, min_size=1, max_size=max_size, kwargs=kwargs, open=False, check=drop_half_open)
        self._owner = AsyncConnectionPool(owner_url, min_size=1, max_size=3, kwargs=kwargs, open=False, check=drop_half_open)
        await self._app.open(wait=True)
        await self._owner.open(wait=True)
        await self._check_app_role()

    async def _check_app_role(self) -> None:
        """An toàn của ``core.claims()`` (057, ADR 0146) dựa vào MỘT giả định: role app không tạo được bảng tạm (nên không giả được
        ``pg_temp.barista_claims``). Role app có TEMP (bản cài đặt tên role khác, dev nối bằng owner/superuser, GRANT về sau) ⇒ không khởi động;
        đường dev/test cố ý nối bằng owner thì đặt ``BARISTA_ALLOW_APP_TEMP=1`` (và biết rằng khi đó claims giả được)."""
        from barista.config import settings

        if settings.allow_app_temp:
            return
        async with self.app.connection() as conn:
            row = await (await conn.execute(
                "SELECT current_user AS who, current_database() AS db, has_database_privilege(current_user, current_database(), 'TEMP') AS temp")).fetchone()
        if row and row["temp"]:
            await self.close()
            raise RuntimeError(f"Role app {row['who']} có quyền TEMP trên DB {row['db']} — claims phiên (057) giả được. Chạy migration 057 (REVOKE TEMP) "
                               "hoặc nối app bằng role không có TEMP; chỉ dev/test cố ý nối bằng owner mới đặt BARISTA_ALLOW_APP_TEMP=1")

    async def close(self) -> None:
        for pool in (self._app, self._owner):
            if pool is not None:
                await pool.close()
        self._app = self._owner = None
        self._session_key = None

    @property
    def app(self) -> AsyncConnectionPool[Conn]:
        assert self._app is not None, "db chưa mở"
        return self._app

    @property
    def owner(self) -> AsyncConnectionPool[Conn]:
        assert self._owner is not None, "db chưa mở"
        return self._owner

    @asynccontextmanager
    async def tenant_tx(self, p: Principal, *, read_only: bool = False, rollback: bool = False,
                        wait: float | None = None, consistent: bool = False) -> AsyncIterator[Conn]:
        """Một giao dịch dưới quyền ``p``. ``read_only`` cho truy vấn (handler đọc không ghi được, DB chặn);
        ``rollback`` cho chạy thử (nhập Excel dry-run): chạy thật rồi bỏ hết. ``wait``: chờ mượn kết nối tối đa bao nhiêu giây (None = mặc định
        của pool) — việc phụ không được treo lâu khi pool kín (``PoolTimeout``). ``consistent``: cả giao dịch đọc MỘT ảnh dữ liệu (REPEATABLE READ —
        đặt ở câu ĐẦU, trước cả việc mở phiên RLS; Postgres không cho đổi mức cách ly sau câu đầu)."""
        t0 = time.perf_counter()
        async with _held(), self.app.connection(timeout=wait) as conn, conn.transaction() as tx:   # _held NGOÀI: việc hẹn chạy sau khi trả kết nối
            t1 = time.perf_counter()
            timing.add("app-wait", t1 - t0)   # gồm cả BEGIN
            # claims + múi giờ công ty + READ ONLY: ba câu của MỌI giao dịch ⇒ MỘT lượt mạng (một thông điệp nhiều câu — đo 12/11/2026). Postgres chạy đúng
            # thứ tự, dừng ở câu lỗi đầu tiên: claims TRƯỚC READ ONLY (lần đầu trên kết nối mới còn phải tạo bảng tạm claims — N11b); chữ ký sai ⇒ lỗi,
            # giao dịch hoàn như trước. Không dùng pipeline: lượt bị huỷ giữa chừng (luồng SSE đóng) để lại máy phát dở của psycopg.
            claims, mac, ts = await signed_claims(conn, p)
            cur = psycopg.AsyncClientCursor(conn)
            iso = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ; " if consistent else ""
            await cur.execute(f"{iso}SELECT core.open_session(%(claims)s, %(mac)s, %(ts)s); {ZONE_SQL}{'; SET TRANSACTION READ ONLY' if read_only else ''}",
                              {"claims": claims, "mac": mac, "ts": ts})
            if consistent:
                cur.nextset()   # bỏ qua kết quả của câu SET TRANSACTION đặt đầu
            cur.nextset()
            tz, country = _zone_of(await cur.fetchone())
            timing.add("claims", time.perf_counter() - t1)
            with clock.using(tz), using_country(country):
                yield conn
                if rollback:
                    raise psycopg.Rollback(tx)

    @asynccontextmanager
    async def owner_tx(self) -> AsyncIterator[Conn]:
        async with _held(), self.owner.connection() as conn, conn.transaction():
            yield conn


# Múi giờ của công ty phiên đang mở (0169): đặt ``TimeZone`` của GIAO DỊCH này theo công ty (mốc ``timestamptz`` đọc ra, ``core.today()``, ``core.tz()``
# theo công ty) và trả tên để ``clock.zone()`` dùng cùng múi — kèm quốc gia công ty (0199, cách hiện SĐT).
ZONE_SQL = "SELECT set_config('TimeZone', t.timezone, true) AS tz, t.country FROM core.tenants t WHERE t.id = core.tenant()"


def _zone_of(row: dict[str, Any] | None) -> tuple[str, str | None]:
    return (row["tz"], row["country"]) if row else (clock.default_zone().key, None)


async def session_key() -> bytes:
    """Khoá ký claims phiên (N11b — ADR 0146): ở bảng ``core.session_key`` chỉ owner đọc được; tiến trình app nạp MỘT lần qua pool owner và giữ trong
    bộ nhớ — SQL chạy bằng barista_app (kể cả bị chèn) không đọc được khoá nên không ký được claims giả."""
    if db._session_key is None:
        async with db.owner.connection() as conn:
            row = await (await conn.execute("SELECT key FROM core.session_key WHERE id = 1")).fetchone()
        if row is None:
            raise RuntimeError("core.session_key trống — DB chưa chạy migration 057")
        db._session_key = bytes(row["key"])
    return db._session_key


def claims_of(p: Principal) -> dict[str, Any]:
    """Những gì RLS cần biết về người gọi — đúng tám biến phiên cũ, giờ là một JSON ký."""
    return {
        "tenant_id": str(p.tenant_id),
        "user_id": str(p.user_id) if p.user_id else "",
        "teams": [str(t) for t in p.managed_teams],
        "scopes": {} if p.system else p.scopes,   # quyền hệ thống: scope() trả 'all' cho MỌI tài nguyên (vế sau của coalesce) — không mang scopes của người gây
        "system": "on" if p.system else "off",
        "email": p.email.lower(),  # email TÀI KHOẢN đã xác thực (core.users) — so quyền tài liệu nguồn ngoài, không bao giờ do client khai
        "link_ref": str(p.link_ref) if p.link_ref else "",   # link công khai (0064): chính sách p_link chỉ mở ĐÚNG đối tượng link trỏ tới
        "member_teams": [str(t) for t in p.member_teams],   # đội THAM GIA: chỉ phạm vi "unit" đọc (core.my_units) — không phải đội quản lý
    }


async def signed_claims(conn: Conn, p: Principal) -> tuple[str, str, int]:
    """(claims JSON, chữ ký, giờ ký) cho ``core.open_session`` — chữ ký gắn với backend của ĐÚNG kết nối này."""
    claims = json.dumps(claims_of(p), separators=(",", ":"), sort_keys=True)
    ts = int(time.time())   # giờ ký: DB từ chối chữ ký lệch quá 5 phút ⇒ không dùng lại được ở giao dịch sau trên cùng backend
    mac = hmac.new(await session_key(), f"{claims}:{conn.info.backend_pid}:{ts}".encode(), hashlib.sha256).hexdigest()
    return claims, mac, ts


async def set_session(conn: Conn, p: Principal) -> None:
    """Mở / đổi claims phiên của giao dịch hiện tại qua ``core.open_session(claims, mac, ts)`` — hàm DEFINER của owner kiểm chữ ký rồi ghi bản đã kiểm vào
    bảng tạm chỉ owner ghi được; mọi hàm biến phiên (core.tenant / me / scope…) đọc bản đó. ``set_config('app.*')`` không còn ý nghĩa (N11b)."""
    await conn.execute("SELECT core.open_session(%s, %s, %s)", await signed_claims(conn, p))


db = Database()
