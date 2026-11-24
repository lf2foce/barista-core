"""Đăng nhập thật (quyết định 0040): id_token Google kiểm đủ trường, gắn danh tính theo `sub` (không theo email), phiên lưu Postgres
thu hồi được, quyền vẫn nạp lại mỗi request. "Google" ở đây là khoá RSA tự sinh — đường kiểm còn lại là code thật."""

import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from barista.config import settings
from barista.db import db
from barista.http_api import app
from barista.kernel import oidc
from tests.conftest import Who, ok, owner_val

CLIENT = "barista-test-client.apps.googleusercontent.com"
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def fake_google() -> Iterator[None]:
    old = (oidc.key_resolver, settings.google_client_ids, settings.google_app_client_id, settings.google_hosted_domains, settings.dev_auth)
    oidc.key_resolver = lambda _token: _key.public_key()
    settings.google_client_ids, settings.google_app_client_id, settings.google_hosted_domains = [CLIENT], CLIENT, []
    yield
    oidc.key_resolver, settings.google_client_ids, settings.google_app_client_id, settings.google_hosted_domains, settings.dev_auth = old


def token(email: str, *, sub: str | None = None, nonce: str = "n-1", aud: str = CLIENT, iss: str = "https://accounts.google.com",
          verified: bool = True, exp_in: int = 600, key: Any = None, **extra: Any) -> str:
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": sub or f"sub-{email}", "email": email, "email_verified": verified, "name": "Người thử",
              "iat": now, "exp": now + exp_in, "nonce": nonce, **extra}
    return jwt.encode(claims, key or _key, algorithm="RS256")


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def login(c: AsyncClient, id_token: str, nonce: str = "n-1") -> Any:
    return await c.post("/v1/auth/google", json={"id_token": id_token, "nonce": nonce})


async def _invite(admin: Any, local: str) -> str:
    email = f"{local}-{uuid.uuid4().hex[:6]}@sanmau.demo"
    await ok(admin, "admin.invite_user", email=email, name=f"Người {local}", role="sale", team_code="")   # không xếp vào đội mẫu: cơ cấu mẫu là của chung
    return email


async def test_google_login_opens_a_session_that_works_without_dev_headers(who: Who) -> None:
    admin = await who("admin")
    email = await _invite(admin, "dangnhap")
    async with client() as c:
        r = await login(c, token(email))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == email and len(body["tenants"]) == 1 and body["token"]
        settings.dev_auth = False                                   # đăng nhập thật không cần (và không dựa vào) chế độ dev
        h = {"Authorization": f"Bearer {body['token']}", "X-Tenant-Id": body["tenants"][0]["id"]}
        m = await c.get("/v1/manifest", headers=h)
        assert m.status_code == 200 and m.json()["user"]["email"] == email
        assert (await c.get("/v1/manifest", headers={"X-Dev-User": email, "X-Tenant-Id": body["tenants"][0]["id"]})).status_code == 401
        cfg = (await c.get("/v1/auth/config")).json()
        assert cfg["google"]["client_id"] == CLIENT and cfg["dev"] is False
    stored = await owner_val("SELECT token_hash FROM core.sessions WHERE user_id = (SELECT id FROM core.users WHERE email = %s)", (email,))
    assert stored and stored != body["token"]                       # DB chỉ giữ mã băm, không giữ mã phiên


@pytest.mark.parametrize("bad", [
    {"aud": "someone-else.apps.googleusercontent.com"},            # token cấp cho ứng dụng khác
    {"iss": "https://evil.example.com"},
    {"verified": False},
    {"exp_in": -600},                                              # hết hạn (quá cả độ lệch 60 giây)
    {"key": _other_key},                                           # chữ ký không phải của "Google"
    {"nonce": "n-khac"},                                           # token lấy ở luồng khác
])
async def test_bad_id_tokens_are_refused(who: Who, bad: dict[str, Any]) -> None:
    email = await _invite(await who("admin"), "xau")
    async with client() as c:
        r = await login(c, token(email, **bad))
    assert r.status_code == 401 and "token" not in r.json()


async def test_only_invited_people_get_in_and_hosted_domain_is_enforced_when_set(who: Who) -> None:
    async with client() as c:
        assert (await login(c, token("nguoila@gmail.com"))).status_code == 403   # Google hợp lệ nhưng chưa được mời
        email = await _invite(await who("admin"), "mien")
        settings.google_hosted_domains = ["sanmau.vn"]
        assert (await login(c, token(email, hd="khac.vn"))).status_code == 401
        assert (await login(c, token(email, hd="sanmau.vn"))).status_code == 200


async def test_identity_is_bound_by_sub_and_a_reused_email_needs_an_admin(who: Who) -> None:
    """Email công ty cấp lại cho người mới (Google cấp `sub` MỚI) không được thừa kế tài khoản cũ — quản trị gỡ liên kết thì mới vào."""
    admin = await who("admin")
    email = await _invite(admin, "taisudung")
    async with client() as c:
        first = await login(c, token(email, sub="google-old"))
        assert first.status_code == 200
        assert (await login(c, token(email, sub="google-old"))).status_code == 200     # cùng sub: vào bình thường
        reused = await login(c, token(email, sub="google-new"))
        assert reused.status_code == 403 and "gỡ liên kết" in reused.json()["detail"]
        out = await ok(admin, "admin.unlink_google", email=email)
        assert out["unlinked"] == 1 and out["sessions_revoked"] >= 2                  # gỡ liên kết thu hồi luôn mọi phiên cũ
        h = {"Authorization": f"Bearer {first.json()['token']}", "X-Tenant-Id": first.json()["tenants"][0]["id"]}
        assert (await c.get("/v1/manifest", headers=h)).status_code == 401
        assert (await login(c, token(email, sub="google-new"))).status_code == 200    # sau khi quản trị gỡ: gắn sub mới


async def test_logout_revokes_and_deactivation_bites_immediately(who: Who) -> None:
    admin = await who("admin")
    email = await _invite(admin, "dangxuat")
    async with client() as c:
        a = (await login(c, token(email))).json()
        b = (await login(c, token(email))).json()
        ha = {"Authorization": f"Bearer {a['token']}", "X-Tenant-Id": a["tenants"][0]["id"]}
        hb = {"Authorization": f"Bearer {b['token']}", "X-Tenant-Id": b["tenants"][0]["id"]}
        assert (await c.post("/v1/auth/logout", headers=ha)).status_code == 200
        assert (await c.get("/v1/manifest", headers=ha)).status_code == 401           # phiên đã đăng xuất
        assert (await c.get("/v1/manifest", headers=hb)).status_code == 200           # thiết bị khác vẫn chạy
        await ok(admin, "admin.update_member", email=email, active=False)              # khoá ở hệ thống ⇒ hiệu lực ngay request sau
        gone = await c.get("/v1/manifest", headers=hb)
        # app quyết theo MÃ máy đọc (xoá phiên, về đăng nhập), không theo chữ; chữ tiếng Việt cho người vẫn nguyên
        assert gone.status_code == 403 and gone.headers["x-barista-error"] == "not_member" and "thành viên" in gone.json()["detail"]
        other = {"Authorization": f"Bearer {b['token']}", "X-Tenant-Id": str(uuid.uuid4())}
        assert (await c.get("/v1/manifest", headers=other)).status_code == 403        # công ty không thuộc về


async def test_expired_session_is_refused(who: Who) -> None:
    email = await _invite(await who("admin"), "hethan")
    async with client() as c:
        s = (await login(c, token(email))).json()
        async with db.owner_tx() as conn:
            await conn.execute("UPDATE core.sessions SET expires_at = now() - interval '1 minute' WHERE user_id = "
                               "(SELECT id FROM core.users WHERE email = %s)", (email,))
        h = {"Authorization": f"Bearer {s['token']}", "X-Tenant-Id": s["tenants"][0]["id"]}
        assert (await c.get("/v1/manifest", headers=h)).status_code == 401


async def test_last_seen_is_written_at_most_once_a_minute_and_validity_is_still_checked_every_request(who: Who) -> None:
    """Đo 12/11/2026: mỗi request từng GHI một dòng phiên (WAL + fsync) chỉ để giữ ``last_seen_at``. Giờ chỉ ghi khi dấu đã cũ hơn 60 giây; phiên thu hồi vẫn
    bị từ chối ngay ở request kế tiếp."""
    email = await _invite(await who("admin"), "seen")
    async with client() as c:
        s = (await login(c, token(email))).json()
        h = {"Authorization": f"Bearer {s['token']}", "X-Tenant-Id": s["tenants"][0]["id"]}
        uid = "(SELECT id FROM core.users WHERE email = %s)"
        async with db.owner_tx() as conn:
            await conn.execute(f"UPDATE core.sessions SET last_seen_at = now() - interval '10 seconds' WHERE user_id = {uid}", (email,))
        recent = await owner_val(f"SELECT last_seen_at FROM core.sessions WHERE user_id = {uid}", (email,))
        assert (await c.get("/v1/manifest", headers=h)).status_code == 200
        assert await owner_val(f"SELECT last_seen_at FROM core.sessions WHERE user_id = {uid}", (email,)) == recent   # còn mới ⇒ không ghi
        async with db.owner_tx() as conn:
            await conn.execute(f"UPDATE core.sessions SET last_seen_at = now() - interval '5 minutes' WHERE user_id = {uid}", (email,))
        stale = await owner_val(f"SELECT last_seen_at FROM core.sessions WHERE user_id = {uid}", (email,))
        assert (await c.get("/v1/manifest", headers=h)).status_code == 200
        assert await owner_val(f"SELECT last_seen_at FROM core.sessions WHERE user_id = {uid}", (email,)) > stale        # đã cũ ⇒ ghi lại
        assert (await c.post("/v1/auth/logout", headers=h)).status_code == 200
        assert (await c.get("/v1/manifest", headers=h)).status_code == 401                                              # thu hồi: chặn ngay
