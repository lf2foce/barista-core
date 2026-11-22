"""HTTP — mỏng: xác thực ⇒ ``Principal`` ⇒ executor. Không route nào chạm DB nghiệp vụ trực tiếp."""

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, unquote
from uuid import UUID

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

import barista.modules  # noqa: F401  — đăng ký mọi module
from barista import mcp_server, production, sweeps, work_runtime
from barista.agent import chat, decisions, desk, field_note, morning, turns, voice
from barista.agent import usage as metering
from barista.agent.llm import AIDisabled
from barista.config import settings
from barista.db import db
from barista.kernel import (
    alerts,
    auth,
    email_login,
    events,
    executor,
    files,
    imports,
    live,
    oauth,
    oidc,
    packs,
    principal,
    push,
    ratelimit,
    registry,
    remote_apps,
    sessions,
    standing,
    timing,
    ui,
    usage,
    vocab,
    web,
    webhooks,
)
from barista.kernel import preview as previews
from barista.kernel.errors import Invalid
from barista.kernel.fields import company_country
from barista.kernel.forms import form_spec
from barista.kernel.migrate import migrate
from barista.kernel.web import current, dev_allowed
from barista.workspaces import PICKER_ORDER, manifest, personal, picker_card, picker_titles

log = logging.getLogger("barista.http_api")

if settings.dev_auth and settings.eval_noise_tools > 0:   # CHỈ dev / eval (0095): mô phỏng danh mục lớn — bản thật không bao giờ có
    from barista import evalnoise

    log.warning("EVAL: thêm %d công cụ nhiễu", evalnoise.install(settings.eval_noise_tools))
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    production.check_config()   # BARISTA_ENV=production còn cờ dev / thiếu đăng nhập thật ⇒ không lên (ADR 0194) — trước khi chạm DB
    packs.check_config()   # BARISTA_PACKS nêu khoá gói lạ ⇒ không lên (healthcheck hỏng, Swarm giữ bản cũ) — trước khi chạm DB
    if settings.migrate_on_start:
        await migrate(settings.owner_database_url)
    await files.check_config()   # kho tệp thiếu cấu hình ⇒ không lên (0122) — healthcheck hỏng, Swarm giữ bản cũ
    await db.open(settings.database_url, settings.owner_database_url)
    tasks = [asyncio.create_task(events.run_forever(settings.dispatch_interval_s)), asyncio.create_task(alerts.watch_forever())]
    if settings.leadflow_sweep_s > 0:
        tasks.append(asyncio.create_task(sweeps.run_forever(settings.leadflow_sweep_s)))
    if settings.webhook_interval_s > 0:   # webhook ra app từ xa (0140)
        tasks.append(asyncio.create_task(webhooks.run_forever(settings.webhook_interval_s)))
    if settings.files_sweep_s > 0:   # dọn tệp (0122): sổ mồ côi ⇒ release, quá hạn lưu ⇒ xoá, đối tượng không sổ > 1 giờ ⇒ xoá
        tasks.append(asyncio.create_task(files.run_forever(settings.files_sweep_s)))
    if settings.morning_interval_s > 0:   # đề xuất buổi sáng (0074 A)
        tasks.append(asyncio.create_task(morning.run_forever(settings.morning_interval_s)))
    if settings.notify_listen:   # thông báo tức thì cho app + gửi đẩy (quyết định 0044)
        tasks.append(asyncio.create_task(live.listen_forever(settings.owner_database_url)))
        tasks.append(asyncio.create_task(live.push_forever(push.sender_from_settings())))
    if settings.work_engine:
        work_runtime.init()
        if t := await work_runtime.launch():
            tasks.append(t)
    try:
        async with mcp_server.server.session_manager.run():   # MCP chỉ đọc (0093) — gắn ở cuối file
            yield
    finally:
        # lượt trợ lý đang chạy tách khỏi HTTP (F6, ADR 0200): chờ có hạn, còn chạy ⇒ dừng + lưu trạng thái "bị ngắt" — TRƯỚC khi đóng pool
        done, cut = await turns.drain(settings.shutdown_turn_grace_s, settings.shutdown_turn_settle_s)
        if done or cut:
            log.info("tắt máy chủ: %d lượt trợ lý xong, %d lượt bị ngắt", done, cut)
        for t in tasks:
            t.cancel()
        if settings.work_engine:
            work_runtime.shutdown()
        await usage.flush()   # số đếm lượt đọc còn trong bộ nhớ (0163) — không bao giờ ném
        if not await metering.flush(wait=2.0):   # lượt dùng AI chưa ghi được (F10) — còn sót ⇒ log
            log.error("tắt máy chủ: còn %d lượt dùng AI chưa ghi được", metering.pending())
        await db.close()


app = FastAPI(title="Barista core", version="0.1.0", lifespan=lifespan)


class _RequestLease:
    """Mỗi request HTTP / websocket một hợp đồng thuê (0130): người gọi cấp ở cửa trong request này (``web.current``, link) chỉ dùng được cho route
    của gói tới khi phản hồi XONG (cả luồng SSE). Giữ lại dùng sau / ở request khác ⇒ hết hạn."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.inner(scope, receive, send)
            return
        lease = principal.Lease()
        token = principal.LEASE.set(lease)
        try:
            await self.inner(scope, receive, send)
        finally:
            lease.active = False
            principal.LEASE.reset(token)


app.add_middleware(_RequestLease)


def _body_caps() -> dict[str, int]:
    """Trần thân request của các đường trợ lý (F5) — kiểm TRƯỚC khi FastAPI đệm thân vào bộ nhớ."""
    audio = field_note.AUDIO_BYTES + 64 * 1024
    return {"/v1/assistant/chat/stream": settings.chat_body_max_bytes, "/v1/assistant/proposals/decide": 1024 * 1024,
            "/v1/assistant/field_note": audio, "/v1/assistant/transcribe": audio}


class _BodyCap:
    """Đường trợ lý: thân request quá trần (khai ở Content-Length hoặc thật sự gửi) ⇒ 413 ngay, không đệm hết vào bộ nhớ (review 12/11 F5). Thân trong trần
    được đọc một lần rồi chuyển nguyên cho route; các đường khác đi thẳng (tải tệp có trần riêng ở ``web.multipart``)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        cap = _body_caps().get(scope.get("path", "")) if scope["type"] == "http" else None
        if not cap:
            await self.inner(scope, receive, send)
            return
        declared = next((v for k, v in scope.get("headers", []) if k == b"content-length"), None)
        try:
            too_big = declared is not None and int(declared) > cap
        except ValueError:
            too_big = False
        chunks: list[bytes] = []
        total, more = 0, not too_big
        while more:
            msg = await receive()
            if msg["type"] != "http.request":   # người gửi bỏ ngang
                return
            total += len(msg.get("body", b""))
            if total > cap:
                too_big = True
                break
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        if too_big:
            mb = cap / (1024 * 1024)
            await JSONResponse({"detail": f"Yêu cầu quá lớn (tối đa {mb:.0f} MB) — bớt ảnh / rút ngắn đoạn ghi âm rồi gửi lại."}, 413)(scope, receive, send)
            return
        body, replayed = b"".join(chunks), False

        async def replay() -> Any:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()   # sau thân: chờ tín hiệu người gửi ngắt (luồng trả lời dùng để biết app đã đóng)

        await self.inner(scope, replay, send)


app.add_middleware(_BodyCap)


class _KeepAliveHint:
    """``Keep-Alive: timeout=…`` trên phản hồi khi bản cài giữ kết nối rảnh lâu (``settings.keep_alive_s``) — client HTTP của BFF (undici) chỉ giữ kết nối
    lâu hơn 4 giây khi server báo. Báo ngắn hơn mức uvicorn thật giữ 5 giây: client đóng trước, không dùng lại kết nối server vừa đóng."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or settings.keep_alive_s <= 5:
            await self.inner(scope, receive, send)
            return
        hint = f"timeout={int(settings.keep_alive_s - 5)}".encode()

        async def hinted(msg: Any) -> None:
            if msg["type"] == "http.response.start":
                msg = {**msg, "headers": [*msg.get("headers", []), (b"keep-alive", hint)]}
            await send(msg)

        await self.inner(scope, receive, hinted)


app.add_middleware(_KeepAliveHint)
app.add_middleware(alerts.ErrorWatch)   # đếm 5xx / lỗi chưa bắt cho cảnh báo lỗi dồn dập của nhà vận hành (kernel/alerts.py)
app.add_middleware(timing.ServerTiming)   # thêm SAU CÙNG = lớp NGOÀI CÙNG: chặng "app" gồm cả các lớp trên


@app.post("/v1/apps/token")
async def app_token(request: Request) -> JSONResponse:
    """Client credentials (RFC 6749 §4.4) cho APP TỪ XA (0140): ``grant_type=client_credentials`` + ``client_id`` / ``client_secret`` (form hoặc
    HTTP Basic) [+ ``scope`` = "tài nguyên:hành động" cách nhau dấu cách] ⇒ token truy cập ngắn hạn. Không có token làm mới."""
    form = dict(await request.form()) if "form" in request.headers.get("content-type", "") else {}
    cid, secret = str(form.get("client_id") or ""), str(form.get("client_secret") or "")
    auth_h = request.headers.get("authorization", "")
    if auth_h[:6].lower() == "basic ":
        try:
            cid, _, secret = base64.b64decode(auth_h[6:].strip()).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
    if form.get("grant_type") != "client_credentials":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    if not cid or not secret:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    ip = request.client.host if request.client else ""
    # lần SAI đếm theo (client_id, địa chỉ gọi): kẻ dò khoá ở máy khác không khoá được app thật (review 08/11); lần ĐÚNG đếm theo client_id
    fail_key = hashlib.sha256(f"{cid}|{ip}".encode()).hexdigest()[:32]
    ok_key = hashlib.sha256(cid.encode()).hexdigest()[:32]
    if ratelimit.blocked(fail_key, "app_token_fail") or ratelimit.blocked(ok_key, "app_token"):
        return JSONResponse({"error": "slow_down", "error_description": "Xin token quá nhiều / sai khoá quá nhiều lần — đợi rồi thử lại"}, status_code=429)
    try:
        tok = await remote_apps.issue_token(cid, secret, str(form.get("scope") or ""), ip=ip)
    except remote_apps.TokenRefused as e:
        ratelimit.hit(fail_key, "app_token_fail")
        return JSONResponse({"error": e.error, "error_description": str(e)}, status_code=401 if e.error == "invalid_client" else 400)
    ratelimit.hit(ok_key, "app_token")
    return JSONResponse(tok, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


type DevUser = Annotated[str | None, Header()]
type TenantId = Annotated[str | None, Header()]


_result = web.result


def _xlsx(data: bytes, filename: str) -> Response:
    return Response(data, media_type=XLSX, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}.xlsx"})


DRAIN_FLAG = Path(os.environ.get("BARISTA_DRAIN_FILE", "/tmp/barista-draining"))


@app.get("/health")
async def health() -> JSONResponse:
    """Cho Swarm / Traefik: DB trả lời trong 2 giây, commit đang chạy; đang rút (serve.py nhận SIGTERM) ⇒ 503 để thôi nhận request mới."""
    if await asyncio.to_thread(DRAIN_FLAG.exists):
        return JSONResponse({"status": "draining", "release": settings.release}, status_code=503)
    try:
        async with asyncio.timeout(2), db.owner.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception:   # mọi lỗi kết nối / quá 2 giây ⇒ 503 cho cân bằng tải; log để quản trị thấy lý do
        log.warning("healthz: DB không trả lời", exc_info=True)
        return JSONResponse({"status": "db_unavailable", "release": settings.release}, status_code=503)
    return JSONResponse({"status": "ok", "release": settings.release})


@app.get("/v1/auth/config")
async def auth_config(request: Request) -> dict[str, Any]:
    """App hỏi cách đăng nhập: Google (client ID app dùng) và có bật đăng nhập dev không (chỉ máy chạy core)."""
    dev = dev_allowed(request)
    return {"google": {"client_id": settings.google_app_client_id, "web_client_id": settings.google_web_client_id or None}
            if (settings.google_app_client_id or settings.google_web_client_id) and (settings.google_client_ids or settings.google_web_client_id) else None,
            "dev": dev,
            # 0205 §5c: đăng nhập bằng mã qua email bật khi bản cài đã cấu hình gửi thư (máy dev: mã ghi ra log) — chưa bật thì trang không hiện ô nhập
            "email": email_login.available()}


@app.post("/v1/auth/google")
async def auth_google(request: Request, body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    """id_token Google (app tự lấy bằng PKCE) ⇒ kiểm ⇒ gắn danh tính theo sub ⇒ cấp phiên. Trả mã phiên + các công ty người đó thuộc về."""
    try:
        ident = await oidc.verify_google(str(body.get("id_token") or ""), str(body.get("nonce") or ""))
    except oidc.OIDCError as e:
        log.warning("đăng nhập Google bị từ chối: %s", e)
        return JSONResponse({"detail": "Không xác thực được tài khoản Google — thử đăng nhập lại"}, status_code=401)
    async with db.owner_tx() as conn:
        try:
            uid = await sessions.bind_google(conn, ident)
        except sessions.LoginRefused as e:
            return JSONResponse({"detail": str(e)}, status_code=403)
        tenants = await sessions.tenants_of(conn, uid)
        if not tenants:
            return JSONResponse({"detail": "Tài khoản đã bị khoá ở mọi công ty — liên hệ quản trị"}, status_code=403)
        token, expires = await sessions.open_session(conn, uid, provider="google", client=request.headers.get("user-agent", ""))
    return JSONResponse(executor.jsonable({"token": token, "expires_at": expires, "email": ident.email, "tenants": tenants}))


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def _slow() -> JSONResponse:
    return JSONResponse({"detail": "Thử quá nhiều lần — đợi vài phút rồi thử lại"}, status_code=429, headers={"Retry-After": "60"})


@app.post("/v1/auth/email/start")
async def auth_email_start(request: Request, body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    """0205 §5c: xin mã đăng nhập qua email. Trả lời Y HỆT dù email có được mời hay không (không lộ ai có tài khoản)."""
    email = email_login.normalize(str(body.get("email") or ""))
    ip = request.client.host if request.client else ""
    if ratelimit.blocked(_key("email", email), "email_code") or not ratelimit.allow(_key("email-ip", ip), "email_code_ip"):
        return _slow()
    ratelimit.hit(_key("email", email), "email_code")
    try:
        await email_login.send_code(email, app="Barista")
    except email_login.EmailLoginUnavailable as e:
        return JSONResponse({"detail": str(e)}, status_code=503)
    return JSONResponse({"status": "sent"}, headers={"Cache-Control": "no-store"})


@app.post("/v1/auth/email/verify")
async def auth_email_verify(request: Request, body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    """Đúng mã ⇒ phiên đăng nhập (provider "email") + các công ty người đó thuộc về — như /v1/auth/google."""
    ip = request.client.host if request.client else ""
    if ratelimit.blocked(_key("email-verify", ip), "email_verify_fail"):
        return _slow()
    try:
        uid = await email_login.check_code(str(body.get("email") or ""), str(body.get("code") or ""))
    except email_login.CodeRefused as e:
        ratelimit.hit(_key("email-verify", ip), "email_verify_fail")
        return JSONResponse({"detail": str(e)}, status_code=401)
    async with db.owner_tx() as conn:
        tenants = await sessions.tenants_of(conn, uid)
        if not tenants:
            return JSONResponse({"detail": "Tài khoản đã bị khoá ở mọi công ty — liên hệ quản trị"}, status_code=403)
        token, expires = await sessions.open_session(conn, uid, provider="email", client=request.headers.get("user-agent", ""))
        email = await (await conn.execute("SELECT email FROM core.users WHERE id = %s", (uid,))).fetchone()
    return JSONResponse(executor.jsonable({"token": token, "expires_at": expires, "email": email["email"] if email else "", "tenants": tenants}),
                        headers={"Cache-Control": "no-store"})


@app.post("/v1/auth/logout")
async def auth_logout(request: Request) -> dict[str, str]:
    bearer = request.headers.get("authorization", "")
    if bearer[:7].lower() == "bearer ":
        async with db.owner_tx() as conn:
            await sessions.revoke(conn, bearer[7:].strip())
    return {"status": "ok"}


@app.get("/v1/dev/logins")
async def dev_logins(request: Request) -> list[dict[str, Any]]:
    if not dev_allowed(request):
        raise HTTPException(404)
    async with db.owner.connection() as conn:
        raw = await auth.dev_logins(conn)
    titles = await picker_titles(r["tenant_id"] for r in raw)   # chức danh do gói cung cấp (Module.titles); không có ⇒ nhãn suy từ vai + đội
    rows = [picker_card(r, titles.get((str(r["tenant_id"]), str(r["user_id"])), "")) for r in raw]
    if len({r["tenant"] for r in rows}) > 1:
        for r in rows:   # một người nhiều công ty ⇒ nút ghi rõ công ty
            r["hint"] = f"{r['tenant']} · {r['hint']}"
    # công ty tạo trước lên trước (công ty chính của bản cài; công ty khách tạo sau) — app chọn sẵn dòng đầu
    return executor.jsonable(sorted(rows, key=lambda r: (r["tenant_since"], PICKER_ORDER.index(r["group"]) if r["group"] in PICKER_ORDER else 9, r["name"])))


@app.get("/v1/manifest")
async def get_manifest(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> dict[str, Any]:
    p = await current(x_dev_user, x_tenant_id, request)
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT name, timezone, country FROM core.tenants WHERE id = %s", (p.tenant_id,))
        row = await cur.fetchone()
        cur = await conn.execute("SELECT name FROM core.teams WHERE id = %s", (p.home_team,))
        team = await cur.fetchone()
    return executor.jsonable(manifest(p, row["name"] if row else "", team["name"] if team else "", await personal(p),
                                      timezone=row["timezone"] if row else "", country=row["country"] if row else ""))


@app.get("/v1/ops")
async def ops(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> list[dict[str, Any]]:
    """Danh mục thao tác người này được chạy, kèm JSON schema — sẵn làm tool cho agent (giai đoạn 2)."""
    p = await current(x_dev_user, x_tenant_id, request, apps=True)
    return vocab.apply([{"name": o.name, "kind": o.kind, "title": o.title, "description": o.description, "schema": o.input.model_json_schema()}
                        for o in registry.OPS.values() if not o.internal and executor.allowed(p, o)], p.vocab)


@app.post("/v1/commands/{name}")
async def command(request: Request, name: str, body: Annotated[dict[str, Any], Body()], x_dev_user: DevUser = None, x_tenant_id: TenantId = None,
                  idempotency_key: Annotated[str | None, Header()] = None) -> JSONResponse:
    p = await current(x_dev_user, x_tenant_id, request, apps=True)
    # nút trang mang CĂN CỨ người xem đã thấy (``ui.act(basis=)`` — 0071 / 0251): app gửi lại ở ``X-Expect-Basis`` ⇒ căn cứ đổi thì ``conflict``, không chạy
    expect = executor.basis_of_token(request.headers.get("x-expect-basis"))
    # app CHỈ GHI NHẬN (0207): cấu hình người vận hành đặt trên app — không phải ô đầu vào; lệnh chạy như nạp lịch sử (không báo, không hạn, không webhook)
    return _result(await executor.run(p, name, body, key=idempotency_key, historical=p.via == "app" and p.shadow, expect_basis=expect))


# ── Link gửi khách (0064): KHÔNG đăng nhập — quyền = cầm đúng link. Người gọi via="link" chỉ chạy thao tác public của đúng loại link (executor),
# RLS chỉ mở đúng đối tượng. Thao tác theo quy ước "<module>.link_view / link_answer / link_upload" của module sở hữu loại link.
LINK_UPLOAD_MAX = 20 * 1024 * 1024


@app.get("/v1/public/links/{token}")
async def public_link(token: str) -> JSONResponse:
    p = await web.link_caller(token, "view")
    return _result(await executor.query(p, web.link_op(p, "view"), {}))


@app.post("/v1/public/links/{token}/answer")
async def public_link_answer(token: str, body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    p = await web.link_caller(token, "answer")
    return _result(await executor.run(p, web.link_op(p, "answer"), body))


@app.post("/v1/public/links/{token}/upload")
async def public_link_upload(token: str, request: Request, item_id: str, x_filename: Annotated[str | None, Header()] = None) -> JSONResponse:
    # rẻ trước: khai báo kích thước → link + tần suất → đọc thân theo khúc, dừng ngay khi vượt → hạn mức dung lượng / ngày
    if int(request.headers.get("content-length") or 0) > LINK_UPLOAD_MAX:
        raise HTTPException(413, "File quá 20 MB")
    p = await web.link_caller(token, "upload")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > LINK_UPLOAD_MAX:
            raise HTTPException(413, "File quá 20 MB")
    if not ratelimit.take_bytes(web.link_key(token), len(data)):
        raise HTTPException(429, "Đã nộp quá dung lượng cho phép trong ngày — liên hệ kế toán")
    return _result(await executor.run(p, web.link_op(p, "upload"), {"item_id": item_id, "filename": unquote(x_filename or ""),
                                                                    "data_b64": base64.b64encode(bytes(data)).decode()}))


# ── Webhook VÀO từ nhà cung cấp ngoài (0153): KHÔNG đăng nhập — quyền = chữ ký / khoá của kết nối, do thao tác nội bộ của module kiểm (một cửa:
# route chỉ đọc thân có giới hạn rồi đưa vào executor bằng quyền hệ thống của công ty trên đường dẫn). Lần sai đếm theo (kết nối, địa chỉ gọi).
HOOK_MAX = 256 * 1024


@app.post("/v1/hooks/{module}/{tenant_id}/{ref}")
async def inbound_hook(module: str, tenant_id: UUID, ref: str, request: Request) -> JSONResponse:
    op = registry.INBOUND.get(module)
    if op is None:
        raise HTTPException(404, "Không có cửa nhận webhook này")
    ip = request.client.host if request.client else ""
    key = hashlib.sha256(f"{module}|{tenant_id}|{ref}".encode()).hexdigest()[:32]
    fail_key = hashlib.sha256(f"{key}|{ip}".encode()).hexdigest()[:32]
    # từ chối TRƯỚC khi chạm DB (quá tần suất / quá cỡ) chỉ ghi log máy chủ — chưa biết kết nối có thật không; từ chối sau khi đọc thân (sai chữ ký,
    # thư cũ, thiếu trường) do thao tác của module ⇒ có dòng nhật ký thao tác (tiêu đề / thân không chép — audit=False, 0172 §2)
    if ratelimit.blocked(fail_key, "hook_fail") or not ratelimit.allow(key, "hook"):
        log.warning("webhook %s/%s: quá tần suất (ip %s)", module, tenant_id, ip)
        return JSONResponse({"error": "slow_down"}, status_code=429)
    if int(request.headers.get("content-length") or 0) > HOOK_MAX:
        log.warning("webhook %s/%s: thân quá %d byte (ip %s)", module, tenant_id, HOOK_MAX, ip)
        raise HTTPException(413, "Thân webhook quá lớn")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > HOOK_MAX:
            log.warning("webhook %s/%s: thân quá %d byte (ip %s)", module, tenant_id, HOOK_MAX, ip)
            raise HTTPException(413, "Thân webhook quá lớn")
    headers = {k.lower(): v[:1000] for k, v in list(request.headers.items())[:60]}
    r = await executor.run(principal.system_principal(tenant_id), op, {"ref": ref[:100], "headers": headers,
                                                                       "body_b64": base64.b64encode(bytes(data)).decode()})
    if not r.ok:
        ratelimit.hit(fail_key, "hook_fail")
    return _result(r)


# ── Webhook CHUNG theo bộ nối (api 1.66, bổ sung ADR 0244): nhà cung cấp chỉ cho MỘT địa chỉ cho cả app (Meta) ⇒ ``/v1/hooks/shared/<bộ nối>`` không có
# công ty trên đường dẫn. Thứ tự cố định: giới hạn tần suất ⇒ đọc thân có trần ⇒ KIỂM CHỮ KÝ bằng khoá của bản cài (trước khi chạm DB) ⇒ tra công ty
# sở hữu khoá định tuyến đã xác minh ⇒ đánh thức kết nối của TỪNG công ty bằng quyền hệ thống của nó. Không ghi dữ liệu khách ở đây — lượt kéo lấy dữ liệu.

def _shared_kind(kind: str) -> Any:
    from barista.kernel import connectors as kc
    k = kc.KINDS.get(kind)
    if k is None or not k.cls.shared_hook:
        raise HTTPException(404, "Không có cửa nhận webhook này")
    return k.cls


@app.post("/v1/hooks/shared/{kind}")
async def shared_hook(kind: str, request: Request) -> JSONResponse:
    from barista.kernel import connectors as kc
    cls = _shared_kind(kind)
    ip = request.client.host if request.client else ""
    key = hashlib.sha256(f"shared|{kind}".encode()).hexdigest()[:32]
    fail_key = hashlib.sha256(f"{key}|{ip}".encode()).hexdigest()[:32]
    if ratelimit.blocked(fail_key, "hook_fail") or not ratelimit.allow(fail_key, "hook"):
        log.warning("webhook chung %s: quá tần suất (ip %s)", kind, ip)
        return JSONResponse({"error": "slow_down"}, status_code=429)
    if int(request.headers.get("content-length") or 0) > HOOK_MAX:
        raise HTTPException(413, "Thân webhook quá lớn")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > HOOK_MAX:
            raise HTTPException(413, "Thân webhook quá lớn")
    headers = {k.lower(): v[:1000] for k, v in list(request.headers.items())[:60]}
    if why := cls.shared_check(headers, bytes(data)):
        ratelimit.hit(fail_key, "hook_fail")
        log.warning("webhook chung %s bị từ chối: %s (ip %s)", kind, why, ip)
        raise HTTPException(403, "Webhook bị từ chối")
    try:
        keys = kc.clean_keys(cls.route_keys(json.loads(bytes(data).decode("utf-8")) if data else None))
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(400, "Thân webhook không đọc được") from None
    woken = 0
    for tid in await kc.route_tenants(kind, keys):   # khoá không thuộc ai ⇒ 200 im lặng (nhà cung cấp không gửi lại mãi)
        r = await executor.run(principal.system_principal(tid), "connectors.wake_routes", {"kind": kind, "keys": keys})
        woken += int((r.output or {}).get("woken", 0)) if r.ok else 0
    return JSONResponse({"accepted": True, "woken": woken})


@app.get("/v1/hooks/shared/{kind}")
async def shared_hook_check(kind: str, request: Request) -> Response:
    cls = _shared_kind(kind)
    ip = request.client.host if request.client else ""
    fail_key = hashlib.sha256(f"{hashlib.sha256(f'shared|{kind}'.encode()).hexdigest()[:32]}|{ip}".encode()).hexdigest()[:32]
    if ratelimit.blocked(fail_key, "hook_fail") or not ratelimit.allow(fail_key, "hook"):
        return JSONResponse({"error": "slow_down"}, status_code=429)
    params = {k[:100]: v[:500] for k, v in list(request.query_params.items())[:20]}
    text = cls.shared_verify(params)
    if not isinstance(text, str):
        ratelimit.hit(fail_key, "hook_fail")
        raise HTTPException(403, "Kiểm địa chỉ không đạt")
    return PlainTextResponse(text[:1000])


HOOK_QUERY_MAX = 20   # tham số URL đưa vào truy vấn kiểm (mỗi giá trị ≤ 500 ký tự)


@app.get("/v1/hooks/{module}/{tenant_id}/{ref}")
async def inbound_hook_check(module: str, tenant_id: UUID, ref: str, request: Request) -> Response:
    """Nhà cung cấp KIỂM địa chỉ webhook trước khi đăng ký gửi thư (1.31 — Meta: ``hub.challenge``). Cùng đếm tần suất / lần sai với POST; module khai
    truy vấn kiểm (``Module.inbound(verify=)``) — truy vấn so khoá kiểm của kết nối, đạt ⇒ chữ trơn đúng như truy vấn trả (nhà cung cấp so từng ký tự)."""
    op = registry.INBOUND_VERIFY.get(module)
    if op is None:
        raise HTTPException(404, "Không có cửa nhận webhook này")
    ip = request.client.host if request.client else ""
    key = hashlib.sha256(f"{module}|{tenant_id}|{ref}".encode()).hexdigest()[:32]
    fail_key = hashlib.sha256(f"{key}|{ip}".encode()).hexdigest()[:32]
    if ratelimit.blocked(fail_key, "hook_fail") or not ratelimit.allow(key, "hook"):
        log.warning("webhook %s/%s: kiểm địa chỉ quá tần suất (ip %s)", module, tenant_id, ip)
        return JSONResponse({"error": "slow_down"}, status_code=429)
    params = {k[:100]: v[:500] for k, v in list(request.query_params.items())[:HOOK_QUERY_MAX]}
    r = await executor.query(principal.system_principal(tenant_id), op, {"ref": ref[:100], "query": params})
    text = r.output.get("text") if r.ok and isinstance(r.output, dict) else None
    if not isinstance(text, str):
        ratelimit.hit(fail_key, "hook_fail")
        return _result(r) if not r.ok else JSONResponse({"error": "Truy vấn kiểm không trả chữ"}, status_code=500)
    return PlainTextResponse(text[:1000])


@app.post("/v1/queries/{name}")
async def query(name: str, request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> JSONResponse:
    p = await current(x_dev_user, x_tenant_id, request, apps=True)
    body = await request.json() if await request.body() else {}
    compact = bool(request.query_params.get("compact"))
    # ``later=1``: app tự tải khối tải sau (``ui.b_later``) — thẻ về ngay với phần nhanh; thẻ gọn luôn đủ khối (cắt trên bản đầy đủ)
    qp = request.query_params
    # ``part=<khoá>``: chỉ một phần ``ui.part`` của thẻ; ``limit`` / ``offset``: một trang của bảng (``ui.slice_page``) — thẻ gọn luôn tính trên bản đầy đủ
    page = None
    if not compact and qp.get("limit"):
        try:
            page = ui.Paging(max(0, int(qp.get("offset") or 0)), min(max(1, int(qp["limit"])), ui.PAGE_MAX))
        except ValueError as e:
            raise HTTPException(422, "limit / offset phải là số") from e
    r = await executor.query(p, name, body, later=qp.get("later") == "1" and not compact, part=(qp.get("part") or None) if not compact else None, page=page)
    if r.ok and compact and (small := ui.compact(r.output)) is not None:
        r = executor.Result(r.status, small)   # thẻ gọn (0094 đợt 2): cùng truy vấn, cùng quyền — server cắt bớt, app vẽ bằng bộ vẽ trang sẵn có
    return _result(r)


@app.post("/v1/queries/{name}/xlsx")
async def query_xlsx(name: str, request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> Response:
    p = await current(x_dev_user, x_tenant_id, request)
    body = await request.json() if await request.body() else {}
    r = await executor.query(p, name, body)
    if not r.ok:
        return _result(r)
    if not isinstance(r.output, dict) or r.output.get("kind") != "table":
        raise HTTPException(422, "Truy vấn này không trả bảng")
    async with db.tenant_tx(p, rollback=True):   # SĐT trong file hiện theo quốc gia công ty như trên màn (0199)
        country = company_country()
    return _xlsx(ui.table_xlsx(r.output, country), r.output.get("title") or name)


@app.get("/v1/options/{name}")
async def options(request: Request, name: str, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> JSONResponse:
    """Danh sách chọn của một ô form. Ô khai ``options_with`` ⇒ app gửi kèm giá trị các trường đó qua query string (vd mã tin nhắn) — truy vấn
    vẫn chạy bằng quyền người xem, đầu vào kiểm như mọi truy vấn."""
    p = await current(x_dev_user, x_tenant_id, request)
    params = dict(request.query_params)
    q, limit_raw = params.get("q", ""), params.get("limit", "")
    op = registry.get(name)
    declared = set(op.input.model_fields) if op is not None else set()
    for k in ("q", "limit"):   # ô chọn có tìm (1.61): truy vấn tự khai q / limit thì nhận (lọc ở SQL); không thì cửa này lọc / cắt thay — truy vấn cũ vẫn chạy
        if k not in declared:
            params.pop(k, None)
    r = await executor.query(p, name, params)
    if r.ok and isinstance(r.output, list) and (q or limit_raw):
        limit = int(limit_raw) if limit_raw.isdigit() else 0
        r = executor.Result(r.status, ui.narrow_options(r.output, "" if "q" in declared else q, 0 if "limit" in declared else limit), approval_id=r.approval_id)
    return _result(r)


@app.get("/v1/forms/{name}")
async def form(request: Request, name: str, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> dict[str, Any]:
    p = await current(x_dev_user, x_tenant_id, request)
    op = registry.get(name)
    if op is None or op.internal or op.kind != "command":
        raise HTTPException(404, f"Không có lệnh {name}")
    if not p.can(op.resource, op.action):
        raise HTTPException(403, f"Không có quyền {op.title}")
    spec = form_spec(op, p.vocab, p.fields)
    _options_create(spec["fields"], p)
    return spec


def _options_create(fields: list[dict[str, Any]], p: Any) -> None:
    """Ô chọn có truy vấn khai ``creates=`` ⇒ thêm ``options_create`` {command, title} khi NGƯỜI XEM có quyền chạy lệnh tạo (chỉ THÊM trường — app cũ bỏ qua)."""
    for f in fields:
        if f.get("fields"):
            _options_create(f["fields"], p)
        cmd = registry.CREATES.get(f.get("options_query") or "")
        make = registry.get(cmd) if cmd else None
        if make is not None and make.kind == "command" and not make.internal and p.can(make.resource, make.action):
            f["options_create"] = {"command": make.name, "title": make.title}


@app.post("/v1/imports/{name}")
async def import_rows(request: Request, name: str, body: Annotated[dict[str, Any], Body()], dry_run: bool = True,
                      x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> JSONResponse:
    """``{"rows": [{tiêu đề cột: giá trị}, …]}`` — dán từ bảng tính."""
    p = await current(x_dev_user, x_tenant_id, request)
    rows = body.get("rows")
    if not isinstance(rows, list):
        raise HTTPException(422, "Cần rows: [...]")
    return _result(executor.localized(p, await imports.run_import(p, name, rows, dry_run=dry_run)))


@app.post("/v1/imports/{name}/xlsx")
async def import_xlsx(name: str, request: Request, dry_run: bool = True, x_dev_user: DevUser = None,
                      x_tenant_id: TenantId = None) -> JSONResponse:
    p = await current(x_dev_user, x_tenant_id, request)
    data = await request.body()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "File quá 20MB")
    try:
        grid = imports.read_grid(data)
    except imports.UNREADABLE as e:   # file hỏng / sai định dạng (tập lỗi đã kiểm ở imports.UNREADABLE) ⇒ một câu 422; lỗi khác không bị che; lỗi gốc nối ``from e``
        raise HTTPException(422, "Không đọc được file (.xlsx hoặc .csv UTF-8)") from e
    return _result(executor.localized(p, await imports.run_import(p, name, imports.rows_of(grid), dry_run=dry_run, grid=grid,
                                                                  file_name=unquote(request.headers.get("x-file-name", ""))[:300],
                                                                  file_sha256=hashlib.sha256(data).hexdigest())))


IMPORT_FILE_MAX = 20 * 1024 * 1024


async def _import_upload(request: Request) -> tuple[web.Upload, bytes]:
    up = await web.multipart(request, IMPORT_FILE_MAX)
    if up.filename is None or not up.data:
        raise HTTPException(422, "Thiếu file")
    return up, bytes(up.data)


@app.post("/v1/imports/{name}/analyze")
async def import_analyze(name: str, request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> JSONResponse:
    """Nhập tự khớp (0226) bước 1 — ``multipart/form-data``: ``file`` (+ ``sheet`` nếu chọn trang). Chưa chạy / ghi gì: trả các trang tính, dòng tiêu đề, ánh xạ cột → trường
    đoán được (độ tin cậy + lý do) để người xác nhận."""
    p = await current(x_dev_user, x_tenant_id, request)
    up, data = await _import_upload(request)
    try:
        sheets = imports.read_all(data)
    except imports.UNREADABLE as e:
        raise HTTPException(422, "Không đọc được file (.xlsx hoặc .csv UTF-8)") from e
    r = await imports.analyze(p, name, sheets, sheet=up.fields.get("sheet") or None, file_name=unquote(up.filename or "")[:300])
    return _result(executor.localized(p, r))


@app.post("/v1/imports/{name}/plan")
async def import_plan(name: str, request: Request, dry_run: bool = True, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> JSONResponse:
    """Nhập tự khớp bước 2-3 — ``multipart/form-data``: ``file`` + ``plan`` (JSON: sheet, header_line, mapping, resolutions, overwrite, remember). ``dry_run=true``: chạy thử
    + báo thêm mới / cập nhật / trùng / lỗi theo lý do; ``false``: nhập thật qua đúng đường lệnh."""
    p = await current(x_dev_user, x_tenant_id, request)
    up, data = await _import_upload(request)
    try:
        plan = json.loads(up.fields.get("plan") or "{}")
    except ValueError as e:
        raise HTTPException(422, "plan không phải JSON") from e
    if not isinstance(plan, dict):
        raise HTTPException(422, "plan phải là object")
    try:
        sheets = imports.read_all(data)
    except imports.UNREADABLE as e:
        raise HTTPException(422, "Không đọc được file (.xlsx hoặc .csv UTF-8)") from e
    r = await imports.run_plan(p, name, sheets, plan, dry_run=dry_run, file_name=unquote(up.filename or "")[:300], file_sha256=hashlib.sha256(data).hexdigest())
    return _result(executor.localized(p, r))


@app.get("/v1/imports/{name}/template")
async def import_template(request: Request, name: str, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> Response:
    p = await current(x_dev_user, x_tenant_id, request)
    op = registry.get(name)
    if op is None or not op.importable:
        raise HTTPException(404, f"Lệnh {name} không nhận nhập hàng loạt")
    if not p.can(op.resource, op.action):
        raise HTTPException(403, f"Không có quyền {op.title}")
    return _xlsx(imports.template_xlsx(op, p.vocab), vocab.resolve(f"Mẫu nhập - {op.title}", p.vocab))


# ── Trợ lý ──

def _ndjson(runner: Callable[[chat.Emit], Awaitable[Any]], p: principal.Principal, stop_key: str = "") -> StreamingResponse:
    """Chạy ``runner`` nền thành một lượt được quản lý (``agent/turns.py``), mỗi sự kiện một dòng NDJSON. Người dùng đóng app giữa chừng ⇒ lượt vẫn chạy
    xong và được lưu. Hết chỗ (người / công ty / tiến trình — F5) ⇒ 429 / 503 + ``Retry-After`` TRƯỚC khi mở luồng. ``stop_key`` (mã lượt app gửi) ⇒
    người đó bấm "Dừng" (``/v1/assistant/chat/stop``) huỷ được lượt: phần đã làm vẫn lưu, lượt kết thúc bằng sự kiện ``done`` có ``stopped``."""
    try:
        turns.admit(str(p.tenant_id), str(p.user_id))
    except turns.Busy as e:
        raise HTTPException(e.status, str(e), headers={"Retry-After": str(e.retry_after)}) from None
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def emit(ev: dict[str, Any]) -> None:
        await queue.put(ev)

    async def turn() -> None:
        try:
            await runner(emit)
        except asyncio.CancelledError:   # người bấm "Dừng" / quá giờ / tắt máy chủ — lượt không tự lưu phần dở (chat_turn tự lưu và không tới đây)
            why = turns.reason()
            await queue.put({"type": "done", "text": turns.interrupted_text(why), "stopped": True,
                             **({"interrupted": why} if why != turns.STOPPED else {})})
        except Exception as e:
            logging.getLogger("barista.agent").exception("lượt chat lỗi")
            slow = chat.model_timed_out(e)   # model quá hạn giữa lượt ⇒ nói đúng nguyên nhân, người hỏi lại được (0072)
            await queue.put({"type": "error", "message": chat.SLOW_MODEL if slow else "Lỗi hệ thống khi trả lời — đã ghi lại để kiểm tra."})
        finally:
            await queue.put(None)

    turns.start(turn(), tenant=str(p.tenant_id), user=str(p.user_id), stop_key=(str(p.user_id), stop_key) if stop_key else None)

    async def lines() -> AsyncIterator[bytes]:
        while (ev := await queue.get()) is not None:
            yield (json.dumps(executor.jsonable(ev), ensure_ascii=False) + "\n").encode()

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/v1/assistant/chat/stream")
async def chat_stream(request: Request, body: Annotated[dict[str, Any], Body()], x_dev_user: DevUser = None,
                      x_tenant_id: TenantId = None) -> StreamingResponse:
    """NDJSON: mỗi dòng một sự kiện (status · step · card · text · text_reset · done · error)."""
    epoch = standing.epoch_of(x_tenant_id)   # đọc TRƯỚC khi nạp người gọi: đổi quyền xen giữa ⇒ công cụ đầu tiên đã thấy lệch (F1)
    p = await current(x_dev_user, x_tenant_id, request)
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(422, "Cần nội dung câu hỏi")
    if (cap := settings.chat_message_max_chars) and len(message) > cap:   # F5: trần độ dài câu hỏi
        raise HTTPException(422, f"Câu hỏi quá dài ({len(message):,} ký tự, tối đa {cap:,}) — rút gọn, hoặc tải tài liệu vào kho tài liệu "
                                 "rồi hỏi về nó.".replace(",", "."))
    try:
        thread_id = UUID(str(body["thread_id"])) if body.get("thread_id") else None
    except ValueError as e:
        raise HTTPException(422, "thread_id không hợp lệ") from e
    images = parse_images(body.get("images"))
    documents = parse_documents(body.get("documents"))   # ADR 0249: thả vào chat, chọn "chỉ dùng trong cuộc trò chuyện này" — không vào kho / hồ sơ
    # chế độ (0098): "assistant" (mặc định) hoặc chế độ module khai (vd "builder" — trình tạo app); app_id ⇒ đang làm trên app đó
    mode = str(body.get("mode") or "assistant")
    if mode != "assistant" and mode not in registry.MODES:
        raise HTTPException(422, "Chế độ trò chuyện không hợp lệ")
    try:
        app_id = UUID(str(body["app_id"])) if body.get("app_id") else None
    except ValueError as e:
        raise HTTPException(422, "app_id không hợp lệ") from e
    context = f"Đang làm trên app mã {app_id} — đọc định nghĩa hiện tại trước khi sửa." if app_id else ""
    # hỏi trợ lý từ một trao đổi đang mở (0115): app gửi loại + mã đối tượng ⇒ trợ lý đọc đúng luồng đó bằng quyền người hỏi (vẫn qua RLS)
    raw_subject = body.get("subject")
    subj: dict[str, Any] = raw_subject if isinstance(raw_subject, dict) else {}
    if mode == "assistant" and str(subj.get("kind")) in registry.SUBJECTS:
        try:
            context = (f"người dùng đang mở trao đổi trên {registry.SUBJECTS[str(subj['kind'])][0].lower()} “{str(subj.get('title') or '')[:120]}” "
                       f"(subject_kind={subj['kind']}, subject_id={UUID(str(subj.get('id')))}). Câu hỏi về “trao đổi này” là về luồng đó.")
        except ValueError:
            pass
    key = str(body.get("turn_key") or "")[:64]   # mã lượt app tạo ⇒ bấm "Dừng" được
    held = standing.for_request(request.headers, p, epoch)   # kiểm lại tư cách trước mỗi công cụ của lượt (khoá / đổi vai / đăng xuất giữa lượt)
    return _ndjson(lambda emit: chat.chat_turn(p, message, emit=emit, thread_id=thread_id, images=images, mode=mode, context=context,
                                               standing=held, documents=documents), p, stop_key=key)


@app.post("/v1/assistant/chat/stop")
async def chat_stop(request: Request, body: Annotated[dict[str, Any], Body()], x_dev_user: DevUser = None,
                    x_tenant_id: TenantId = None) -> dict[str, Any]:
    """Người dùng bấm "Dừng": huỷ lượt đang chạy CỦA CHÍNH họ (theo mã lượt app gửi lúc hỏi). Phần đã làm vẫn lưu; lệnh đã chạy không bị hoàn tác."""
    p = await current(x_dev_user, x_tenant_id, request)
    return {"stopped": turns.stop((str(p.user_id), str(body.get("turn_key") or "")[:64]))}


@app.post("/v1/assistant/field_note")
async def assistant_field_note(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None,
                               thread_id: UUID | None = None) -> StreamingResponse:
    """Nói ghi chú hiện trường (0074 D): thân request là ĐOẠN GHI ÂM (Content-Type audio/…). NDJSON như chat, dòng đầu ``transcript`` (chữ chép
    từ giọng nói), sau đó lượt trợ lý soạn đề xuất. Âm thanh chỉ ở trong bộ nhớ lúc chép lời, không lưu."""
    epoch = standing.epoch_of(x_tenant_id)
    p = await current(x_dev_user, x_tenant_id, request)
    media_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if media_type not in field_note.AUDIO_TYPES:
        raise HTTPException(422, "Cần đoạn ghi âm (wav / m4a / mp3 / ogg / webm)")
    audio = await request.body()
    if not audio or len(audio) > field_note.AUDIO_BYTES:
        raise HTTPException(422, "Đoạn ghi âm trống hoặc quá dài (tối đa khoảng 6 phút)")
    held = standing.for_request(request.headers, p, epoch)
    return _ndjson(lambda emit: field_note.turn(p, audio, media_type, emit=emit, thread_id=thread_id, standing=held), p)


@app.post("/v1/assistant/transcribe")
async def assistant_transcribe(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> dict[str, str]:
    """ĐỌC CHÍNH TẢ (mic trong ô nhập, kiểu ChatGPT): thân request là đoạn ghi âm ⇒ chỉ trả CHỮ chép được; app đưa vào ô nhập để người xem / sửa
    rồi TỰ bấm gửi — không chạy lượt trợ lý. Âm thanh chỉ ở trong bộ nhớ lúc chép lời, không lưu; tốn token ghi như mọi lượt (mục "voice")."""
    p = await current(x_dev_user, x_tenant_id, request)
    media_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if media_type not in field_note.AUDIO_TYPES:
        raise HTTPException(422, "Cần đoạn ghi âm (wav / m4a / mp3 / ogg / webm)")
    audio = await request.body()
    if not audio or len(audio) > field_note.AUDIO_BYTES:
        raise HTTPException(422, "Đoạn ghi âm trống hoặc quá dài (tối đa khoảng 6 phút)")
    try:
        text = await field_note.transcribe(p, audio, media_type)
    except AIDisabled as e:
        raise HTTPException(403, str(e)) from None
    return {"text": text}


IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"}
IMAGE_MAX, IMAGE_BYTES = 3, 5 * 1024 * 1024


def parse_images(raw: Any) -> list[tuple[bytes, str]]:
    """Ảnh gửi kèm câu hỏi: [{media_type, data: base64}] — tối đa 3 ảnh, mỗi ảnh ≤ 5 MB. Không lưu ở đâu cả (chat.chat_turn)."""
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > IMAGE_MAX:
        raise HTTPException(422, f"Tối đa {IMAGE_MAX} ảnh mỗi câu hỏi")
    out: list[tuple[bytes, str]] = []
    for item in raw:
        mt = str((item or {}).get("media_type") or "").lower()
        if mt not in IMAGE_TYPES:
            raise HTTPException(422, "Chỉ nhận ảnh PNG, JPEG, WebP, HEIC")
        try:
            data = base64.b64decode(str(item.get("data") or ""), validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(422, "Ảnh không đọc được") from e
        if not data or len(data) > IMAGE_BYTES:
            raise HTTPException(422, "Mỗi ảnh tối đa 5 MB")
        out.append((data, mt))
    return out


DOC_MAX, DOC_BYTES = 3, 10 * 1024 * 1024


def parse_documents(raw: Any) -> list[tuple[str, bytes]]:
    """Tài liệu gửi kèm câu hỏi: [{filename, data: base64}] — tối đa 3, mỗi tệp ≤ 10 MB, PDF / Word / Excel / PowerPoint / chữ. Thân tệp không lưu (chat.chat_turn)."""
    from barista.docread.types import kind_of
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > DOC_MAX:
        raise HTTPException(422, f"Tối đa {DOC_MAX} tài liệu mỗi câu hỏi")
    out: list[tuple[str, bytes]] = []
    for item in raw:
        name = str((item or {}).get("filename") or "").strip()[:300] or "tai-lieu"
        if kind_of(name) in ("", "image"):   # ảnh đi đường ``images``; loại docread không đọc được ⇒ từ chối
            raise HTTPException(422, "Chỉ nhận tài liệu PDF, Word, Excel, PowerPoint hoặc chữ")
        try:
            data = base64.b64decode(str(item.get("data") or ""), validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(422, "Tài liệu không đọc được") from e
        if not data or len(data) > DOC_BYTES:
            raise HTTPException(422, "Mỗi tài liệu tối đa 10 MB")
        out.append((name, data))
    return out


@app.post("/v1/assistant/proposals/decide")
async def decide_proposal(request: Request, body: Annotated[dict[str, Any], Body()], x_dev_user: DevUser = None,
                          x_tenant_id: TenantId = None) -> StreamingResponse:
    """Người Xác nhận / Bỏ một thẻ đề xuất (0049). NDJSON: decided | rejected, rồi (nếu đủ quyết định) lượt chạy tiếp như chat."""
    epoch = standing.epoch_of(x_tenant_id)
    p = await current(x_dev_user, x_tenant_id, request)
    card_id, decision, key = str(body.get("card_id") or ""), str(body.get("decision") or ""), str(body.get("key") or "")
    data = body.get("input") or {}
    if not card_id or not key or not isinstance(data, dict):
        raise HTTPException(422, "Cần card_id, key (chống lặp) và input")
    held = standing.for_request(request.headers, p, epoch)
    return _ndjson(lambda emit: chat.decide(p, card_id, decision, data, key, emit=emit, standing=held), p)


@app.get("/v1/assistant/threads")
async def threads(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> list[dict[str, Any]]:
    p = await current(x_dev_user, x_tenant_id, request)
    return executor.jsonable(await chat.list_threads(p))


@app.get("/v1/assistant/threads/{thread_id}")
async def thread(thread_id: UUID, request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> dict[str, Any]:
    p = await current(x_dev_user, x_tenant_id, request)
    t = await chat.load_thread(p, thread_id)
    if t is None:
        raise HTTPException(404, "Không thấy cuộc trò chuyện")
    # lịch sử nguyên văn cho model (lời gọi + kết quả công cụ) không gửi về app — app chỉ vẽ chữ, thẻ, bước
    t["turns"] = [{k: v for k, v in turn.items() if k != "messages"} for turn in chat.with_states(t["turns"])]
    return executor.jsonable(t)


@app.get("/v1/assistant/suggestions")
async def assistant_suggestions(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> list[str]:
    """Gợi ý câu hỏi ở ô chat — gợi ý HẰNG NGÀY, không phải câu hỏi mẫu để test: trước hết câu hỏi theo việc ĐANG CÓ hôm nay của chính người
    này ("Chia giúp tôi các lead đang chờ", "Hồ sơ nào đang kẹt…" — chỉ khi dữ liệu có việc đó), rồi câu XUYÊN MODULE của vai trò (trợ lý
    nối nhiều phân hệ trong một lượt — Role.showcase / Pack.hints của gói), rồi câu quen thuộc của vai trò; đều lọc theo quyền."""
    p = await current(x_dev_user, x_tenant_id, request)
    return compose_suggestions([i["ask"] for i in await desk.attention(p) if i.get("ask")], p, await chat.hint_rows(p))


SUGGESTION_SLOTS = 6
TODAY_SLOTS = 2   # câu "việc hôm nay" (chỉ việc người đó LÀM được — bàn việc bỏ mục ``doers_only`` của người chỉ xem) tối đa 2
ROLE_SLOTS = 2    # luôn chừa ít nhất 2 chỗ cho câu riêng của vai (rà vai giám đốc 18/11/2026 — ADR 0239 phần 4)


def compose_suggestions(today: list[str], p: Any, rows: list[dict[str, Any]] | None) -> list[str]:
    """Việc hôm nay (≤ 2) → câu xuyên module của vai → câu quen của vai; câu của vai luôn giữ ≥ 2 chỗ (cắt câu xuyên module trước)."""
    own = [q for q in chat.suggestions(p, rows=rows) if q not in today]
    head = list(dict.fromkeys(today))[:TODAY_SLOTS]
    show = [q for q in chat.showcase(p, rows=rows) if q not in head and q not in own]
    room = SUGGESTION_SLOTS - len(head) - min(ROLE_SLOTS, len(own))
    return list(dict.fromkeys(head + show[:max(0, room)] + own))[:SUGGESTION_SLOTS]


@app.websocket("/v1/assistant/voice")
async def assistant_voice(ws: WebSocket, thread_id: UUID | None = None) -> None:
    """Nói chuyện bằng giọng: Gemini Live chạy ở backend, app chỉ gửi / nhận âm thanh — xem ``agent/voice.py`` (0042)."""
    epoch = standing.epoch_of(ws.headers.get("x-tenant-id"))
    try:
        p = await current(ws.headers.get("x-dev-user"), ws.headers.get("x-tenant-id"), ws)
    except HTTPException as e:
        await ws.close(code=4401, reason=str(e.detail)[:120])
        return
    await ws.accept()
    # cả phiên một người gọi ⇒ kiểm lại tư cách mỗi lượt / định kỳ (F1) và cửa giọng nói (hạng, công tắc, hạn mức — 0193) trước khi mở Gemini Live
    await voice.serve(ws, p, thread_id, standing=standing.for_request(ws.headers, p, epoch))


@app.get("/v1/assistant/morning")
async def assistant_morning(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> list[dict[str, Any]]:
    """Đề xuất trợ lý soạn sẵn hôm nay cho người này (0074 A) — lệnh đã chạy thử, bấm Xác nhận mới ghi."""
    p = await current(x_dev_user, x_tenant_id, request)
    return executor.jsonable(await morning.cards(p))


@app.get("/v1/assistant/to-decide")
async def assistant_to_decide(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> list[dict[str, Any]]:
    """"Chờ tôi duyệt" (ADR 0253): TỪNG việc người này phải quyết — đề xuất trợ lý soạn + phiếu duyệt — nhóm theo loại, cùng hợp đồng bản chạy thử."""
    p = await current(x_dev_user, x_tenant_id, request)
    return executor.jsonable(await decisions.waiting(p))


@app.post("/v1/assistant/to-decide/decide")
async def assistant_decide_many(request: Request, body: Annotated[dict[str, Any], Body()], x_dev_user: DevUser = None,
                                x_tenant_id: TenantId = None) -> list[dict[str, Any]]:
    """Duyệt NHIỀU việc cùng một loại trong một lần — lần lượt từng thẻ qua đúng đường quyết của nó (kiểm căn cứ, nhật ký từng thẻ)."""
    p = await current(x_dev_user, x_tenant_id, request)
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise HTTPException(422, "Cần ids (danh sách mã việc)")
    try:
        return await decisions.decide_many(p, str(body.get("kind") or ""), ids, str(body.get("decision") or ""))
    except (Invalid, ValueError) as e:
        raise HTTPException(422, str(e)) from e


@app.post("/v1/assistant/morning/{proposal_id}/decide")
async def assistant_morning_decide(proposal_id: UUID, request: Request, body: Annotated[dict[str, Any], Body()], x_dev_user: DevUser = None,
                                   x_tenant_id: TenantId = None) -> dict[str, Any]:
    """Xác nhận (chạy bằng quyền người bấm, kiểm căn cứ) / Bỏ một đề xuất buổi sáng. ``key`` chống lặp như mọi lệnh."""
    p = await current(x_dev_user, x_tenant_id, request)
    decision, key, data = str(body.get("decision") or ""), str(body.get("key") or ""), body.get("input")
    if decision not in ("confirm", "dismiss", *previews.RECONCILED_MESSAGE) or not key:
        raise HTTPException(422, "Cần decision (confirm | dismiss | reconcile_done | reconcile_not_done) và key (chống lặp)")
    return await morning.decide(p, proposal_id, decision, data if isinstance(data, dict) else None, key)


@app.get("/v1/assistant/attention")
async def assistant_attention(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> list[dict[str, Any]]:
    """Việc cần chú ý hôm nay do hệ thống tính (không qua model), theo đúng quyền người hỏi."""
    p = await current(x_dev_user, x_tenant_id, request)
    return await desk.attention(p)


@app.get("/v1/desk")
async def work_desk(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> dict[str, Any]:
    """Bàn việc (0094): một điểm vào "tôi phải làm gì" — mục theo nhóm, mỗi mục là một màn đúng quyền + câu tín hiệu do code tính."""
    p = await current(x_dev_user, x_tenant_id, request)
    return executor.jsonable(await desk.desk(p))


# ── Thông báo (quyết định 0044) ──

@app.get("/v1/notifications/stream")
async def notifications_stream(request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> StreamingResponse:
    """SSE: ``event: changed`` mỗi khi hộp thông báo của người này có thêm (và một lần ngay khi nối). KHÔNG mang nội dung — app đọc
    lại ``core.notifications`` qua executor (RLS). Cứ ~25 giây một dòng giữ kết nối; ~5 phút kiểm lại phiên (bị thu hồi ⇒ đóng)."""
    p = await current(x_dev_user, x_tenant_id, request)

    async def lines() -> AsyncIterator[bytes]:
        with live.hub.subscribe(str(p.tenant_id), str(p.user_id)) as q:
            yield b"event: changed\ndata: {}\n\n"
            idle = 0
            while not await request.is_disconnected():
                try:
                    await asyncio.wait_for(q.get(), 25)
                    yield b"event: changed\ndata: {}\n\n"
                except TimeoutError:
                    idle += 1
                    if idle % 12 == 0:
                        try:
                            await current(x_dev_user, x_tenant_id, request)
                        except HTTPException:
                            return
                    yield b": ping\n\n"

    return StreamingResponse(lines(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/v1/public/links/{token}/signal")
async def public_link_signal(token: str, body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    p = await web.link_caller(token, "signal")
    return _result(await executor.run(p, web.link_op(p, "signal"), body, quiet=True))


@app.post("/v1/public/links/{token}/presence")
async def public_link_presence(token: str, body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    p = await web.link_caller(token, "presence")
    return _result(await executor.run(p, web.link_op(p, "presence"), body))


@app.get("/v1/files/signed")
async def signed_file(request: Request) -> StreamingResponse:
    """Link ký ≤ 60 giây của nơi chứa thư mục (0122 §4; R2 thì link đi thẳng tới kho, không qua đây). Quyền đã kiểm lúc cấp link
    (``files.signed_url``) — route chỉ kiểm chữ ký + hạn, không chạm DB; hai tiêu đề an toàn lấy từ phần đã ký, không từ người gọi."""
    got = files.verify_signed(dict(request.query_params))
    if got is None:
        raise HTTPException(404, "Link hết hạn hoặc không hợp lệ")
    key, content_type, disposition = got
    if await files.store().size(key) is None:
        raise HTTPException(404, "Tệp không còn")
    return StreamingResponse(files.stream_object(key), media_type=content_type,
                             headers={"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


# ── MCP chỉ đọc (0093): bước ĐỒNG Ý cấp quyền cho ứng dụng AI — trên web Barista, bằng phiên đăng nhập + công ty đang chọn ──

@app.get("/v1/mcp/requests/{request_id}")
async def mcp_request(request_id: str, request: Request, x_dev_user: DevUser = None, x_tenant_id: TenantId = None) -> dict[str, Any]:
    p = await current(x_dev_user, x_tenant_id, request)
    d = await oauth.describe(request_id)
    if d is None:
        raise HTTPException(404, "Yêu cầu kết nối đã dùng hoặc hết hạn — bấm kết nối lại từ ứng dụng AI")
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT name FROM core.tenants WHERE id = %s", (p.tenant_id,))
        t = await cur.fetchone()
    return {**d, "company": t["name"] if t else "", "user": p.name}


@app.get("/v1/oauth/signin/{request_id}")
async def oauth_signin_app(request_id: str) -> JSONResponse:
    """0205: tên ứng dụng first-party của một yêu cầu đăng nhập — trang đăng nhập Barista (chưa có phiên) hiện "Đăng nhập <ứng dụng>" bằng tên thật."""
    name = await oauth.signin_app(request_id)
    if name is None:
        return JSONResponse({"error": "not_found"}, status_code=404, headers={"Cache-Control": "no-store"})
    return JSONResponse({"app": name}, headers={"Cache-Control": "no-store"})


@app.post("/v1/oauth/google")
async def oauth_google_signin(request: Request, body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    """0205 §5b: SERVER ứng dụng first-party (client_id + client_secret) đổi id_token Google (nút Google của Barista đặt ngay trên trang ứng dụng) lấy
    token ``identity`` — trả đúng dạng
    /token (``access_token``, ``refresh_token``, ``expires_in``, ``scope``); làm mới vẫn qua /token như thường."""
    client_id = str(body.get("client_id") or "")
    ip = request.client.host if request.client else ""
    ok_key = hashlib.sha256(f"signin|{client_id}".encode()).hexdigest()[:32]
    fail_key = hashlib.sha256(f"signin|{client_id}|{ip}".encode()).hexdigest()[:32]
    no_store = {"Cache-Control": "no-store"}
    if ratelimit.blocked(fail_key, "signin_fail") or ratelimit.blocked(ok_key, "signin"):
        return JSONResponse({"error": "slow_down"}, status_code=429, headers={**no_store, "Retry-After": "60"})
    try:
        tok = await oauth.google_signin(client_id, str(body.get("client_secret") or ""), str(body.get("id_token") or ""), str(body.get("nonce") or ""),
                                        str(body.get("tenant_id") or ""))
    except oauth.SignInRefused as e:
        ratelimit.hit(fail_key, "signin_fail")
        log.info("đăng nhập ứng dụng first-party bị từ chối (%s): %s", e.code, e)
        status = {"unauthorized_client": 401, "invalid_request": 400, "invalid_grant": 400}.get(e.code, 403)
        return JSONResponse({"error": e.code, "error_description": str(e)}, status_code=status, headers=no_store)
    ratelimit.hit(ok_key, "signin")
    return JSONResponse(tok.model_dump(mode="json", exclude_none=True), headers=no_store)


@app.post("/v1/oauth/email/start")
async def oauth_email_start(body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    """0205 §5c: SERVER ứng dụng first-party (client_id + client_secret) xin mã đăng nhập qua email thay người dùng; ``nonce`` gắn mã với trình duyệt
    đã xin. Trả lời như nhau dù email có được mời hay không. Giới hạn theo email (mọi lượt đi từ server ứng dụng — không theo địa chỉ gọi)."""
    no_store = {"Cache-Control": "no-store"}
    try:
        app_name = await oauth.app_client(str(body.get("client_id") or ""), str(body.get("client_secret") or ""))
    except oauth.SignInRefused as e:
        return JSONResponse({"error": e.code, "error_description": str(e)}, status_code=401, headers=no_store)
    email = email_login.normalize(str(body.get("email") or ""))
    if ratelimit.blocked(_key("email", email), "email_code"):
        return JSONResponse({"error": "slow_down"}, status_code=429, headers={**no_store, "Retry-After": "60"})
    ratelimit.hit(_key("email", email), "email_code")
    try:
        await email_login.send_code(email, app=app_name, client_id=str(body.get("client_id")), nonce=str(body.get("nonce") or "") or None)
    except email_login.EmailLoginUnavailable as e:
        return JSONResponse({"error": "temporarily_unavailable", "error_description": str(e)}, status_code=503, headers=no_store)
    return JSONResponse({"status": "sent"}, headers=no_store)


@app.post("/v1/oauth/email/verify")
async def oauth_email_verify(body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
    """0205 §5c: SERVER ứng dụng first-party gửi mã người dùng nhập ⇒ token ``identity`` (amr ["email"]) — dạng như /token, làm mới qua /token."""
    no_store = {"Cache-Control": "no-store"}
    try:
        tok = await oauth.email_signin(str(body.get("client_id") or ""), str(body.get("client_secret") or ""), str(body.get("email") or ""),
                                       str(body.get("code") or ""), str(body.get("nonce") or ""), str(body.get("tenant_id") or ""))
    except oauth.SignInRefused as e:
        status = {"unauthorized_client": 401, "invalid_request": 400, "invalid_grant": 400}.get(e.code, 403)
        return JSONResponse({"error": e.code, "error_description": str(e)}, status_code=status, headers=no_store)
    return JSONResponse(tok.model_dump(mode="json", exclude_none=True), headers=no_store)


@app.get("/v1/oauth/userinfo")
async def oauth_userinfo(request: Request) -> JSONResponse:
    """0205: DANH TÍNH của người đã đăng nhập ứng dụng first-party bằng tài khoản Barista (token phạm vi ``identity``). Nạp lại mỗi lần: người bị khoá
    / rời công ty ⇒ 401 (ứng dụng đó phải đăng xuất họ). Token đọc dữ liệu của ứng dụng AI không đổi được thành danh tính."""
    auth_h = request.headers.get("authorization", "")
    token = auth_h[7:].strip() if auth_h.lower().startswith("bearer ") else ""
    ip = request.client.host if request.client else ""
    fail_key = hashlib.sha256(f"userinfo|{ip}".encode()).hexdigest()[:32]   # token SAI đếm theo địa chỉ gọi (chống dò token)
    ok_key = hashlib.sha256(token.encode()).hexdigest()[:32]
    no_store = {"Cache-Control": "no-store"}
    if ratelimit.blocked(fail_key, "userinfo_fail") or (token and ratelimit.blocked(ok_key, "userinfo")):
        return JSONResponse({"error": "slow_down"}, status_code=429, headers={**no_store, "Retry-After": "60"})
    who = await oauth.identity(token) if token else None
    if who is None:
        ratelimit.hit(fail_key, "userinfo_fail")
        return JSONResponse({"error": "invalid_token"}, status_code=401, headers={**no_store, "WWW-Authenticate": 'Bearer error="invalid_token"'})
    ratelimit.hit(ok_key, "userinfo")
    return JSONResponse(who, headers=no_store)


@app.post("/v1/mcp/approve")
async def mcp_approve(request: Request, body: Annotated[dict[str, Any], Body()], x_dev_user: DevUser = None,
                      x_tenant_id: TenantId = None) -> dict[str, str]:
    """Đồng ý / từ chối ⇒ địa chỉ quay về ứng dụng AI (mã cấp quyền gắn NGƯỜI + CÔNG TY đang chọn, chỉ đọc)."""
    p = await current(x_dev_user, x_tenant_id, request)
    bearer = request.headers.get("authorization", "")
    login = None   # cách đăng nhập của phiên đang đồng ý: phiên THẬT (Bearer ⇒ core.sessions.provider); đăng nhập dev / chọn vai ⇒ None
    if bearer[:7].lower() == "bearer ":
        async with db.owner.connection() as conn:
            login = await sessions.provider_of(conn, bearer[7:].strip())
    try:
        url = await oauth.approve(str(body.get("request_id") or ""), p, bool(body.get("approve", True)), login=login)
    except oauth.NeedsRealLogin as e:
        raise HTTPException(403, str(e)) from e
    except LookupError as e:
        raise HTTPException(410, str(e)) from e
    return {"redirect": url}


# ── Route do MODULE tự khai (``Module.route``): kênh tức thì, tệp lên / xuống của gói. Lõi gắn theo sổ đăng ký, không biết module nào; gói tắt /
# chưa cài ⇒ không có route. Không được trùng route của lõi (trùng ⇒ không lên — lõi không chạy nửa vời). ──
_core = {(m, getattr(r, "path", "")) for r in app.routes for m in getattr(r, "methods", None) or ()}
for _r in registry.ROUTES:
    if (_r.method, _r.path) in _core:
        raise RuntimeError(f"module {_r.module}: {_r.method} {_r.path} trùng route của lõi")
    app.add_api_route(_r.path, _r.handler, methods=[_r.method], name=f"{_r.module}.{_r.handler.__name__}",
                      dependencies=None if _r.public else [Depends(web.require_caller)])   # mặc định cần đăng nhập; công khai phải khai lý do


# CUỐI CÙNG: MCP + đường OAuth / metadata ở gốc — mọi đường /v1 ở trên khớp trước (Starlette xét theo thứ tự)
app.mount("/", mcp_server.app())

