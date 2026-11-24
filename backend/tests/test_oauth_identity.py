"""Đăng nhập ứng dụng FIRST-PARTY bằng tài khoản Barista (0205) — trên máy chủ OAuth sẵn có của MCP (0093), phạm vi riêng ``identity``. Canh RANH GIỚI:
· trọn vòng: /authorize ⇒ trang đồng ý (chữ "đăng nhập", không phải "đọc dữ liệu") ⇒ mã ⇒ token ``identity`` ⇒ /v1/oauth/userinfo trả đúng người + công ty;
· token ``identity`` KHÔNG vào được MCP; token đọc của ứng dụng AI KHÔNG đổi được thành danh tính;
· đăng ký tự do không xin được ``identity``; client first-party không xin được ``read``, không quay về địa chỉ ngoài danh sách khai;
· bị khoá trong Barista ⇒ userinfo 401 VÀ không làm mới được phiên (kể cả phiên đang mở);
· đăng ký tự do trùng mã không ghi đè được client first-party;
· chỉ phiên đăng nhập THẬT (Google) mới đồng ý được ``identity`` — đăng nhập dev / chọn vai (bản cài giữ để thử) bị từ chối, yêu cầu không bị tiêu;
  userinfo nói cách đăng nhập (``amr``) và chỉ coi email đã xác minh khi Google xác thực."""

import base64
import hashlib
import secrets
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.shared.auth import OAuthClientInformationFull
from psycopg.types.json import Jsonb

from barista.config import settings
from barista.db import db
from barista.http_api import app
from barista.kernel import oauth, sessions
from tests.conftest import Who, ok, owner_val
from tests.test_auth import fake_google  # noqa: F401  — "Google" giả (khoá RSA tự sinh) cho đăng nhập ngay trên trang ứng dụng (§5b)
from tests.test_auth import token as google_token

BACK = "http://localhost:3001/api/auth/barista/callback"


def _pkce() -> tuple[str, str]:
    v = secrets.token_urlsafe(48)
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


SECRET = "bi-mat-cua-server-suite"


async def _first_party(client_id: str) -> None:
    """Như scripts/first_party_client.py (việc của nhà vận hành): client công khai cho /authorize + PKCE, kèm MÃ BĂM client secret cho đường đổi thẳng."""
    info = OAuthClientInformationFull.model_validate({"client_id": client_id, "client_name": "Marketing Suite (test)", "redirect_uris": [BACK],
                                                     "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                                                     "token_endpoint_auth_method": "none", "scope": oauth.IDENTITY})
    data = {**info.model_dump(mode="json"), oauth.SECRET_KEY: oauth.hash_secret(SECRET)}
    async with db.owner_tx() as c:
        await c.execute("INSERT INTO core.mcp_clients (client_id, info) VALUES (%s, %s)", (client_id, Jsonb(data)))


async def _google(email: str, tenant: uuid.UUID) -> dict[str, str]:
    """Tiêu đề của một phiên đăng nhập THẬT (như sau Google OIDC — core.sessions, provider google)."""
    async with db.owner_tx() as c:
        row = await (await c.execute("SELECT id FROM core.users WHERE email = %s", (email,))).fetchone()
        assert row is not None
        uid = row["id"]
        token, _ = await sessions.open_session(c, uid, provider="google")
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": str(tenant)}


async def _login(c: httpx.AsyncClient, client_id: str, email: str, tenant: uuid.UUID) -> dict:
    verifier, challenge = _pkce()
    r = await c.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": BACK, "state": "s",
                                          "code_challenge": challenge, "code_challenge_method": "S256", "scope": "identity"})
    assert r.status_code == 302, r.text
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["signin"] == ["1"] and "app" not in q                          # trang đăng nhập chỉ hiện Google; tên KHÔNG đi trên URL (giả được)
    req = q["req"][0]
    assert (await c.get(f"/v1/oauth/signin/{req}")).json() == {"app": "Marketing Suite (test)"}   # tên thật, hỏi theo mã yêu cầu, chưa cần đăng nhập
    dev = {"X-Dev-User": email, "X-Tenant-Id": str(tenant)}
    info = (await c.get(f"/v1/mcp/requests/{req}", headers=dev)).json()
    assert info["title"].startswith("Đăng nhập Marketing Suite") and "không đọc" in info["action"] and info["scopes"] == ["identity"]
    assert info["auto"] is True                                                                 # first-party: trang tự đồng ý
    refused = await c.post("/v1/mcp/approve", headers=dev, json={"request_id": req, "approve": True})
    assert refused.status_code == 403 and "Google" in refused.json()["detail"]                  # chọn vai (dev) không thành danh tính
    real = await _google(email, tenant)
    back = (await c.post("/v1/mcp/approve", headers=real, json={"request_id": req, "approve": True})).json()["redirect"]   # yêu cầu chưa bị tiêu
    code = parse_qs(urlparse(back).query)["code"][0]
    tok = (await c.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": BACK, "client_id": client_id,
                                        "code_verifier": verifier})).json()
    assert tok["scope"] == "identity", tok
    return tok


async def test_first_party_app_signs_in_with_identity_only_and_loses_it_when_locked(who: Who) -> None:
    admin = await who("admin")
    cid = f"suite-test-{uuid.uuid4().hex[:6]}"
    await _first_party(cid)
    email = f"suite-{uuid.uuid4().hex[:6]}@mau.test"
    await ok(admin, "admin.invite_user", email=email, name="Người đăng nhập Suite", role="sale", team_code="")   # không xếp vào đội mẫu: cơ cấu mẫu là của chung
    base = settings.public_api_url.rstrip("/")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as c:
        tok = await _login(c, cid, email, admin.tenant_id)
        me = (await c.get("/v1/oauth/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"})).json()
        uid = await owner_val("SELECT id FROM core.users WHERE email = %s", (email,))
        assert me["sub"] == str(uid) and me["email"] == email and me["tenant_id"] == str(admin.tenant_id) and me["role"] == "sale"
        assert me["amr"] == ["google"] and me["email_verified"] is True
        again = (await c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid})).json()
        me2 = (await c.get("/v1/oauth/userinfo", headers={"Authorization": f"Bearer {again['access_token']}"})).json()
        assert me2["amr"] == ["google"]                                                             # làm mới giữ cách đăng nhập
        tok = again
        assert (await c.post("/mcp", headers={"Authorization": f"Bearer {tok['access_token']}"}, json={})).status_code in (401, 403)   # không vào MCP

        await ok(admin, "admin.update_member", email=email, active=False)                           # khoá trong Barista
        locked = await c.get("/v1/oauth/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"})
        assert locked.status_code == 401 and locked.headers.get("cache-control") == "no-store"
        refresh = await c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid})
        assert refresh.status_code == 400                                                           # phiên đang mở cũng không làm mới được


async def test_scopes_and_redirects_do_not_cross(who: Who) -> None:
    sale = await who("sale1")
    cid = f"suite-test-{uuid.uuid4().hex[:6]}"
    await _first_party(cid)
    base = settings.public_api_url.rstrip("/")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as c:
        _, ch = _pkce()
        q = {"response_type": "code", "client_id": cid, "state": "s", "code_challenge": ch, "code_challenge_method": "S256"}
        read = await c.get("/authorize", params={**q, "redirect_uri": BACK, "scope": "read"})
        assert read.status_code != 302 or "error" in read.headers.get("location", "")                 # first-party không xin quyền đọc
        evil = await c.get("/authorize", params={**q, "redirect_uri": "https://evil.example/cb", "scope": "identity"})
        assert evil.status_code == 400 or "evil.example" not in evil.headers.get("location", "")      # không quay về địa chỉ lạ

        reg = await c.post("/register", json={"client_name": "Lạ", "redirect_uris": [BACK], "token_endpoint_auth_method": "none",
                                               "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": "identity"})
        assert reg.status_code == 400                                                               # đăng ký tự do không có "identity"

        # token ĐỌC của ứng dụng AI không đổi được thành danh tính
        ai = (await c.post("/register", json={"client_name": "AI", "redirect_uris": ["http://localhost:9999/callback"], "token_endpoint_auth_method": "none",
                                              "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})).json()
        v, ch2 = _pkce()
        r = await c.get("/authorize", params={"response_type": "code", "client_id": ai["client_id"], "redirect_uri": "http://localhost:9999/callback",
                                              "state": "s", "code_challenge": ch2, "code_challenge_method": "S256", "scope": "read"})
        loc = parse_qs(urlparse(r.headers["location"]).query)
        assert "signin" not in loc                                                                  # ứng dụng AI: không phải luồng đăng nhập
        req = loc["req"][0]
        assert (await c.get(f"/v1/oauth/signin/{req}")).status_code == 404                         # không lộ tên ứng dụng AI qua đường không đăng nhập
        dev = {"X-Dev-User": sale.email, "X-Tenant-Id": str(sale.tenant_id)}
        assert (await c.get(f"/v1/mcp/requests/{req}", headers=dev)).json()["auto"] is False      # ứng dụng AI: vẫn phải bấm Cho phép
        back = (await c.post("/v1/mcp/approve", headers=dev, json={"request_id": req, "approve": True})).json()["redirect"]
        tok = (await c.post("/token", data={"grant_type": "authorization_code", "code": parse_qs(urlparse(back).query)["code"][0],
                                            "redirect_uri": "http://localhost:9999/callback", "client_id": ai["client_id"], "code_verifier": v})).json()
        assert (await c.get("/v1/oauth/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"})).status_code == 401

    # đăng ký tự do trùng mã không ghi đè client first-party (phòng thủ ở kho)
    await oauth.BaristaOAuth().register_client(OAuthClientInformationFull.model_validate(
        {"client_id": cid, "client_name": "Giả mạo", "redirect_uris": ["https://evil.example/cb"], "token_endpoint_auth_method": "none", "scope": "read"}))
    assert (await owner_val("SELECT info->>'client_name' FROM core.mcp_clients WHERE client_id = %s", (cid,))) == "Marketing Suite (test)"


# ── 0205 §5b: nút Google của Barista đặt NGAY trên trang ứng dụng first-party — id_token ⇒ token identity, không qua trang Barista ──


async def test_first_party_app_exchanges_a_google_id_token_once_for_identity(who: Who) -> None:
    admin = await who("admin")
    cid = f"suite-test-{uuid.uuid4().hex[:6]}"
    await _first_party(cid)
    email = f"suite-g-{uuid.uuid4().hex[:6]}@mau.test"
    await ok(admin, "admin.invite_user", email=email, name="Người đăng nhập Suite", role="sale", team_code="")   # không xếp vào đội mẫu: cơ cấu mẫu là của chung
    tenant = str(admin.tenant_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_api_url.rstrip("/")) as c:
        async def swap(id_token: str, *, client_id: str = cid, secret: str | None = SECRET, nonce: str = "n-1", tenant_id: str = tenant) -> httpx.Response:
            body = {"client_id": client_id, "id_token": id_token, "nonce": nonce, "tenant_id": tenant_id}
            return await c.post("/v1/oauth/google", json=body if secret is None else {**body, "client_secret": secret})

        # chỉ SERVER ứng dụng (có client secret) đổi được: client_id công khai + nonce đọc được trong id_token không đủ (soát 12/11)
        assert (await swap(google_token(email, jti="khong-secret"), secret=None)).status_code == 401
        assert (await swap(google_token(email, jti="sai-secret"), secret="doan-bua")).status_code == 401

        assert (await swap(google_token(email), nonce="khac")).status_code == 400                     # nonce không khớp phiên trình duyệt
        assert (await swap(google_token(email), tenant_id=str(uuid.uuid4()))).status_code == 403     # không phải thành viên công ty đó
        ai = (await c.post("/register", json={"client_name": "AI", "redirect_uris": ["http://localhost:9999/callback"], "token_endpoint_auth_method": "none",
                                              "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})).json()
        assert (await swap(google_token(email), client_id=ai["client_id"])).status_code == 401       # ứng dụng AI không dùng cửa này được
        stranger = await swap(google_token(f"la-{uuid.uuid4().hex[:6]}@mau.test"))
        assert stranger.status_code == 403                                                           # email chưa được mời

        idt = google_token(email)
        r = await swap(idt)
        assert r.status_code == 200 and r.headers.get("cache-control") == "no-store", r.text
        tok = r.json()
        assert tok["scope"] == "identity" and tok["access_token"] and tok["refresh_token"]
        assert (await swap(idt)).json()["error"] == "invalid_grant"                                   # mỗi id_token đổi MỘT lần
        me = (await c.get("/v1/oauth/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"})).json()
        assert me["email"] == email and me["tenant_id"] == tenant and me["amr"] == ["google"] and me["email_verified"] is True
        again = await c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid})
        assert again.status_code == 200 and again.json()["scope"] == "identity"                       # làm mới như đường /authorize
        assert (await c.post("/mcp", headers={"Authorization": f"Bearer {tok['access_token']}"}, json={})).status_code in (401, 403)

        await ok(admin, "admin.update_member", email=email, active=False)                             # khoá trong Barista ⇒ không đăng nhập lại được
        assert (await swap(google_token(email, jti="sau-khi-khoa"))).status_code == 403
