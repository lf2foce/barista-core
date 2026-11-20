"""APP TỪ XA (0140, 0130 tầng 3): danh tính, khoá, token và người gọi của một app chạy trên máy chủ khác.

* App = client OAuth của MỘT công ty (``core.remote_apps``), quản trị cài, chủ tài khoản duyệt (luật ``access_change`` — 0029). Cấp quyền
  **client credentials** (RFC 6749 §4.4): máy gọi máy, không có người đứng trước trình duyệt; sự đồng ý là lần cài đã được duyệt, không phải
  từng lần xin token. Khoá app (``client_secret``) và token lưu BĂM; khoá ký webhook không lưu — dẫn xuất từ khoá của bản cài.
* Người gọi của app (``via="app"``): user_id = dòng core.users riêng của app (không có thành viên ⇒ không đăng nhập, không hiện trong danh sách
  người, không nhận lead / phiếu); quyền = vai của app (``core.remote_app_grants``) GIAO quyền hiện tại của người bảo lãnh (nạp lại mỗi lần,
  như trợ lý ≤ người) GIAO phạm vi của token. Chỉ xem / ghi — không duyệt, không quản lý, không tài nguyên quản trị lõi. Người bảo lãnh nghỉ /
  bị khoá ⇒ app không gọi được (quản trị khác đổi quyền app ⇒ thành người bảo lãnh mới, qua duyệt).
* Thu hồi có hiệu lực ngay: mỗi request tra token + trạng thái app trong DB (không token tự chứa).
"""

import hashlib
import hmac
import secrets
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.config import settings
from barista.db import Conn, db
from barista.kernel import auth, packs, vocab
from barista.kernel.principal import SCOPES, Grant, Principal, issued

CLIENT_PREFIX, SECRET_PREFIX, TOKEN_PREFIX = "bca_", "bcs_", "bat_"
APP_ACTIONS = frozenset({"read", "write"})   # app không duyệt, không quản lý
FORBIDDEN_RESOURCES = frozenset({"core.admin"})
SERVICE_DOMAIN = "apps.barista.invalid"      # .invalid (RFC 2606): không ai đăng nhập Google bằng tên miền này được


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_client_id() -> str:
    return CLIENT_PREFIX + secrets.token_urlsafe(16)


def service_email(app_id: UUID) -> str:
    return f"app-{app_id}@{SERVICE_DOMAIN}"


def is_app_token(bearer: str) -> bool:
    return bearer.startswith(TOKEN_PREFIX)


# ── Quyền ──

def parse_grants(text: str) -> dict[str, Grant]:
    """``"leadflow:team:read,write; crm:all:read"`` → {tài nguyên: Grant}. Chỉ kiểm cú pháp + hành động app được có; tài nguyên có thật hay không
    do bên gọi kiểm với sổ đăng ký."""
    out: dict[str, Grant] = {}
    for raw in text.replace("\n", ";").split(";"):
        part = raw.strip()
        if not part:
            continue
        bits = [b.strip() for b in part.split(":")]
        if len(bits) != 3 or not bits[0] or bits[1] not in SCOPES:
            raise ValueError(f"“{part}” không hiểu — dạng <tài nguyên>:<own|unit|team|all>:<read,write>")
        acts = frozenset(a.strip() for a in bits[2].split(",") if a.strip())
        if not acts or acts - APP_ACTIONS:
            raise ValueError(f"“{part}”: app chỉ được xem (read) / ghi (write) — không duyệt, không quản lý")
        if bits[0] in FORBIDDEN_RESOURCES:
            raise ValueError(f"“{part}”: app không bao giờ có quyền quản trị lõi")
        if bits[0] in out:
            raise ValueError(f"“{bits[0]}” khai hai lần")
        out[bits[0]] = Grant(bits[1], acts)
    return out


def format_grants(grants: Mapping[str, Grant]) -> str:
    return "; ".join(f"{r}:{g.scope}:{','.join(a for a in ('read', 'write') if a in g.actions)}" for r, g in sorted(grants.items()))


def _narrower(a: str, b: str) -> str:
    return a if SCOPES.index(a) <= SCOPES.index(b) else b


def exceeding(wanted: Mapping[str, Grant], holder: Mapping[str, Grant]) -> list[str]:
    """Phần ``wanted`` vượt quyền ``holder`` (tài nguyên không có, hành động thiếu, phạm vi rộng hơn) — rỗng = nằm gọn trong."""
    bad = []
    for res, g in sorted(wanted.items()):
        h = holder.get(res)
        if h is None:
            bad.append(f"{res} (không có quyền)")
        elif g.actions - h.actions:
            bad.append(f"{res}: {'/'.join(sorted(g.actions - h.actions))}")
        elif _narrower(g.scope, h.scope) != g.scope:
            bad.append(f"{res}: phạm vi {g.scope} rộng hơn {h.scope}")
    return bad


def effective(app: Mapping[str, Grant], sponsor: Mapping[str, Grant], token_scopes: Iterable[str] = ()) -> dict[str, Grant]:
    """Quyền LÚC CHẠY = vai app ∩ quyền hiện tại của người bảo lãnh ∩ phạm vi token (``tài nguyên:hành động``; rỗng = không thu hẹp). Phạm vi lấy
    hẹp hơn theo thứ tự own < unit < team < all; hành động ∩ {read, write}."""
    want = set(token_scopes)
    out: dict[str, Grant] = {}
    for res, g in app.items():
        h = sponsor.get(res)
        if h is None or res in FORBIDDEN_RESOURCES:
            continue
        acts = g.actions & h.actions & APP_ACTIONS
        if want:
            acts = frozenset(a for a in acts if f"{res}:{a}" in want)
        if acts:
            out[res] = Grant(_narrower(g.scope, h.scope), acts)
    return out


async def app_grants(conn: Conn, app_id: UUID) -> dict[str, Grant]:
    cur = await conn.execute("SELECT resource, scope, actions FROM core.remote_app_grants WHERE app_id = %s", (app_id,))
    return {r["resource"]: Grant(r["scope"], frozenset(r["actions"])) for r in await cur.fetchall()}


# ── Khoá ──

def signing_key(app_id: UUID, version: int) -> str:
    """Khoá ký webhook của app ở phiên bản khoá ``version`` — DẪN XUẤT (HMAC khoá bản cài), không lưu ở đâu. Xoay khoá app ⇒ khoá ký đổi theo."""
    if not settings.apps_signing_secret:
        raise RuntimeError("BARISTA_APPS_SIGNING_SECRET chưa đặt — bản cài chưa cấp được khoá app / ký webhook (runbook 11)")
    return "whsec_" + hmac.new(settings.apps_signing_secret.encode(), f"webhook:{app_id}:{version}".encode(), hashlib.sha256).hexdigest()


async def set_secret(conn: Conn, app_id: UUID) -> tuple[str, int]:
    """Cấp khoá mới cho app (trong giao dịch của lệnh nhận khoá): khoá cũ + mọi token đang có hết hiệu lực NGAY. Trả (khoá rõ — chỉ một lần, phiên bản)."""
    secret = SECRET_PREFIX + secrets.token_urlsafe(32)
    cur = await conn.execute("""UPDATE core.remote_apps SET secret_hash = %s, secret_version = secret_version + 1, secret_claim_by = NULL,
                                secret_set_at = now() WHERE id = %s AND status = 'active' RETURNING secret_version""", (_hash(secret), app_id))
    row = await cur.fetchone()
    if row is None:
        raise LookupError("app không còn hoạt động")
    await conn.execute("UPDATE core.remote_app_tokens SET revoked_at = now() WHERE app_id = %s AND revoked_at IS NULL", (app_id,))
    return secret, row["secret_version"]


# ── Token (client credentials) ──

class TokenRefused(Exception):
    """``error`` theo RFC 6749 §5.2 (invalid_client, invalid_scope…) — lời không nói khoá sai hay app không có, để không dò được client."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error


async def _audit_token(app: Mapping[str, Any], status: str, error: str, scope: str, ip: str) -> None:
    """Nhật ký thao tác của công ty có app (review 08/11): mỗi lần xin token — cấp / từ chối, phạm vi xin, địa chỉ gọi. Không ghi khoá."""
    async with db.owner_tx() as c:
        await c.execute("""INSERT INTO core.audit_log (tenant_id, user_id, system, via, command, input, status, error)
                           VALUES (%s, %s, false, 'app', 'apps.token', %s, %s, %s)""",
                        (app["tenant_id"], app["service_user_id"], Jsonb({"client_id": app["client_id"], "scope": scope, "ip": ip}), status,
                         error[:500]))


async def issue_token(client_id: str, client_secret: str, scope: str = "", *, ip: str = "") -> dict[str, Any]:
    """Đổi khoá app lấy token truy cập ngắn hạn. ``scope``: "tài nguyên:hành động" cách nhau dấu cách — phải nằm trong quyền app (không thì
    ``invalid_scope``); trống = mọi quyền của app. Lần xin của một app có thật (cấp hay bị từ chối) vào nhật ký của công ty đó."""
    async with db.owner.connection() as c:
        cur = await c.execute("""SELECT id, tenant_id, client_id, service_user_id, secret_hash, secret_version, status FROM core.remote_apps
                                 WHERE client_id = %s""", (client_id,))
        app = await cur.fetchone()
    if app is None:
        raise TokenRefused("invalid_client", "Sai client_id / client_secret, hoặc app đã bị thu hồi")
    if app["status"] != "active" or not app["secret_hash"] or not hmac.compare_digest(app["secret_hash"], _hash(client_secret)):
        await _audit_token(app, "denied", "sai khoá" if app["status"] == "active" else "app đã bị thu hồi", scope, ip)
        raise TokenRefused("invalid_client", "Sai client_id / client_secret, hoặc app đã bị thu hồi")
    async with db.owner.connection() as c:
        grants = await app_grants(c, app["id"])
    wanted = [s for s in scope.split() if s]
    have = {f"{r}:{a}" for r, g in grants.items() for a in g.actions}
    if outside := set(wanted) - have:
        await _audit_token(app, "denied", f"phạm vi ngoài quyền: {' '.join(sorted(outside))}", scope, ip)
        raise TokenRefused("invalid_scope", f"Ngoài quyền của app: {' '.join(sorted(outside))}")
    async with db.owner_tx() as c:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(seconds=settings.apps_token_ttl_s)
        await c.execute("""INSERT INTO core.remote_app_tokens (tenant_id, app_id, token_hash, scopes, secret_version, expires_at)
                           VALUES (%s, %s, %s, %s, %s, %s)""", (app["tenant_id"], app["id"], _hash(token), wanted, app["secret_version"], expires))
        await c.execute("UPDATE core.remote_apps SET last_token_at = now() WHERE id = %s AND status = 'active'", (app["id"],))
    await _audit_token(app, "ok", "", scope, ip)
    return {"access_token": token, "token_type": "Bearer", "expires_in": settings.apps_token_ttl_s,
            "scope": " ".join(wanted) or " ".join(sorted(have)), "company_id": str(app["tenant_id"])}


class AppRefused(Exception):
    """Token hợp lệ về hình thức nhưng app không gọi được lúc này (người bảo lãnh không còn hoạt động)."""


async def principal_for(token: str) -> tuple[Principal, UUID] | None:
    """Người gọi của app cầm ``token`` (+ mã app), hoặc None (token sai / hết hạn / bị thu hồi / app bị thu hồi / khoá đã xoay)."""
    async with db.owner.connection() as c:
        cur = await c.execute(
            """SELECT t.id AS token_id, t.scopes, a.id, a.tenant_id, a.name, a.client_id, a.service_user_id, a.installed_by, a.sponsor_role, a.shadow
               FROM core.remote_app_tokens t JOIN core.remote_apps a ON a.id = t.app_id
               WHERE t.token_hash = %s AND t.revoked_at IS NULL AND t.expires_at > now() AND a.status = 'active'
                 AND t.secret_version = a.secret_version""", (_hash(token),))
        row = await cur.fetchone()
        if row is None:
            return None
        p = await load(c, row, row["scopes"])
        await c.execute("UPDATE core.remote_app_tokens SET last_used_at = now() WHERE id = %s", (row["token_id"],))
    return p, row["id"]


async def role_grants(conn: Conn, tenant_id: UUID, role: str) -> dict[str, Grant] | None:
    """Quyền HIỆN TẠI của một vai (None = công ty không có vai này) — bảo lãnh của app nhà vận hành cài (0206). Lọc công ty rõ ràng: dùng được cả
    bằng kết nối owner (không RLS)."""
    cur = await conn.execute("SELECT 1 FROM core.roles WHERE tenant_id = %s AND key = %s", (tenant_id, role))
    if await cur.fetchone() is None:
        return None
    cur = await conn.execute(auth.GRANTS_SQL, {"t": tenant_id, "role": role})
    return {g["resource"]: Grant(g["scope"], frozenset(g["actions"])) for g in await cur.fetchall()}


async def load(conn: Conn, app: Mapping[str, Any], token_scopes: Iterable[str] = ()) -> Principal:
    """Dựng người gọi của app từ dòng ``core.remote_apps`` (kết nối owner hoặc trong phiên công ty). Người bảo lãnh nạp LẠI từ DB mỗi lần.
    App nhà vận hành cài (0206, ``sponsor_role``): bảo lãnh là quyền HIỆN TẠI của vai đó — không đội (vai không thuộc đội nào), nên app chỉ nhận
    phạm vi own / all lúc cài; đổi quyền của vai (cửa nhà vận hành hoặc quản trị qua duyệt) có hiệu lực từ request kế tiếp."""
    if role := app.get("sponsor_role"):
        held = await role_grants(conn, app["tenant_id"], role)
        if held is None:
            raise AppRefused(f"Vai bảo lãnh app [{role}] không còn — nhà vận hành cài lại hoặc quản trị đổi quyền app (qua duyệt)")
        return issued(Principal(tenant_id=app["tenant_id"], user_id=app["service_user_id"], email=service_email(app["id"]),
                                name=f"Ứng dụng {app['name']}", role=f"app:{app['client_id']}", role_label="Ứng dụng kết nối",
                                grants=effective(await app_grants(conn, app["id"]), held, token_scopes), via="app", ai=False,
                                packs=await packs.enabled(conn, app["tenant_id"]), vocab=await vocab.terms(conn, app["tenant_id"]), app_id=app["id"],
                                shadow=bool(app.get("shadow"))))
    sponsor = await auth.load(conn, app["tenant_id"], user_id=app["installed_by"])
    if sponsor is None:
        raise AppRefused("Người bảo lãnh app không còn hoạt động trong công ty — quản trị đổi quyền app để nhận bảo lãnh (qua duyệt)")
    grants = effective(await app_grants(conn, app["id"]), sponsor.grants, token_scopes)
    return issued(Principal(tenant_id=app["tenant_id"], user_id=app["service_user_id"], email=service_email(app["id"]),
                            name=f"Ứng dụng {app['name']}", role=f"app:{app['client_id']}", role_label="Ứng dụng kết nối", grants=grants,
                            member_teams=sponsor.member_teams, managed_teams=sponsor.managed_teams, via="app", ai=False,
                            packs=sponsor.packs, vocab=sponsor.vocab, app_id=app["id"], shadow=bool(app.get("shadow"))))
