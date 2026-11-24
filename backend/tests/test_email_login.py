"""Đăng nhập bằng MÃ QUA EMAIL (0205 §5c) — cùng luật vào cửa như Google. Canh:
· chỉ email ĐÃ ĐƯỢC MỜI nhận mã; email lạ được trả lời y hệt nhưng không có thư (không lộ ai có tài khoản);
· mã một lần, sai quá 5 lần thì mã chết, xin mã mới thì mã cũ chết; phiên mở ra là phiên thật (provider "email");
· ứng dụng first-party: chỉ SERVER ứng dụng (client secret) xin / kiểm được; mã gắn trình duyệt đã xin (nonce); danh tính amr ["email"], email đã
  xác minh; sai công ty ⇒ từ chối; phiên đăng nhập bằng mã cũng đồng ý được cho ứng dụng first-party (là đăng nhập thật);
· bản cài chưa cấu hình gửi thư (không phải máy dev) ⇒ báo chưa bật, không âm thầm."""

import re
import uuid
from collections.abc import Iterator

import httpx
import pytest

from barista.config import settings
from barista.db import db
from barista.http_api import app
from barista.kernel import email_login, mailer, oauth, ratelimit
from tests.conftest import Who, ok
from tests.test_oauth_identity import SECRET, _first_party

Outbox = list[tuple[str, str, str]]


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[Outbox]:
    sent: Outbox = []

    async def send(to: str, subject: str, text: str) -> None:
        sent.append((to, subject, text))

    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", send)
    ratelimit.reset()
    yield sent


async def _code(box: Outbox, to: str) -> str:
    await email_login.wait_sent()   # thư gửi nền (trả lời không chờ SMTP)
    to_me = [m for m in box if m[0] == to]
    assert to_me, f"không có thư cho {to}"
    m = re.search(r"\b(\d{6})\b", to_me[-1][2])
    assert m
    return m.group(1)


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_api_url.rstrip("/"))


async def _invite(who: Who, local: str) -> tuple[str, uuid.UUID]:
    admin = await who("admin")
    email = f"{local}-{uuid.uuid4().hex[:6]}@mau.test"
    await ok(admin, "admin.invite_user", email=email, name=f"Người {local}", role="sale", team_code="")   # không xếp vào đội mẫu: cơ cấu mẫu là của chung
    return email, admin.tenant_id


async def test_invited_email_gets_a_one_time_code_that_opens_a_real_session(who: Who, outbox: Outbox) -> None:
    email, tenant = await _invite(who, "ma")
    async with client() as c:
        stranger = f"la-{uuid.uuid4().hex[:6]}@mau.test"
        r = await c.post("/v1/auth/email/start", json={"email": stranger})
        await email_login.wait_sent()
        assert r.status_code == 200 and r.json() == {"status": "sent"} and not outbox        # trả lời y hệt, nhưng không gửi thư cho người lạ
        r = await c.post("/v1/auth/email/start", json={"email": email.upper()})
        await email_login.wait_sent()
        assert r.status_code == 200 and len(outbox) == 1 and outbox[0][0] == email
        code = await _code(outbox, email)
        assert code in outbox[0][1]                                                       # mã ở tiêu đề (đọc nhanh trên điện thoại)
        wrong = "000000" if code != "000000" else "111111"
        assert (await c.post("/v1/auth/email/verify", json={"email": email, "code": wrong})).status_code == 401
        r = await c.post("/v1/auth/email/verify", json={"email": email, "code": code})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == email and [t["id"] for t in body["tenants"]] == [str(tenant)] and body["token"]
        m = await c.get("/v1/manifest", headers={"Authorization": f"Bearer {body['token']}", "X-Tenant-Id": str(tenant)})
        assert m.status_code == 200 and m.json()["user"]["email"] == email
        assert (await c.post("/v1/auth/email/verify", json={"email": email, "code": code})).status_code == 401   # dùng một lần


async def test_five_wrong_tries_burn_the_code_and_a_new_code_replaces_the_old(who: Who, outbox: Outbox) -> None:
    email, _ = await _invite(who, "thu")
    async with client() as c:
        await c.post("/v1/auth/email/start", json={"email": email})
        first = await _code(outbox, email)
        await c.post("/v1/auth/email/start", json={"email": email})
        second = await _code(outbox, email)
        if first != second:
            assert (await c.post("/v1/auth/email/verify", json={"email": email, "code": first})).status_code == 401   # mã cũ chết khi xin mã mới
        wrong = "000000" if second != "000000" else "111111"
        for _ in range(5):
            assert (await c.post("/v1/auth/email/verify", json={"email": email, "code": wrong})).status_code == 401
        assert (await c.post("/v1/auth/email/verify", json={"email": email, "code": second})).status_code == 401   # sai 5 lần ⇒ mã chết


async def test_first_party_app_signs_in_by_email_code_through_its_server(who: Who, outbox: Outbox) -> None:
    email, tenant = await _invite(who, "suite")
    cid = f"suite-test-{uuid.uuid4().hex[:6]}"
    await _first_party(cid)
    async with client() as c:
        start = {"client_id": cid, "email": email, "nonce": "trinh-duyet-1"}
        assert (await c.post("/v1/oauth/email/start", json=start)).status_code == 401            # không có client secret
        assert (await c.post("/v1/oauth/email/start", json={**start, "client_secret": "doan"})).status_code == 401
        assert (await c.post("/v1/oauth/email/start", json={**start, "client_secret": SECRET})).status_code == 200
        code = await _code(outbox, email)
        assert "Marketing Suite (test)" in outbox[-1][1]                                                         # thư mang tên ứng dụng
        verify = {"client_id": cid, "client_secret": SECRET, "email": email, "code": code, "tenant_id": str(tenant)}
        assert (await c.post("/v1/oauth/email/verify", json={**verify, "nonce": "trinh-duyet-khac"})).status_code == 400   # trình duyệt khác
        assert (await c.post("/v1/oauth/email/verify", json={**verify, "client_secret": "doan", "nonce": "trinh-duyet-1"})).status_code == 401
        r = await c.post("/v1/oauth/email/verify", json={**verify, "nonce": "trinh-duyet-1"})
        assert r.status_code == 200 and r.json()["scope"] == "identity", r.text
        me = (await c.get("/v1/oauth/userinfo", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).json()
        assert me["email"] == email and me["amr"] == ["email"] and me["email_verified"] is True and me["tenant_id"] == str(tenant)
        # mã của ứng dụng không dùng để đăng nhập Barista được (và ngược lại)
        await c.post("/v1/oauth/email/start", json={**start, "client_secret": SECRET})
        assert (await c.post("/v1/auth/email/verify", json={"email": email, "code": await _code(outbox, email)})).status_code == 401
        # sai công ty
        await c.post("/v1/oauth/email/start", json={**start, "client_secret": SECRET})
        other = await c.post("/v1/oauth/email/verify", json={**verify, "code": await _code(outbox, email), "nonce": "trinh-duyet-1",
                                                               "tenant_id": str(uuid.uuid4())})
        assert other.status_code == 403


async def test_an_email_code_session_is_a_real_login_for_first_party_consent(who: Who) -> None:
    sale = await who("sale1")
    cid = f"suite-test-{uuid.uuid4().hex[:6]}"
    await _first_party(cid)
    async with client() as c:
        r = await c.get("/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": "http://localhost:3001/api/auth/barista/callback",
                                              "state": "s", "code_challenge": "x" * 43, "code_challenge_method": "S256", "scope": "identity"})
        req = re.search(r"req=([^&]+)", r.headers["location"]).group(1)   # type: ignore[union-attr]
    back = await oauth.approve(req, sale, True, login="email")
    assert "code=" in back


async def test_without_mail_configured_an_installation_says_email_login_is_off(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    email, _ = await _invite(who, "tat")
    monkeypatch.setattr(mailer, "configured", lambda: False)
    monkeypatch.setattr(settings, "dev_auth", True)   # bản cài GIỮ đăng nhập dev (chọn vai) — vẫn không được ghi mã ra log
    # cả bản cài không đặt BARISTA_ENV, có khi chỉ đặt địa chỉ web https (bản cài đầu tiên 12/11: API công khai để mặc định http://localhost)
    for env, api, web in (("demo", "http://localhost:8300", "http://localhost:3000"), ("", "https://api.vi-du.test", "http://localhost:3000"),
                          ("", "http://localhost:8300", "https://app.vi-du.test")):
        monkeypatch.setattr(settings, "env", env)
        monkeypatch.setattr(settings, "public_api_url", api)
        monkeypatch.setattr(settings, "web_url", web)
        assert not email_login.available(), (env, api, web)
    ratelimit.reset()
    async with client() as c:
        assert (await c.get("/v1/auth/config")).json()["email"] is False                     # trang không hiện ô nhập mã
        r = await c.post("/v1/auth/email/start", json={"email": email})
        assert r.status_code == 503 and "chưa được bật" in r.json()["detail"]
        monkeypatch.setattr(mailer, "configured", lambda: True)
        assert (await c.get("/v1/auth/config")).json()["email"] is True


async def test_wrong_guesses_add_up_per_email_across_codes_and_paths(who: Who, outbox: Outbox) -> None:
    """Xin mã mới không nạp lại lượt đoán: 20 lần sai rải qua 4 mã (cả đường Barista lẫn ứng dụng) ⇒ khoá — mã đúng cũng bị từ chối, không gửi thêm thư."""
    email, tenant = await _invite(who, "do")
    cid = f"suite-test-{uuid.uuid4().hex[:6]}"
    await _first_party(cid)
    async with client() as c:
        for i in range(4):
            if i % 2 == 0:
                await c.post("/v1/auth/email/start", json={"email": email})
                code = await _code(outbox, email)
                wrong = "000000" if code != "000000" else "111111"
                for _ in range(5):
                    await c.post("/v1/auth/email/verify", json={"email": email, "code": wrong})
            else:
                await c.post("/v1/oauth/email/start", json={"client_id": cid, "client_secret": SECRET, "email": email, "nonce": "n"})
                code = await _code(outbox, email)
                wrong = "000000" if code != "000000" else "111111"
                for _ in range(5):
                    await c.post("/v1/oauth/email/verify", json={"client_id": cid, "client_secret": SECRET, "email": email, "code": wrong, "nonce": "n",
                                                                  "tenant_id": str(tenant)})
            ratelimit.reset()   # giới hạn theo địa chỉ / số lần xin mã ở bộ nhớ — thứ đang thử ở đây là bộ đếm trong DB
        # mã mới: thư không gửi nữa (đang khoá); kể cả có mã còn sống, đoán đúng cũng bị từ chối
        sent_before = len(outbox)
        await c.post("/v1/auth/email/start", json={"email": email})
        await email_login.wait_sent()
        assert len(outbox) == sent_before
    async with db.owner_tx() as conn:   # một mã còn sống, biết trước — đúng mã vẫn bị từ chối khi đang khoá
        row_id = uuid.uuid4()
        await conn.execute("""INSERT INTO core.login_codes (id, email, code_hash, expires_at) VALUES (%s, %s, %s, now() + interval '5 minutes')""",
                           (row_id, email, email_login._hash(row_id, "424242")))
    async with client() as c:
        assert (await c.post("/v1/auth/email/verify", json={"email": email, "code": "424242"})).status_code == 401


async def test_a_right_code_clears_the_wrong_guess_count(who: Who, outbox: Outbox) -> None:
    email, _ = await _invite(who, "xoa")
    async with client() as c:
        await c.post("/v1/auth/email/start", json={"email": email})
        code = await _code(outbox, email)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            await c.post("/v1/auth/email/verify", json={"email": email, "code": wrong})
        assert (await c.post("/v1/auth/email/verify", json={"email": email, "code": code})).status_code == 200
    async with db.owner.connection() as conn:
        n = await (await conn.execute("SELECT coalesce(sum(attempts), 0) AS n FROM core.login_codes WHERE email = %s", (email,))).fetchone()
    assert n is not None and n["n"] == 0
