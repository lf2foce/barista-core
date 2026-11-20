"""WEBHOOK ra app từ xa (0140): sự kiện outbox CÔNG BỐ (``Module.webhook``) ⇒ hộp thư đi ``core.webhook_deliveries`` ⇒ worker gửi, ký HMAC.

* Ghi CÙNG giao dịch với sự kiện (``enqueue`` từ ``executor.flush_events``): lệnh hoàn ⇒ không có webhook; commit ⇒ chắc chắn được gửi (ít nhất
  một lần — bên nhận chống lặp theo ``webhook-id``).
* Thân CHỈ mang loại + mã (khoá ``*_id`` module khai) + lúc xảy ra + mã công ty. App gọi ngược API để đọc bằng quyền của nó.
* Ký theo Standard Webhooks (standardwebhooks.com): ``webhook-id``, ``webhook-timestamp`` (giây Unix), ``webhook-signature: v1,<base64 HMAC-SHA256
  của "{id}.{timestamp}.{thân}">``. Bên nhận: so chữ ký thời gian hằng, bỏ thư lệch giờ > 5 phút, nhớ ``webhook-id`` đã nhận ⇒ chống phát lại.
* Lúc gửi kiểm lại: app còn hoạt động, còn XEM được tài nguyên của sự kiện phạm vi cả công ty (quyền giao người bảo lãnh — có thể đã hẹp đi).
  Không ⇒ thư bị huỷ / dừng, không gửi. Lỗi / mã ≠ 2xx ⇒ thử lại lùi dần (30 giây × 2^n, tối đa 1 giờ); quá ``MAX_ATTEMPTS`` ⇒ ``dead``.
* Chống SSRF: bản thật chỉ gửi https tới địa chỉ công khai — phân giải tên, MỌI địa chỉ phải công khai (kể cả dạng IPv4 nhúng trong IPv6: NAT64,
  ánh xạ ``::ffff:``, 6to4, Teredo…), rồi NỐI THẲNG tới địa chỉ đã kiểm (ghim IP — chống DNS rebinding), TLS vẫn kiểm chứng chỉ theo tên máy (SNI);
  không theo chuyển hướng, không dùng proxy của môi trường.
* Gửi song song: tối đa ``CONCURRENCY`` thư một lúc, ``PER_ENDPOINT`` thư một lúc mỗi máy nhận, mỗi thư hết giờ ``TIMEOUT_S`` ⇒ một máy nhận chậm không
  chặn các app khác. Thuê lô ``LEASE_S`` (lớn hơn trường hợp xấu nhất của một lô); ghi kết quả chỉ khi còn đúng lượt thuê của mình (``next_at`` lúc thuê)
  ⇒ hai bản chạy song song không ghi đè nhau.
"""

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
from collections.abc import Awaitable, Callable
from datetime import UTC
from typing import Any
from urllib.parse import urlparse

import httpx

from barista.config import settings
from barista.db import Conn, db, set_session
from barista.kernel import registry, remote_apps
from barista.kernel.principal import Principal, system_principal

log = logging.getLogger("barista.webhooks")
MAX_ATTEMPTS = 12
LEASE_S = 300          # ≥ trường hợp xấu nhất: BATCH / PER_ENDPOINT × (TIMEOUT_S + ghi DB) ≈ 13 × 7 giây; quá thì lô bị cắt ở BATCH_BUDGET_S
BATCH = 50
BATCH_BUDGET_S = 200   # cả lô dừng trước khi hết thuê — thư dở dang thuê lại sau (ít nhất một lần; bên nhận chống lặp theo webhook-id)
TIMEOUT_S = 5.0
CONCURRENCY = 8
PER_ENDPOINT = 4
# IPv6 mang / dịch địa chỉ IPv4 hoặc dành riêng — coi là KHÔNG công khai (NAT64, IPv4-compatible, 6to4, Teredo, ORCHID, tài liệu, discard)
_SPECIAL6 = tuple(ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "::/96", "::ffff:0:0/96", "2002::/16", "2001::/32",
                                                    "2001:10::/28", "2001:20::/28", "2001:db8::/32", "100::/64", "fc00::/7", "fe80::/10", "ff00::/8"))

# (url, thân, header) → mã HTTP. Test thay bằng app giả trong tiến trình.
type Sender = Callable[[str, bytes, dict[str, str]], Awaitable[int]]


def by_event(event_type: str) -> list[registry.Webhook]:
    return [w for w in registry.WEBHOOKS.values() if w.event_type == event_type]


async def enqueue(conn: Conn, p: Principal, event_id: int, event_type: str, payload: dict[str, Any]) -> int:
    """Trong giao dịch của sự kiện: mỗi app ĐANG HOẠT ĐỘNG đăng ký tên công bố của sự kiện này một thư. Chỉ chép khoá mã module khai. Sự kiện xảy
    ra trong phiên link công khai (0064 — phiên đó không đọc cấu hình công ty) ⇒ tra danh sách app bằng quyền hệ thống của công ty, rồi trả phiên."""
    hooks = by_event(event_type)
    if not hooks:
        return 0
    sysp = system_principal(p.tenant_id) if p.link_ref is not None else None
    if sysp is not None:
        await set_session(conn, sysp)
    try:
        return await _enqueue(conn, hooks, event_id, payload)
    finally:
        if sysp is not None:
            await set_session(conn, p)


async def _enqueue(conn: Conn, hooks: list[registry.Webhook], event_id: int, payload: dict[str, Any]) -> int:
    cur = await conn.execute(
        """SELECT s.app_id, s.event FROM core.remote_app_events s JOIN core.remote_apps a ON a.id = s.app_id
           WHERE s.event = ANY(%s) AND a.status = 'active' AND a.webhook_url <> ''""", ([h.name for h in hooks],))
    subs = await cur.fetchall()
    if not subs:
        return 0
    data = {h.name: {k: payload.get(k) for k in h.ids if payload.get(k) is not None} for h in hooks}
    async with conn.cursor() as c:
        await c.executemany(
            "INSERT INTO core.webhook_deliveries (app_id, event, event_id, data, occurred_at) VALUES (%s, %s, %s, %s, clock_timestamp())",
            [(s["app_id"], s["event"], event_id, json.dumps(data[s["event"]])) for s in subs])
    return len(subs)


def sign(key: str, msg_id: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(key.encode(), f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def verify(key: str, headers: dict[str, str], body: bytes, *, tolerance_s: int = 300, now: float | None = None) -> bool:
    """Phía NHẬN (mẫu cho app, cũng dùng trong test): chữ ký đúng + thời điểm trong ±``tolerance_s``. Chống lặp theo ``webhook-id`` là việc bên nhận."""
    h = {k.lower(): v for k, v in headers.items()}
    try:
        ts = int(h["webhook-timestamp"])
    except (KeyError, ValueError):
        return False
    if abs((time.time() if now is None else now) - ts) > tolerance_s:
        return False
    want = sign(key, h.get("webhook-id", ""), ts, body)
    return any(hmac.compare_digest(want, s.strip()) for s in h.get("webhook-signature", "").split(" ") if s.strip())


def is_public(addr: str) -> bool:
    """Địa chỉ IP công khai thật (không nội bộ / loopback / link-local / CGNAT / dành riêng / đa hướng, không IPv4 nội bộ nhúng trong IPv6)."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public(str(ip.ipv4_mapped))
        if any(ip in n for n in _SPECIAL6):
            return False
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_unspecified)


def url_allowed(url: str) -> str | None:
    """Lý do KHÔNG nhận địa chỉ webhook (None = được). Bản thật: https + tên máy không phải địa chỉ nội bộ viết thẳng."""
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        return "Địa chỉ webhook phải là http(s)://…"
    if u.username or u.password:
        return "Địa chỉ webhook không được chứa tên / mật khẩu"
    if settings.webhook_allow_private:
        return None
    if u.scheme != "https":
        return "Địa chỉ webhook phải là https://"
    host = u.hostname
    try:
        ipaddress.ip_address(host.split("%")[0])
        literal = True
    except ValueError:
        literal = False
    if (literal and not is_public(host)) or host == "localhost" or host.endswith((".local", ".internal", ".localhost", ".home.arpa")):
        return "Địa chỉ webhook không được trỏ vào mạng nội bộ"
    return None


async def resolve_public(host: str, port: int) -> str:
    """Phân giải ``host``: MỌI địa chỉ phải công khai (một địa chỉ nội bộ ⇒ không gửi). Trả địa chỉ sẽ NỐI THẲNG tới (ghim IP)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addrs = sorted({str(i[4][0]) for i in infos})
    if not addrs or any(not is_public(a) for a in addrs):
        raise PermissionError(f"{host} phân giải ra địa chỉ không công khai — không gửi")
    return addrs[0]


def pinned_url(url: str, ip: str) -> str:
    u = urlparse(url)
    netloc = (f"[{ip}]" if ":" in ip else ip) + (f":{u.port}" if u.port else "")
    return u._replace(netloc=netloc).geturl()


async def http_send(url: str, body: bytes, headers: dict[str, str]) -> int:
    """Người gửi thật: không theo chuyển hướng, không proxy môi trường; bản thật nối thẳng tới IP đã kiểm (Host + SNI là tên máy gốc ⇒ TLS vẫn kiểm
    chứng chỉ theo tên)."""
    if (why := url_allowed(url)) is not None:
        raise PermissionError(why)
    u = urlparse(url)
    target, extensions = url, {}
    if not settings.webhook_allow_private:
        ip = await resolve_public(u.hostname or "", u.port or 443)
        target, extensions = pinned_url(url, ip), {"sni_hostname": u.hostname or ""}
        headers = {**headers, "host": u.netloc}
    async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S, connect=3.0), follow_redirects=False, trust_env=False) as client:
        r = await client.post(target, content=body, headers=headers, extensions=extensions)
    return r.status_code


_sender: Sender = http_send


def use_sender(s: Sender | None) -> None:
    """Đổi người gửi (test: app giả trong tiến trình). None = gửi HTTP thật."""
    global _sender
    _sender = s or http_send


def _body(d: dict[str, Any]) -> bytes:
    return json.dumps({"id": str(d["id"]), "type": d["event"], "occurred_at": d["occurred_at"].astimezone(UTC).isoformat(),
                       "company_id": str(d["tenant_id"]), "data": d["data"]}, ensure_ascii=False, separators=(",", ":")).encode()


async def _finish(d: dict[str, Any], status: str, *, code: int | None = None, error: str = "") -> None:
    """Ghi kết quả CHỈ khi thư còn đúng lượt thuê của mình (``next_at`` = lúc thuê) — hết thuê mà bản khác đã thuê lại thì không ghi đè."""
    async with db.owner_tx() as c:
        if status == "retry":
            await c.execute(
                """UPDATE core.webhook_deliveries SET attempts = attempts + 1, last_status = %s, last_error = %s,
                       status = CASE WHEN attempts + 1 >= %s THEN 'dead' ELSE 'pending' END,
                       next_at = now() + make_interval(secs => least(3600, 30 * 2 ^ attempts))
                   WHERE id = %s AND status = 'pending' AND next_at = %s""", (code, error[:1000], MAX_ATTEMPTS, d["id"], d["lease"]))
        else:
            await c.execute("""UPDATE core.webhook_deliveries SET status = %s, attempts = attempts + %s, last_status = %s, last_error = %s,
                               done_at = now() WHERE id = %s AND status = 'pending' AND next_at = %s""",
                            (status, 1 if code is not None else 0, code, error[:1000], d["id"], d["lease"]))


async def _deliver(d: dict[str, Any]) -> bool:
    if d["app_status"] != "active":
        await _finish(d, "cancelled", error="app đã bị thu hồi")
        return False
    hook = registry.WEBHOOKS.get(d["event"])
    try:
        async with db.owner.connection() as c:
            p = await remote_apps.load(c, {"id": d["app_id"], "tenant_id": d["tenant_id"], "name": d["name"], "client_id": d["client_id"],
                                           "service_user_id": d["service_user_id"], "installed_by": d["installed_by"],
                                           "sponsor_role": d["sponsor_role"]})
    except remote_apps.AppRefused as e:
        await _finish(d, "retry", error=str(e))
        return False
    if hook is None or not (p.can(hook.resource, "read") and p.has_scope(hook.resource, "all")):
        await _finish(d, "dead", error=f"app không còn xem được “{hook.resource if hook else d['event']}” phạm vi cả công ty — không gửi")
        return False
    body = _body(d)
    ts = int(time.time())
    headers = {"content-type": "application/json", "user-agent": "Barista-Webhooks/1", "webhook-id": str(d["id"]),
               "webhook-timestamp": str(ts)}
    try:
        headers["webhook-signature"] = sign(remote_apps.signing_key(d["app_id"], d["secret_version"]), str(d["id"]), ts, body)
        code = await _sender(d["webhook_url"], body, headers)
    except Exception as e:   # mạng, DNS, địa chỉ nội bộ, thiếu khoá bản cài ⇒ thử lại sau (lỗi ghi lại cho quản trị xem)
        log.warning("webhook %s lỗi: %r", d["id"], e)
        await _finish(d, "retry", error=f"{type(e).__name__}: {e}")
        return False
    if 200 <= code < 300:
        await _finish(d, "done", code=code)
        return True
    await _finish(d, "retry", code=code, error=f"HTTP {code}")
    return False


async def dispatch_once(limit: int = BATCH) -> int:
    """Thuê một lô thư đến hạn (đẩy next_at ra sau ``LEASE_S``) trong giao dịch ngắn — nhiều worker không lấy trùng; chết giữa chừng ⇒ hết thuê,
    thư quay lại hàng đợi. Gửi song song có trần chung + trần mỗi máy nhận."""
    async with db.owner_tx() as c:
        cur = await c.execute(
            """UPDATE core.webhook_deliveries w SET next_at = now() + make_interval(secs => %s)
               FROM core.remote_apps a
               WHERE a.id = w.app_id AND w.id IN (SELECT id FROM core.webhook_deliveries WHERE status = 'pending' AND next_at <= now()
                                                  ORDER BY next_at LIMIT %s FOR UPDATE SKIP LOCKED)
               RETURNING w.id, w.next_at AS lease, w.tenant_id, w.app_id, w.event, w.data, w.occurred_at, a.status AS app_status, a.webhook_url,
                         a.secret_version, a.name, a.client_id, a.service_user_id, a.installed_by, a.sponsor_role""", (LEASE_S, limit))
        due = await cur.fetchall()
    overall = asyncio.Semaphore(CONCURRENCY)
    per_host: dict[str, asyncio.Semaphore] = {}

    async def one(d: dict[str, Any]) -> bool:
        host = urlparse(d["webhook_url"]).netloc
        async with overall, per_host.setdefault(host, asyncio.Semaphore(PER_ENDPOINT)):
            try:
                async with asyncio.timeout(TIMEOUT_S + 5):
                    return await _deliver(d)
            except TimeoutError:
                await _finish(d, "retry", error="quá giờ")
                return False

    tasks = [asyncio.create_task(one(d)) for d in sorted(due, key=lambda r: r["occurred_at"])]
    if not tasks:
        return 0
    done, pending = await asyncio.wait(tasks, timeout=BATCH_BUDGET_S)
    for t in pending:   # lô quá ngân sách thời gian: thư dở dang để hết thuê rồi gửi lại (không ghi gì — lượt thuê vẫn của mình tới lúc đó)
        t.cancel()
    return sum(1 for t in done if not t.cancelled() and t.exception() is None and t.result())


async def drain(max_rounds: int = 20) -> int:
    total = 0
    for _ in range(max_rounds):
        n = await dispatch_once()
        total += n
        if n == 0:
            break
    return total


async def run_forever(interval_s: float) -> None:
    while True:
        try:
            await drain()
        except asyncio.CancelledError:
            raise
        except Exception:   # vòng nền không được chết vì một lượt lỗi — log.exception rồi chạy lượt sau
            log.exception("vòng gửi webhook lỗi")
        await asyncio.sleep(interval_s)
