"""Máy chủ OAuth TỐI GIẢN cho MCP (quyết định 0093) — Barista tự cấp quyền, không dịch vụ ngoài (đăng nhập vẫn là phiên Google OIDC / dev sẵn có).

Thư viện ``mcp`` lo giao thức (đăng ký client, /authorize, /token + PKCE, metadata RFC 8414 / 9728); đây chỉ là KHO: client, yêu cầu chờ đồng
ý, mã cấp quyền, token truy cập / làm mới — mọi giá trị bí mật lưu BĂM (như ``core.sessions``), dùng kết nối owner. Bước ĐỒNG Ý chạy trên web
Barista bằng phiên đăng nhập của người dùng (``approve``): token gắn đúng NGƯỜI + CÔNG TY đang chọn, phạm vi chỉ đọc.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from psycopg.types.json import Jsonb

from barista.config import settings
from barista.db import db
from barista.kernel import email_login, oidc, sessions
from barista.kernel.principal import Principal

SCOPE = "read"                      # đọc trong quyền người đó (0093) — phạm vi mặc định của ứng dụng AI
# 0228: ứng dụng AI của nhân viên SOẠN ĐỀ XUẤT (chạy thử rồi bỏ, thành thẻ chờ trong Barista) — KHÔNG ghi: lệnh chỉ chạy khi chính người đó bấm
# Xác nhận trong app, bằng quyền của họ. Phạm vi riêng, phải xin rõ và người dùng đồng ý rõ; token "read" cũ không tự có.
PROPOSE = "propose"
AI_SCOPES = (SCOPE, PROPOSE)
# 0205: ĐĂNG NHẬP ứng dụng first-party (vd giao diện marketing chạy riêng) bằng tài khoản Barista. Chỉ client do NHÀ VẬN HÀNH khai (phạm vi đăng ký có
# "identity" — đăng ký tự do (DCR) chỉ xin được "read") mới xin được; token "identity" chỉ đọc được DANH TÍNH (``/v1/oauth/userinfo``), không vào MCP.
IDENTITY = "identity"
# Cách đăng nhập THẬT (người chứng minh giữ tài khoản Google / hộp thư) — mới được thành danh tính cho ứng dụng first-party và mới coi email là đã xác minh.
# Đăng nhập dev / chọn vai không nằm ở đây. Thêm cách mới (vd Microsoft) ⇒ thêm vào đây cùng đường kiểm của nó.
REAL_LOGINS = ("google", "email")
REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _epoch(t: datetime) -> int:
    return int(t.timestamp())


async def _row(kind: str, token: str) -> dict[str, Any] | None:
    async with db.owner.connection() as c:
        cur = await c.execute("""SELECT * FROM core.mcp_tokens WHERE kind = %s AND token_hash = %s AND revoked_at IS NULL AND expires_at > now()""",
                              (kind, _hash(token)))
        return await cur.fetchone()


async def _issue(c: Any, kind: str, client_id: str, ttl: timedelta, *, user_id: Any = None, tenant_id: Any = None,
                 scopes: list[str] | None = None, params: dict[str, Any] | None = None) -> tuple[str, datetime]:
    token, expires = secrets.token_urlsafe(32), datetime.now(UTC) + ttl
    await c.execute("""INSERT INTO core.mcp_tokens (kind, token_hash, client_id, user_id, tenant_id, scopes, params, expires_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (kind, _hash(token), client_id, user_id, tenant_id, scopes or [SCOPE], Jsonb(params or {}), expires))
    return token, expires


def redirect_host(uri: str) -> str:
    return (urlparse(uri).hostname or "").lower()


def redirect_allowed(uri: str) -> bool:
    """Bản thật đặt ``mcp_redirect_hosts`` (allowlist tên miền ứng dụng AI) — đăng ký client tự do (DCR) nhưng mã chỉ quay về nơi đã cho phép."""
    hosts = {h.strip().lower() for h in settings.mcp_redirect_hosts.split(",") if h.strip()}
    host = redirect_host(uri)
    return not hosts or any(host == h or host.endswith("." + h) for h in hosts)


def subject(user_id: Any, tenant_id: Any) -> str:
    return f"{user_id}:{tenant_id}"


def parse_subject(sub: str) -> tuple[UUID, UUID]:
    u, t = sub.split(":", 1)
    return UUID(u), UUID(t)


def first_party(client: OAuthClientInformationFull) -> bool:
    """Client do nhà vận hành khai (``scripts/first_party_client.py``): phạm vi đăng ký có ``identity`` — đăng ký tự do không bao giờ có (MCP chỉ nhận
    ``read`` khi đăng ký)."""
    return IDENTITY in (client.scope or "").split()


async def _member_active(user_id: Any, tenant_id: Any) -> bool:
    """Còn là thành viên ĐANG HOẠT ĐỘNG của công ty (khoá ở Barista ⇒ đăng nhập ứng dụng first-party hết hiệu lực ngay, kể cả khi làm mới token)."""
    async with db.owner.connection() as c:
        cur = await c.execute("""SELECT 1 FROM core.memberships m JOIN core.users u ON u.id = m.user_id
                                 WHERE m.user_id = %s AND m.tenant_id = %s AND m.active""", (user_id, tenant_id))
        return await cur.fetchone() is not None


class BaristaOAuth(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """``OAuthAuthorizationServerProvider`` của thư viện mcp — kho trên Postgres. Không nhận ID-JAG doanh nghiệp (mặc định của thư viện từ chối)."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with db.owner.connection() as c:
            cur = await c.execute("SELECT info FROM core.mcp_clients WHERE client_id = %s", (client_id,))
            row = await cur.fetchone()
        return OAuthClientInformationFull.model_validate(row["info"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        async with db.owner_tx() as c:
            # đăng ký tự do KHÔNG BAO GIỜ ghi đè client first-party của nhà vận hành (0205) — phòng thủ thêm, dù thư viện tự sinh mã client
            await c.execute("""INSERT INTO core.mcp_clients (client_id, info) VALUES (%s, %s) ON CONFLICT (client_id) DO UPDATE SET info = EXCLUDED.info
                               WHERE NOT (coalesce(core.mcp_clients.info->>'scope', '') ~ '(^| )identity( |$)')""",
                            (client_info.client_id, Jsonb(client_info.model_dump(mode="json"))))

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Ghi yêu cầu chờ đồng ý ⇒ chuyển người dùng sang trang đồng ý trên web Barista (đăng nhập ở đó nếu chưa)."""
        fp = first_party(client)
        if fp and set(params.scopes or []) != {IDENTITY}:   # ứng dụng đăng nhập chỉ xin danh tính — không gộp quyền đọc dữ liệu vào cùng token
            raise AuthorizeError("invalid_scope", f"Ứng dụng đăng nhập chỉ xin phạm vi “{IDENTITY}”")
        if not fp and params.scopes and (set(params.scopes) - set(AI_SCOPES) or SCOPE not in params.scopes):
            raise AuthorizeError("invalid_scope", f"Chỉ cấp phạm vi “{SCOPE}” (đọc), kèm “{PROPOSE}” (soạn đề xuất) nếu cần")
        # first-party: địa chỉ quay về đã khớp TUYỆT ĐỐI danh sách nhà vận hành khai (thư viện kiểm) ⇒ không cần allowlist tên miền của ứng dụng AI
        if not fp and not redirect_allowed(str(params.redirect_uri)):
            raise AuthorizeError("unauthorized_client", "Ứng dụng này chưa được phép nối vào Barista")
        async with db.owner_tx() as c:
            rid, _ = await _issue(c, "request", str(client.client_id), REQUEST_TTL, scopes=list(params.scopes or [SCOPE]), params={
                "state": params.state, "code_challenge": params.code_challenge, "redirect_uri": str(params.redirect_uri),
                "explicit": params.redirect_uri_provided_explicitly, "resource": params.resource})
        if fp:   # đăng nhập ứng dụng first-party: trang đăng nhập Barista chỉ hiện Google; TÊN ứng dụng hỏi server theo mã yêu cầu (signin_app), không lấy từ URL
            return f"{settings.web_url.rstrip('/')}/connect/ai?req={rid}&signin=1"
        return f"{settings.web_url.rstrip('/')}/connect/ai?req={rid}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        r = await _row("code", authorization_code)
        if r is None or r["client_id"] != client.client_id:
            return None
        p = r["params"]
        return AuthorizationCode(code=authorization_code, scopes=r["scopes"], expires_at=r["expires_at"].timestamp(), client_id=r["client_id"],
                                 code_challenge=p["code_challenge"], redirect_uri=p["redirect_uri"], redirect_uri_provided_explicitly=p["explicit"],
                                 resource=p.get("resource"), subject=subject(r["user_id"], r["tenant_id"]))

    async def _tokens(self, c: Any, client_id: str, sub: str, scopes: list[str], resource: str | None, amr: list[str] | None = None) -> OAuthToken:
        """``amr``: cách người đó đăng nhập lúc đồng ý (vd ["google"]) — mang theo qua mọi lần làm mới để userinfo nói thật (0205)."""
        uid, tid = parse_subject(sub)
        p = {"resource": resource, "amr": list(amr or [])}
        access, _ = await _issue(c, "access", client_id, ACCESS_TTL, user_id=uid, tenant_id=tid, scopes=scopes, params=p)
        refresh, _ = await _issue(c, "refresh", client_id, REFRESH_TTL, user_id=uid, tenant_id=tid, scopes=scopes, params=p)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=int(ACCESS_TTL.total_seconds()), refresh_token=refresh,
                          scope=" ".join(scopes))

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        async with db.owner_tx() as c:   # mã dùng MỘT lần: thu hồi trong cùng giao dịch cấp token
            cur = await c.execute("""UPDATE core.mcp_tokens SET revoked_at = now() WHERE kind = 'code' AND token_hash = %s AND revoked_at IS NULL
                                     RETURNING id, params""", (_hash(authorization_code.code),))
            row = await cur.fetchone()
            if row is None:
                raise TokenError("invalid_grant", "Mã cấp quyền đã dùng hoặc hết hạn")
            assert authorization_code.subject
            return await self._tokens(c, str(client.client_id), authorization_code.subject, authorization_code.scopes, authorization_code.resource,
                                      (row["params"] or {}).get("amr"))

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        r = await _row("refresh", refresh_token)
        if r is None or r["client_id"] != client.client_id:
            return None
        if IDENTITY in r["scopes"] and not await _member_active(r["user_id"], r["tenant_id"]):
            return None   # bị khoá / rời công ty ⇒ không làm mới được phiên đăng nhập ứng dụng first-party
        return RefreshToken(token=refresh_token, client_id=r["client_id"], scopes=r["scopes"], expires_at=_epoch(r["expires_at"]),
                            resource=r["params"].get("resource"), subject=subject(r["user_id"], r["tenant_id"]))

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        async with db.owner_tx() as c:   # xoay vòng: token làm mới cũ hết hiệu lực
            cur = await c.execute("""UPDATE core.mcp_tokens SET revoked_at = now() WHERE kind = 'refresh' AND token_hash = %s AND revoked_at IS NULL
                                     RETURNING id, params""", (_hash(refresh_token.token),))
            row = await cur.fetchone()
            if row is None:
                raise TokenError("invalid_grant", "Token làm mới đã dùng hoặc bị thu hồi")
            assert refresh_token.subject
            return await self._tokens(c, str(client.client_id), refresh_token.subject, scopes or refresh_token.scopes, refresh_token.resource,
                                      (row["params"] or {}).get("amr"))

    async def load_access_token(self, token: str) -> AccessToken | None:
        r = await _row("access", token)
        if r is None:
            return None
        async with db.owner_tx() as c:
            await c.execute("UPDATE core.mcp_tokens SET last_used_at = now() WHERE id = %s", (r["id"],))
        return AccessToken(token=token, client_id=r["client_id"], scopes=r["scopes"], expires_at=_epoch(r["expires_at"]),
                           resource=r["params"].get("resource"), subject=subject(r["user_id"], r["tenant_id"]))

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        async with db.owner_tx() as c:
            await c.execute("UPDATE core.mcp_tokens SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL", (_hash(token.token),))


# ── Bước đồng ý trên web (phiên đăng nhập của người dùng, công ty đang chọn) ──

async def describe(request_id: str) -> dict[str, Any] | None:
    """Trang đồng ý hỏi: ứng dụng nào xin, quyền gì — chưa gắn người (yêu cầu đến trước khi đăng nhập)."""
    r = await _row("request", request_id)
    if r is None:
        return None
    async with db.owner.connection() as c:
        cur = await c.execute("SELECT info FROM core.mcp_clients WHERE client_id = %s", (r["client_id"],))
        client = await cur.fetchone()
    info = client["info"] if client else {}
    signin = IDENTITY in r["scopes"]
    drafts = PROPOSE in r["scopes"]
    name = info.get("client_name") or r["client_id"]
    return {"client_name": name, "client_uri": info.get("client_uri") or "", "scopes": r["scopes"],
            # chữ cho trang đồng ý (app chỉ vẽ — 0205): đăng nhập ứng dụng first-party khác cho ứng dụng AI đọc dữ liệu
            "title": f"Đăng nhập {name} bằng tài khoản Barista" if signin else "Cho phép ứng dụng AI kết nối",
            # ứng dụng first-party (nhà vận hành khai, chỉ nhận danh tính) ⇒ trang tự đồng ý, không bắt bấm; server vẫn kiểm đăng nhập Google thật
            "auto": signin,
            "action": "chỉ nhận họ tên, email, vai trò và công ty của anh/chị — không đọc dữ liệu" if signin else "đọc dữ liệu và soạn đề xuất chờ anh/chị bấm" if drafts else "đọc dữ liệu",
            "points": (["Chỉ nhận họ tên, email, vai trò và công ty — không đọc khách, lead, tiền hay tài liệu.",
                        "Bị khoá trong Barista ⇒ hết đăng nhập ứng dụng này ngay, kể cả phiên đang mở.",
                        "Chỉ công ty đang chọn — muốn công ty khác thì đổi công ty rồi đăng nhập lại."] if signin else
                       ["Đọc và SOẠN ĐỀ XUẤT — không tự tạo, sửa, duyệt hay xoá: mỗi đề xuất chỉ chạy khi chính bạn bấm Xác nhận trong Barista.",
                        "Chỉ thấy và chỉ soạn được đúng phần bạn được làm trong Barista; việc duyệt và phân quyền không soạn được từ ứng dụng — người tự làm trong Barista.",
                        "Chỉ công ty đang chọn — muốn công ty khác thì đổi công ty rồi kết nối lại."] if drafts else
                       ["Chỉ đọc — không tạo, sửa, duyệt hay xoá được gì.", "Chỉ thấy đúng phần bạn được xem trong Barista.",
                        "Chỉ công ty đang chọn — muốn công ty khác thì đổi công ty rồi kết nối lại."]),
            "redirect_host": redirect_host(r["params"]["redirect_uri"]),   # tên client tự khai được — tên miền quay về thì không giả được
            "expires_at": r["expires_at"]}


class NeedsRealLogin(Exception):
    """Đăng nhập ứng dụng first-party (``identity``) chỉ từ phiên đăng nhập THẬT — không từ đăng nhập dev / chọn vai (0205)."""


async def approve(request_id: str, p: Principal, ok: bool, login: str | None = None) -> str:
    """Người dùng đồng ý / từ chối ⇒ địa chỉ quay về ứng dụng AI (mã cấp quyền gắn người + công ty, hoặc lỗi access_denied). Yêu cầu dùng một lần.
    ``login``: cách đăng nhập của phiên đang đồng ý (vd "google"; None = đăng nhập dev / chọn vai). Yêu cầu ``identity`` mà không phải đăng nhập thật
    ⇒ NeedsRealLogin, kiểm TRƯỚC khi tiêu yêu cầu (bản cài giữ màn chọn vai để thử thì ai qua được cổng cũng chọn được người bất kỳ — không được thành
    danh tính cho ứng dụng khác tin)."""
    if ok:
        r0 = await _row("request", request_id)
        if r0 is not None and IDENTITY in r0["scopes"] and login not in REAL_LOGINS:
            raise NeedsRealLogin("Đăng nhập ứng dụng bằng tài khoản Barista cần đăng nhập thật (Google hoặc mã qua email) — đăng nhập chọn vai (thử "
                                 "nghiệm) không dùng được. Đăng xuất, đăng nhập lại rồi thử lại.")
    async with db.owner_tx() as c:
        cur = await c.execute("""UPDATE core.mcp_tokens SET revoked_at = now() WHERE kind = 'request' AND token_hash = %s AND revoked_at IS NULL
                                 AND expires_at > now() RETURNING *""", (_hash(request_id),))
        r = await cur.fetchone()
        if r is None:
            raise LookupError("Yêu cầu kết nối đã dùng hoặc hết hạn — bấm kết nối lại từ ứng dụng AI")
        params = r["params"]
        if not ok:
            return construct_redirect_uri(params["redirect_uri"], error="access_denied", state=params["state"])
        code, _ = await _issue(c, "code", r["client_id"], CODE_TTL, user_id=p.user_id, tenant_id=p.tenant_id, scopes=r["scopes"],
                               params={**params, "amr": [login] if login else []})
    return construct_redirect_uri(params["redirect_uri"], code=code, state=params["state"])



async def identity(token: str) -> dict[str, Any] | None:
    """Danh tính của token ``identity`` còn hiệu lực (0205) — NẠP LẠI mỗi lần: người bị khoá / rời công ty ⇒ None. Không phải token ``identity`` ⇒ None
    (token đọc dữ liệu của ứng dụng AI không dùng làm đăng nhập được)."""
    r = await _row("access", token)
    if r is None or IDENTITY not in r["scopes"]:
        return None
    async with db.owner.connection() as c:
        cur = await c.execute("""SELECT u.id, u.email, u.name, m.role, coalesce(ro.label, m.role) AS role_label, t.id AS tenant_id, t.name AS tenant
                                 FROM core.memberships m JOIN core.users u ON u.id = m.user_id JOIN core.tenants t ON t.id = m.tenant_id
                                 LEFT JOIN core.roles ro ON ro.tenant_id = m.tenant_id AND ro.key = m.role
                                 WHERE m.user_id = %s AND m.tenant_id = %s AND m.active""", (r["user_id"], r["tenant_id"]))
        row = await cur.fetchone()
    if row is None:
        return None
    amr = list((r["params"] or {}).get("amr") or [])
    return {"sub": str(row["id"]), "email": row["email"], "email_verified": any(a in REAL_LOGINS for a in amr),   # Google / mã email đã chứng minh hộp thư
            "amr": amr, "name": row["name"], "role": row["role"], "role_label": row["role_label"], "tenant_id": str(row["tenant_id"]),
            "tenant": row["tenant"], "client_id": r["client_id"]}


async def signin_app(request_id: str) -> str | None:
    """Tên ứng dụng first-party của một yêu cầu đăng nhập còn hiệu lực — cho trang đăng nhập (người CHƯA đăng nhập) ghi "Đăng nhập <ứng dụng>" bằng tên
    nhà vận hành khai, không bằng chữ trên URL (giả được). Mã yêu cầu ngẫu nhiên, chỉ ai đang đi đúng luồng mới có; chỉ trả TÊN, không gì khác.
    Không phải yêu cầu đăng nhập first-party ⇒ None."""
    r = await _row("request", request_id)
    if r is None or IDENTITY not in r["scopes"]:
        return None
    async with db.owner.connection() as c:
        cur = await c.execute("SELECT info->>'client_name' AS name FROM core.mcp_clients WHERE client_id = %s", (r["client_id"],))
        row = await cur.fetchone()
    return (row["name"] if row else None) or None


# ── Đăng nhập NGAY TRÊN trang ứng dụng first-party (0205 §5b): nút Google của chính Barista đặt ở trang ứng dụng (cửa sổ nhỏ của Google, không rời
# trang) ⇒ ứng dụng gửi id_token sang đây ⇒ Barista kiểm Google + thành viên rồi mới cấp token ``identity`` — như đi qua /authorize, chỉ bỏ trang Barista.

class SignInRefused(Exception):
    """``code``: mã lỗi OAuth (``unauthorized_client`` / ``invalid_request`` / ``invalid_grant`` / ``access_denied``) — chữ cho người đọc ở ``args[0]``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


SECRET_KEY = "barista_secret_sha256"   # trong core.mcp_clients.info: MÃ BĂM client secret của ứng dụng first-party (không bao giờ giữ secret thô)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


async def _secret_ok(client_id: str, secret: str) -> bool:
    """Server ứng dụng first-party chứng minh là chính nó (client bí mật — secret chỉ ở server ứng dụng, không bao giờ ra trình duyệt). Client chưa có
    secret ⇒ không dùng được đường đổi thẳng (vẫn dùng /authorize + PKCE được)."""
    async with db.owner.connection() as c:
        cur = await c.execute("SELECT info->>%s AS h FROM core.mcp_clients WHERE client_id = %s", (SECRET_KEY, client_id))
        row = await cur.fetchone()
    stored = (row["h"] if row else None) or ""
    return bool(stored) and bool(secret) and hmac.compare_digest(stored, hash_secret(secret))


async def google_signin(client_id: str, client_secret: str, id_token: str, nonce: str, tenant_id: str) -> OAuthToken:
    """SERVER ứng dụng (client_id + client secret) gửi id_token Google (``aud`` = client Google của Barista; ``nonce`` do server ứng dụng sinh và tự kiểm
    gắn với cookie trình duyệt — Barista chỉ kiểm nonce khớp id_token, không thấy được trình duyệt) ⇒ token ``identity`` của người đó tại
    công ty ``tenant_id``. Chỉ client first-party; người phải là thành viên ĐANG HOẠT ĐỘNG của đúng công ty; email Google phải là email đã được mời
    (cùng luật đăng nhập Barista — ``sessions.bind_google``). Mỗi id_token đổi MỘT lần (ghi mã băm như một yêu cầu đăng nhập ĐÃ TIÊU — thu hồi ngay, không ai đọc lại được) ⇒ id_token lộ ra không
    dùng lại được. Không mở phiên Barista (người đó mở Barista thì đăng nhập riêng)."""
    await app_client(client_id, client_secret)
    tid = _tenant(tenant_id)
    try:
        ident = await oidc.verify_google(id_token, nonce)
    except oidc.OIDCError as e:
        raise SignInRefused("invalid_grant", "Không xác thực được tài khoản Google — thử đăng nhập lại") from e
    async with db.owner_tx() as c:
        cur = await c.execute("""INSERT INTO core.mcp_tokens (kind, token_hash, client_id, scopes, params, expires_at, revoked_at)
                                 VALUES ('request', %s, %s, %s, '{}', now() + interval '2 hours', now()) ON CONFLICT (token_hash) DO NOTHING RETURNING id""",
                              (_hash("google-id-token:" + id_token), client_id, [IDENTITY]))
        if await cur.fetchone() is None:
            raise SignInRefused("invalid_grant", "Lượt đăng nhập Google này đã dùng — bấm đăng nhập lại")
        try:
            uid = await sessions.bind_google(c, ident)
        except sessions.LoginRefused as e:
            raise SignInRefused("access_denied", str(e)) from e
        return await _identity_tokens(c, client_id, uid, tid, "google")


async def app_client(client_id: str, client_secret: str) -> str:
    """Người gọi là SERVER của một ứng dụng first-party (client_id + client secret). client_id công khai ⇒ không đủ để tin: thiếu secret thì ai cầm được
    giấy tờ rò rỉ (id_token, mã) cũng đổi được ra danh tính (soát: phiên giữ lõi 12/11). Trả tên ứng dụng (cho thư gửi mã)."""
    client = await BaristaOAuth().get_client(client_id) if client_id else None
    if client is None or not first_party(client) or not await _secret_ok(client_id, client_secret):
        raise SignInRefused("unauthorized_client", "Ứng dụng này không được đăng nhập bằng tài khoản Barista")
    return client.client_name or client_id


def _tenant(tenant_id: str) -> UUID:
    try:
        return UUID(tenant_id)
    except ValueError as e:
        raise SignInRefused("invalid_request", "Thiếu mã công ty") from e


async def _identity_tokens(c: Any, client_id: str, uid: UUID, tid: UUID, login: str) -> OAuthToken:
    """Thành viên ĐANG HOẠT ĐỘNG của đúng công ty ⇒ token ``identity`` (amr = cách đăng nhập) — y như đường /authorize."""
    cur = await c.execute("SELECT 1 FROM core.memberships WHERE user_id = %s AND tenant_id = %s AND active", (uid, tid))
    if await cur.fetchone() is None:
        raise SignInRefused("access_denied", "Tài khoản không thuộc công ty này hoặc đã bị khoá")
    return await BaristaOAuth()._tokens(c, client_id, subject(uid, tid), [IDENTITY], None, [login])


async def email_signin(client_id: str, client_secret: str, email: str, code: str, nonce: str, tenant_id: str) -> OAuthToken:
    """0205 §5c: SERVER ứng dụng first-party gửi mã người dùng nhận qua email (server đã xin mã bằng ``email_login.send_code`` với cùng ``nonce``) ⇒
    token ``identity`` (amr ["email"]). Cùng luật như đổi id_token Google: client secret, thành viên đúng công ty."""
    await app_client(client_id, client_secret)
    tid = _tenant(tenant_id)
    try:
        uid = await email_login.check_code(email, code, client_id=client_id, nonce=nonce or None)
    except email_login.CodeRefused as e:
        raise SignInRefused("invalid_grant", str(e)) from e
    async with db.owner_tx() as c:
        return await _identity_tokens(c, client_id, uid, tid, "email")
