"""HTTP CỦA LÕI dùng chung: ai đang gọi (phiên / dev / app từ xa / link), trả kết quả executor, kênh SSE, đọc tệp gửi lên theo khúc.

``http_api.py`` (route của lõi) và route do MODULE tự khai (``Module.route`` — gói mang đường HTTP riêng như kênh tức thì, tải tệp lên / về) đều
dùng đúng các hàm này: xác thực, hạn mức, kiểm lại quyền của kênh mở lâu nằm ở lõi; route của module chỉ đọc tham số rồi gọi executor
(``run`` / ``query``) bằng người gọi lõi cấp. Gói dùng qua ``barista.api.web``.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, cast
from uuid import UUID

from fastapi import Body, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import HTTPConnection

from barista.config import settings
from barista.db import Conn, db
from barista.kernel import auth, executor, links, live, principal, ratelimit, remote_apps, sessions, timing
from barista.kernel.principal import Principal

log = logging.getLogger("barista.web")


def _issued(p: Principal) -> Principal:
    """Người gọi phải được cấp ở CỬA HTTP (phiên / dev / app từ xa qua ``current``, link qua ``links.resolve``) — bản tự dựng, quyền hệ thống,
    ``ctx.p`` của thao tác, quyền người khác nạp bằng ``principal_of`` đều bị từ chối (0130): route của gói không thành đường nâng quyền / làm thay."""
    if not principal.live_edge(p):
        raise TypeError("web.run / web.query chỉ nhận người gọi cấp ở cửa HTTP cho CHÍNH request này (web.caller, web.Listener, web.link_caller) — không "
                        "nhận người gọi tự dựng / đã sửa, giữ lại từ request khác, quyền hệ thống hay quyền người khác nạp bằng principal_of (0130)")
    return p


async def run(p: Principal, name: str, inp: dict[str, Any], *, key: str | None = None, files: dict[str, bytes | bytearray] | None = None,
              quiet: bool = False) -> executor.Result:
    """Chạy LỆNH qua executor bằng người gọi lõi cấp."""
    return await executor.run(_issued(p), name, inp, key=key, files=files, quiet=quiet)


async def query(p: Principal, name: str, inp: dict[str, Any], *, quiet: bool = False) -> executor.Result:
    """Chạy TRUY VẤN qua executor bằng người gọi lõi cấp."""
    return await executor.query(_issued(p), name, inp, quiet=quiet)


LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# Mã lỗi MÁY ĐỌC (đi kèm câu tiếng Việt cho người): app quyết định theo mã, không theo chữ — chữ đổi được mà app không vỡ.
ERROR_HEADER = "X-Barista-Error"
NOT_MEMBER = "not_member"   # phiên còn nhưng không còn là thành viên đang hoạt động của công ty đang chọn ⇒ app xoá phiên, về trang đăng nhập
NOT_MEMBER_TEXT = "Không phải thành viên đang hoạt động của công ty này"


def dev_allowed(request: HTTPConnection) -> bool:
    """Đăng nhập dev: từ chính máy chạy core; hoặc bản demo (BARISTA_DEMO_KEY) khi gửi đúng X-Demo-Key — so khớp thời gian hằng."""
    if not settings.dev_auth:
        return False
    if request.client is not None and request.client.host in LOOPBACK:
        return True
    given = request.headers.get("x-demo-key", "")
    return bool(settings.demo_key) and hmac.compare_digest(given.encode(), settings.demo_key.encode())


def _tenant(x_tenant_id: str | None) -> UUID:
    if not x_tenant_id:
        raise HTTPException(401, "Thiếu X-Tenant-Id (chọn công ty)")
    try:
        return UUID(x_tenant_id)
    except ValueError as e:
        raise HTTPException(401, "X-Tenant-Id không hợp lệ") from e


@asynccontextmanager
async def _owner() -> AsyncIterator[Conn]:
    """Kết nối pool owner cho lượt xác thực — đo thời gian CHỜ mượn (``owner-wait``: pool 3 kết nối kín ⇒ request xếp hàng)."""
    t0 = time.perf_counter()
    async with db.owner.connection() as conn:
        timing.add("owner-wait", time.perf_counter() - t0)
        yield conn


async def current(x_dev_user: str | None, x_tenant_id: str | None, request: HTTPConnection, *, apps: bool = False) -> Principal:
    """Người gọi ở CỬA HTTP (loại EDGE — route của gói chỉ nhận loại này). Chi tiết: ``_resolve``."""
    return principal.edge(await _resolve(x_dev_user, x_tenant_id, request, apps=apps))


async def _resolve(x_dev_user: str | None, x_tenant_id: str | None, request: HTTPConnection, *, apps: bool = False) -> Principal:
    """Ai đang gọi: phiên đăng nhập thật (``Authorization: Bearer``) hoặc — chỉ khi bật dev và gọi từ chính máy — header dev.
    Quyền LUÔN nạp lại từ DB mỗi request theo công ty đang chọn (khoá thành viên có hiệu lực ngay, không chờ phiên hết hạn).
    ``apps``: đường này nhận token của APP TỪ XA (0140) — chỉ API lệnh / truy vấn / danh mục thao tác; mọi đường khác (trợ lý, tệp, trao đổi…) từ chối."""
    bearer = request.headers.get("authorization", "")
    if bearer[:7].lower() == "bearer " and remote_apps.is_app_token(bearer[7:].strip()):
        return await _app_caller(bearer[7:].strip(), x_tenant_id, apps)
    if bearer[:7].lower() == "bearer ":
        tid = _tenant(x_tenant_id)
        async with _owner() as conn:
            with timing.span("session"):
                uid = await sessions.user_for(conn, bearer[7:].strip())
            if uid is None:
                raise HTTPException(401, "Phiên đăng nhập đã hết hạn hoặc bị thu hồi — đăng nhập lại")
            with timing.span("principal"):
                p = await auth.load(conn, tid, user_id=uid)
        if p is None:
            raise HTTPException(403, NOT_MEMBER_TEXT, headers={ERROR_HEADER: NOT_MEMBER})
        return p
    if not settings.dev_auth:
        raise HTTPException(401, "Chưa đăng nhập")
    if not dev_allowed(request):
        raise HTTPException(401, "Đăng nhập dev chỉ nhận từ chính máy chạy core (hoặc bản demo có khoá)")
    if not x_dev_user or not x_tenant_id:
        raise HTTPException(401, "Thiếu X-Dev-User / X-Tenant-Id")
    tid = _tenant(x_tenant_id)
    async with _owner() as conn:
        with timing.span("principal"):
            p = await auth.load(conn, tid, email=x_dev_user)
    if p is None:
        raise HTTPException(403, NOT_MEMBER_TEXT, headers={ERROR_HEADER: NOT_MEMBER})
    return p


async def _app_caller(token: str, x_tenant_id: str | None, apps: bool) -> Principal:
    if not apps:
        raise HTTPException(403, "Token ứng dụng chỉ dùng cho API lệnh / truy vấn (/v1/commands, /v1/queries, /v1/ops)")
    try:
        got = await remote_apps.principal_for(token)
    except remote_apps.AppRefused as e:
        raise HTTPException(403, str(e)) from e
    if got is None:
        raise HTTPException(401, "Token ứng dụng hết hạn hoặc bị thu hồi — xin token mới bằng khoá app",
                            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
    p, app_id = got
    if x_tenant_id and x_tenant_id != str(p.tenant_id):
        raise HTTPException(403, "Token ứng dụng thuộc công ty khác")
    if not ratelimit.allow(str(app_id), "app"):
        raise HTTPException(429, "Ứng dụng gọi quá nhiều trong thời gian ngắn — chậm lại rồi thử lại", headers={"Retry-After": "60"})
    return p


async def caller(request: HTTPConnection) -> Principal:
    """Người gọi của một route do module khai — lõi đã xác thực trước handler (``require_caller``) thì dùng lại, không nạp quyền hai lần;
    không thì như ``current`` với tiêu đề của chính request (không nhận token app từ xa)."""
    got = getattr(request.state, "principal", None)
    if principal.live_edge(got):
        return got  # type: ignore[return-value]
    return await current(request.headers.get("x-dev-user"), request.headers.get("x-tenant-id"), request)


async def require_caller(request: Request) -> None:
    """Cửa của MỌI route module khai không ``public``: xác thực trước handler (thiếu / sai ⇒ 401 / 403), giữ người gọi cho ``caller`` / ``Listener``.
    Đọc số đếm "quyền đã đổi" TRƯỚC khi nạp: quyền đổi trong lúc nạp ⇒ kênh thấy số khác ở tín hiệu đầu tiên và nạp lại (không giữ quyền cũ ~60 giây)."""
    tenant = request.headers.get("x-tenant-id")
    request.state.auth_epoch = live.hub.auth_epoch(tenant or "")
    request.state.principal = await current(request.headers.get("x-dev-user"), tenant, request)


def result(r: executor.Result) -> JSONResponse:
    with timing.span("json"):
        return JSONResponse(r.to_json(), status_code=r.http_status)


# ── Link gửi khách (0064): KHÔNG đăng nhập — quyền = cầm đúng link; thao tác theo quy ước "<module>.link_<việc>" của module sở hữu loại link ──

def public_url(path: str) -> str:
    """Địa chỉ CÔNG KHAI của core cho một đường dẫn (vd link gửi khách ``/v1/compose/shared/<mã>``) — cấu hình của bản cài (``BARISTA_PUBLIC_API_URL``)."""
    return f"{settings.public_api_url.rstrip('/')}/{path.lstrip('/')}"


def app_url(path: str) -> str:
    """Địa chỉ CÔNG KHAI của WEB app cho một đường dẫn (``BARISTA_WEB_URL`` của bản cài) — vd trang nhà cung cấp OAuth trả người dùng về
    (``/oauth/<module>``, api 1.67): trang chạy trong phiên đăng nhập của người dùng, gọi lệnh của gói bằng chính quyền người đó."""
    return f"{settings.web_url.rstrip('/')}/{path.lstrip('/')}"


def link_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:32]   # khoá đếm theo mã băm — mã rõ không nằm trong bộ nhớ đếm


async def link_caller(token: str, kind: str) -> Principal:
    """Người cầm link (đếm tần suất theo loại việc ``kind``); link sai / hết hạn / thu hồi ⇒ 404 không nói lý do."""
    if not ratelimit.allow(link_key(token), kind):
        raise HTTPException(429, "Gửi quá nhiều trong thời gian ngắn — đợi một lúc rồi thử lại")
    p = await links.resolve(token)
    if p is None or p.link_target is None:
        raise HTTPException(404, "Link không còn hiệu lực — liên hệ bên gửi link để nhận link mới")
    return p


def link_op(p: Principal, action: str) -> str:
    return f"{(p.link_target or '').split('.')[0]}.link_{action}"


# ── Kênh tức thì (SSE, 0022 / 0044 / 0115): tín hiệu chỉ đánh thức, dữ liệu đọc lại qua executor bằng quyền người nghe ──
# Kết quả kiểm lại quyền: "ok" · "gone" (mất quyền đọc / không còn là thành viên) · "auth" (phiên hết hạn — app đăng nhập lại) · "retry" (lỗi tạm thời
# — app nối lại sau, KHÔNG xoá thứ đang hiện). Chỉ "gone" là mất quyền thật.
GONE, AUTH, RETRY = b"event: gone\ndata: {}\n\n", b"event: auth\ndata: {}\n\n", b"event: retry\ndata: {}\n\n"
ENDED = b"event: ended\ndata: {}\n\n"
CHANGED = b"event: changed\ndata: {}\n\n"
PING = b": ping\n\n"
EVENT = {"gone": GONE, "auth": AUTH, "retry": RETRY}
PRINCIPAL_TTL = 60.0   # giây — giữ quyền nạp sẵn của người nghe tối đa chừng này (phiên hết hạn theo giờ được phát hiện trong khoảng này)
DEBOUNCE = 0.25        # giây — gom tín hiệu dồn dập (thả / bỏ cảm xúc, ghim liên tục) thành một lần kiểm + một lần báo
SIGNAL_SETTLE = 0.03   # giây — gom vài tín hiệu tới cùng lúc thành một lần đọc (ngắn: nối cuộc gọi cần nhanh)
RESYNC = live.RESYNC   # hàng đợi hộp tin tràn ⇒ "đọc lại cả hộp"


def sse(lines: AsyncIterator[bytes]) -> StreamingResponse:
    return StreamingResponse(lines, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def state_of(r: executor.Result) -> str:
    return "ok" if r.ok else "gone" if r.status in ("not_found", "denied") else "retry"


@contextmanager
def subscribe(key: str) -> Iterator[asyncio.Queue[None]]:
    """Nghe tín hiệu của một khoá (``thread:<mã>``, ``<module>:<công ty>:<mã>``…) — chỉ đánh thức, không mang dữ liệu."""
    with live.hub.subscribe_key(key) as q:
        yield q


@contextmanager
def inbox_feed(p: Principal) -> Iterator[asyncio.Queue[str]]:
    """Mã các luồng vừa đổi trong hộp tin của người này (0115) — bên nghe kiểm quyền từng mã trước khi báo."""
    with live.hub.feed(f"{live.FEED}{p.tenant_id}:{p.user_id}") as q:
        yield q


class Listener:
    """Người nghe một kênh tức thì (0115). Quyền nạp MỘT lần rồi dùng lại tới ``PRINCIPAL_TTL`` giây hoặc tới khi công ty có thay đổi quyền /
    thành viên / phiên (``live.hub.auth_epoch`` — lệnh quản trị, đăng xuất báo qua NOTIFY): nạp quyền chạy trên pool CHỦ (3 kết nối, chung với mọi
    lượt đăng nhập), nên không chạy cho mỗi tín hiệu của mỗi người nghe. Kiểm thấy-đối-tượng vẫn chạy MỖI tín hiệu, trên pool app, bằng RLS."""

    def __init__(self, request: Request) -> None:
        self.dev_user, self.tenant, self.request = request.headers.get("x-dev-user"), request.headers.get("x-tenant-id"), request
        self.p: Principal | None = None
        self.at = 0.0
        self.epoch = -1
        got = getattr(request.state, "principal", None)   # lõi vừa xác thực trước handler ⇒ dùng luôn (một lần nạp quyền mỗi kênh)
        if principal.live_edge(got):
            # số đếm đọc TRƯỚC lúc nạp (require_caller) — không đọc lại ở đây: đổi quyền xen giữa thì tín hiệu đầu tiên đã thấy lệch và nạp lại
            self.p, self.at, self.epoch = got, time.monotonic(), getattr(request.state, "auth_epoch", -1)

    async def principal(self) -> Principal:
        epoch = live.hub.auth_epoch(self.tenant or "")
        if self.p is None or epoch != self.epoch or time.monotonic() - self.at > PRINCIPAL_TTL:
            self.p = await current(self.dev_user, self.tenant, self.request)
            self.at, self.epoch = time.monotonic(), epoch
        return self.p

    async def fetch(self, op: str, inp: dict[str, Any]) -> tuple[str, Any]:
        """Chạy truy vấn ``op`` bằng quyền người nghe (``quiet``: không thành dòng nhật ký mỗi tín hiệu) ⇒ (trạng thái, kết quả)."""
        try:
            p = await self.principal()
        except HTTPException as e:
            self.p = None
            return ("auth" if e.status_code == 401 else "gone"), None   # 403 = không còn là thành viên đang hoạt động
        except Exception:   # lỗi tạm khi nạp quyền (DB / mạng) ⇒ báo app thử lại, không đóng luồng; log để lỗi dai dẳng không im lặng
            log.warning("người nghe: nạp quyền lỗi — báo thử lại", exc_info=True)
            self.p = None
            return "retry", None
        r = await query(p, op, inp, quiet=True)
        return state_of(r), r.output

    async def access(self, op: str, inp: dict[str, Any]) -> str:
        """Người nghe CÒN được đọc không: "ok" · "gone" · "auth" · "retry"."""
        return (await self.fetch(op, inp))[0]


class LinkListener:
    """Người ngoài nghe qua link: mã link kiểm lại (hạn / thu hồi) tối đa mỗi ``PRINCIPAL_TTL`` giây; thu hồi có hiệu lực NGAY ở RLS của đối
    tượng (đọc trạng thái lời mời mỗi lần)."""

    def __init__(self, token: str, p: Principal) -> None:
        self.token, self.p, self.at = token, p, time.monotonic()

    async def principal(self) -> Principal | None:
        if self.p is None or time.monotonic() - self.at > PRINCIPAL_TTL:
            self.p, self.at = await links.resolve(self.token), time.monotonic()
        return self.p

    async def fetch(self, op: str, inp: dict[str, Any]) -> tuple[str, Any]:
        try:
            p = await self.principal()
        except Exception:   # lỗi tạm khi kiểm lại mã link ⇒ báo thử lại, không đóng luồng; log để lỗi dai dẳng không im lặng
            log.warning("người nghe link: kiểm mã lỗi — báo thử lại", exc_info=True)
            return "retry", None
        if p is None:
            return "gone", None
        r = await query(p, op, inp, quiet=True)
        return state_of(r), r.output


async def settle(q: asyncio.Queue[Any], into: set[Any] | None = None) -> None:
    """Chờ ``DEBOUNCE`` rồi gom mọi tín hiệu đang chờ (nhiều lần bấm liên tiếp ⇒ một lần kiểm quyền, một lần báo)."""
    await asyncio.sleep(DEBOUNCE)
    while not q.empty():
        x = q.get_nowait()
        if into is not None:
            into.add(x)


async def relay(request: Request, key: str, after: int, fetch: Callable[[int], Awaitable[tuple[str, Any]]]) -> AsyncIterator[bytes]:
    """Vòng chung của kênh TÍN HIỆU (0114): đọc thư mới (kiểm quyền trong ``fetch`` — trả {signals, active, peer?}) ⇒ ``signal`` từng thư (data có
    ``id`` để nối lại đúng chỗ); người kia đổi tư cách ⇒ ``peer`` (nhãn rỗng = không còn ai được nối); kết thúc ⇒ ``ended``; mất quyền / phiên /
    lỗi tạm ⇒ ``gone`` / ``auth`` / ``retry`` rồi đóng. Rảnh 20 giây ⇒ đọc lại (kiểm lại quyền) + ping."""
    peer: str | None = None
    with live.hub.subscribe_key(key) as q:
        while not await request.is_disconnected():
            state, out = await fetch(after)
            if state != "ok":
                yield EVENT[state]
                return
            if out.get("peer", "") != peer:   # người kia vào / bị bỏ (thu hồi, rút đồng ý, mất quyền, trả chỗ) ⇒ trang bỏ kết nối cũ ngay
                peer = out.get("peer", "")
                yield f"event: peer\ndata: {json.dumps({'peer': peer})}\n\n".encode()
            for s in out["signals"]:
                after = s["id"]
                yield f"event: signal\ndata: {json.dumps(executor.jsonable(s))}\n\n".encode()
            if not out["active"]:
                yield ENDED
                return
            if len(out["signals"]) >= 200:   # còn thư: đọc tiếp ngay
                continue
            try:
                await asyncio.wait_for(q.get(), 20)
                await asyncio.sleep(SIGNAL_SETTLE)
            except TimeoutError:
                yield PING


# ── Tệp gửi lên dạng multipart, đọc THEO KHÚC ──

class _TooBig(Exception):
    pass


class Upload:
    """Đọc ``multipart/form-data`` THEO KHÚC (python-multipart, không chép cả thân vào bộ nhớ trước): trường chữ ≤ 16 KB, MỘT tệp ≤ giới hạn, băm
    sha256 ngay khi đọc. Vượt giới hạn ⇒ dừng đọc ngay (``_TooBig``), không chờ hết thân."""

    FIELD_MAX = 16 * 1024

    def __init__(self, file_max: int) -> None:
        self.file_max = file_max
        self.fields: dict[str, str] = {}
        self.filename: str | None = None
        self.data = bytearray()
        self.sha = hashlib.sha256()
        self._headers: dict[bytes, bytes] = {}
        self._hname = bytearray()
        self._hval = bytearray()
        self._name = ""
        self._is_file = False
        self._buf = bytearray()

    def callbacks(self) -> dict[str, Any]:
        def part_begin() -> None:
            self._headers, self._buf, self._name, self._is_file = {}, bytearray(), "", False

        def header_field(data: bytes, start: int, end: int) -> None:
            self._hname += data[start:end]

        def header_value(data: bytes, start: int, end: int) -> None:
            self._hval += data[start:end]

        def header_end() -> None:
            self._headers[bytes(self._hname).lower()] = bytes(self._hval)
            self._hname, self._hval = bytearray(), bytearray()

        def headers_finished() -> None:
            _, opts = parse_options_header(self._headers.get(b"content-disposition", b""))
            self._name = opts.get(b"name", b"").decode("utf-8", "replace")
            if b"filename" in opts:
                if self.filename is not None:
                    raise ValueError("chỉ một tệp mỗi lần gửi")
                self._is_file = True
                self.filename = opts[b"filename"].decode("utf-8", "replace")

        def part_data(data: bytes, start: int, end: int) -> None:
            chunk = data[start:end]
            if self._is_file:
                if len(self.data) + len(chunk) > self.file_max:
                    raise _TooBig
                self.data += chunk
                self.sha.update(chunk)
            else:
                if len(self._buf) + len(chunk) > self.FIELD_MAX:
                    raise ValueError("trường chữ quá dài")
                self._buf += chunk

        def part_end() -> None:
            if not self._is_file and self._name:
                self.fields[self._name] = self._buf.decode("utf-8")

        return {"on_part_begin": part_begin, "on_header_field": header_field, "on_header_value": header_value, "on_header_end": header_end,
                "on_headers_finished": headers_finished, "on_part_data": part_data, "on_part_end": part_end}


async def multipart(request: Request, file_max: int) -> Upload:
    """Thân ``multipart/form-data`` với MỘT tệp ≤ ``file_max`` (+ vài trường chữ): sai dạng ⇒ 415 / 400; quá cỡ (khai báo hoặc thật) ⇒ 413 ngay."""
    kind, opts = parse_options_header(request.headers.get("content-type", ""))
    if kind != b"multipart/form-data" or not opts.get(b"boundary"):
        raise HTTPException(415, "Cần gửi dạng multipart/form-data")
    raw_len = request.headers.get("content-length")
    try:
        declared = int(raw_len) if raw_len is not None else 0
    except ValueError as e:
        raise HTTPException(400, "Content-Length không hợp lệ") from e
    limit = file_max + 64 * 1024   # tệp + vài trường chữ + ranh giới multipart
    too_big = HTTPException(413, f"Tệp quá {file_max // (1024 * 1024)} MB")
    if declared < 0 or declared > limit:
        raise too_big
    up = Upload(file_max)
    parser = MultipartParser(opts[b"boundary"], cast(Any, up.callbacks()))
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise too_big
            parser.write(chunk)
        parser.finalize()
    except _TooBig as e:
        raise too_big from e
    except (ValueError, UnicodeDecodeError) as e:   # MultipartParseError là ValueError
        raise HTTPException(400, f"Thân yêu cầu không hợp lệ: {e}") from e
    return up


__all__ = ["AUTH", "CHANGED", "EVENT", "GONE", "PING", "RESYNC", "RETRY", "Body", "HTTPException", "JSONResponse", "LinkListener", "Listener",
           "Request", "Response", "StreamingResponse", "Upload", "caller", "current", "dev_allowed", "inbox_feed", "link_caller", "link_key",
           "link_op", "multipart", "query", "relay", "result", "run", "settle", "sse", "state_of", "subscribe"]
