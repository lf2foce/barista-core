"""App từ xa (0140, 0130 lát 5) — trọn vòng với một APP GIẢ trong tiến trình (FastAPI riêng, nói chuyện với Barista qua HTTP như máy chủ khác):
cài (chờ chủ tài khoản duyệt) → người xin nhận khoá một lần → xin token (client credentials) → nạp lead hai lần (một lead) → nhận webhook ký
HMAC khi lead được giao → gọi ngược đọc bằng quyền của app → không vượt vai, không duyệt, không gọi thao tác nội bộ / quản trị → thu hồi ⇒ token
bị từ chối, webhook dừng."""

import asyncio
import itertools
import json
import uuid
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from barista import seed
from barista.config import settings
from barista.db import db
from barista.http_api import app
from barista.kernel import executor, ratelimit, remote_apps, webhooks
from barista.kernel.clock import today
from barista.kernel.principal import Grant, Principal
from tests.conftest import edge, fresh_phone, ok, owner_val

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("giamdoc", "Giám đốc", "director", "HQ", True),
          ("a", "Sale A", "sale", "KD-A", False)]
HOOK_URL = "https://app-gia.test/hooks"
GRANTS = "leadflow:team:write; crm:all:read"


class FakeApp:
    """Máy chủ của app ngoài: nhận webhook (kiểm chữ ký, chống lặp theo webhook-id), gọi ngược Barista bằng token của chính nó."""

    def __init__(self, barista: httpx.AsyncClient) -> None:
        self.barista = barista
        self.secret = ""
        self.token = ""
        self.seen: set[str] = set()
        self.events: list[dict[str, Any]] = []
        self.reads: list[dict[str, Any]] = []
        self.server = FastAPI()

        @self.server.post("/hooks")
        async def hooks(request: Request) -> JSONResponse:
            body = await request.body()
            if not webhooks.verify(self.secret, dict(request.headers), body):
                return JSONResponse({"error": "bad signature"}, status_code=401)
            if request.headers["webhook-id"] in self.seen:   # chống phát lại
                return JSONResponse({"ok": True, "replay": True})
            self.seen.add(request.headers["webhook-id"])
            ev = json.loads(body)
            self.events.append(ev)
            if ev["type"].startswith("lead."):   # gọi ngược: đọc bằng quyền của app
                r = await self.barista.post("/v1/queries/crm.lead_status", json={"lead_id": ev["data"]["lead_id"]},
                                            headers={"Authorization": f"Bearer {self.token}"})
                self.reads.append(r.json())
            return JSONResponse({"ok": True})

    async def send(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        assert url == HOOK_URL
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.server), base_url="https://app-gia.test") as c:
            return (await c.post("/hooks", content=body, headers=headers)).status_code


@pytest.fixture
async def company() -> dict[str, Any]:
    settings.apps_signing_secret = "test-signing-secret"
    ratelimit.reset()
    domain = f"ra{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty app {domain}", domain, people=PEOPLE)
    people = {k: await seed.who(tid, k, domain) for k, *_ in PEOPLE}
    await ok(people["admin"], "leadflow.set_rule", name="Chia lead", effective_from=(today() - timedelta(days=1)).isoformat(), roles=["sale"],
             cap_uncalled=0, first_call_min=15, max_recalls=2, days="all", open_at="00:00", close_at="24:00")
    return {"tid": tid, **people}


async def _approve_as_owner(owner: Principal, r: executor.Result) -> Any:
    assert r.status == "pending_approval", (r.status, r.error)
    d = await executor.run(edge(owner), "core.decide", {"approval_id": r.approval_id, "approve": True})
    assert d.status == "ok" and d.output["status"] == "executed", d
    return d.output["result"]


async def _install(c: dict[str, Any], *, grants: str = GRANTS, events: str = "lead.assigned, lead.stage_changed") -> dict[str, Any]:
    r = await executor.run(c["admin"], "connect.install", {"name": "App giả", "grants": grants, "events": events, "webhook_url": HOOK_URL})
    return await _approve_as_owner(c["giamdoc"], r)


async def _token(h: httpx.AsyncClient, client_id: str, secret: str, **extra: str) -> httpx.Response:
    return await h.post("/v1/apps/token", data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret, **extra})


def _auth(token: str, key: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **({"Idempotency-Key": key} if key else {})}


async def test_remote_app_full_cycle(company: dict[str, Any]) -> None:
    admin, owner, sale = company["admin"], company["giamdoc"], company["a"]
    # 1. Cài: phiếu chờ chủ tài khoản — chưa có app nào; người xin không tự duyệt
    r = await executor.run(admin, "connect.install", {"name": "App giả", "grants": GRANTS, "events": "lead.assigned, lead.stage_changed",
                                                       "webhook_url": HOOK_URL})
    assert r.status == "pending_approval"
    assert not await owner_val("SELECT count(*) FROM core.remote_apps WHERE tenant_id = %s", (company["tid"],))
    self_ok = await executor.run(edge(admin), "core.decide", {"approval_id": r.approval_id, "approve": True})
    assert self_ok.status == "denied"
    got = await _approve_as_owner(owner, r)
    app_id, client_id = UUID(got["app_id"]), got["client_id"]
    assert "once_client_secret" not in json.dumps(await owner_val("SELECT result FROM core.approvals WHERE id = %s", (UUID(r.approval_id),)))

    # 2. Khoá: chỉ người xin (đã được duyệt) nhận, một lần; không lưu bản rõ
    assert (await executor.run(owner, "connect.claim_secret", {"app_id": str(app_id)})).status == "denied"
    claim = await executor.run(admin, "connect.claim_secret", {"app_id": str(app_id)}, key="claim-1")
    assert claim.status == "ok", claim.error
    secret, hook_secret = claim.output["once_client_secret"], claim.output["once_webhook_secret"]
    assert (await executor.run(admin, "connect.claim_secret", {"app_id": str(app_id)})).status == "denied"   # một lần
    assert secret not in json.dumps(await owner_val("SELECT to_jsonb(a) FROM core.remote_apps a WHERE id = %s", (app_id,)))
    assert secret not in json.dumps(await owner_val("SELECT output FROM core.idempotency WHERE key LIKE %s", ("%claim-1",)))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as h:
        fake = FakeApp(h)
        fake.secret = hook_secret
        webhooks.use_sender(fake.send)
        try:
            # 3. Token
            assert (await _token(h, client_id, "bcs_sai")).status_code == 401
            assert (await _token(h, client_id, secret, scope="ledger:read")).json()["error"] == "invalid_scope"
            t = await _token(h, client_id, secret)
            assert t.status_code == 200, t.text
            fake.token = tok = t.json()["access_token"]
            assert t.json()["company_id"] == str(company["tid"])

            # 4. Nạp lead hai lần (lần gửi lại khác khoá chống lặp, cùng mã ngoài) ⇒ MỘT lead; lệnh ghi thiếu khoá chống lặp ⇒ từ chối
            phone = fresh_phone()
            lead = {"name": "Khách từ quảng cáo", "phone": phone, "provider": "meta", "external_ref": f"lg-{uuid.uuid4().hex[:8]}"}
            assert (await h.post("/v1/commands/leadflow.intake", json=lead, headers=_auth(tok))).status_code == 422
            no_ref = await h.post("/v1/commands/leadflow.intake", json={"name": "X", "phone": fresh_phone()}, headers=_auth(tok, "k0"))
            assert no_ref.status_code == 422 and "mã bên ngoài" in no_ref.json()["error"]
            one = await h.post("/v1/commands/leadflow.intake", json=lead, headers=_auth(tok, "k1"))
            assert one.status_code == 200, one.text
            two = await h.post("/v1/commands/leadflow.intake", json=lead, headers=_auth(tok, "k2"))
            out1, out2 = one.json()["output"], two.json()["output"]
            assert out2["duplicate"] and out2["lead_id"] == out1["lead_id"] and out2["party_id"] == out1["party_id"] and out1["party_id"]
            assert set(out1) == {"lead_id", "party_id", "duplicate", "assigned_to"}   # chỉ mã — không trả lại tên / SĐT
            assert out1["assigned_to"] == str(sale.user_id)
            assert await owner_val("SELECT count(*) FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE c.phone = %s",
                                   (phone,)) == 1
            assert await owner_val("SELECT via FROM core.audit_log WHERE command = 'leadflow.intake' AND tenant_id = %s ORDER BY id DESC LIMIT 1",
                                   (company["tid"],)) == "app"

            # 5. Webhook ký HMAC khi lead được giao — chỉ loại + mã + lúc xảy ra; app gọi ngược đọc được bằng quyền của nó
            assert await webhooks.drain() == 1
            assert [e["type"] for e in fake.events] == ["lead.assigned"]
            ev = fake.events[0]
            assert set(ev) == {"id", "type", "occurred_at", "company_id", "data"} and ev["data"] == {"lead_id": out1["lead_id"]}
            assert fake.reads[0]["status"] == "ok" and fake.reads[0]["output"]["owner_id"] == str(sale.user_id)
            assert fake.reads[0]["output"]["party_id"] == out1["party_id"]

            # chữ ký sai / quá giờ ⇒ app giả từ chối (mẫu kiểm phía nhận)
            body = b'{"x":1}'
            hdr = {"webhook-id": "m1", "webhook-timestamp": "1000", "webhook-signature": webhooks.sign(hook_secret, "m1", 1000, body)}
            assert webhooks.verify(hook_secret, hdr, body, now=1100) and not webhooks.verify(hook_secret, hdr, body, now=2000)
            assert not webhooks.verify("whsec_khac", hdr, body, now=1100)

            # 6. Không vượt vai: thấy lead (crm:all:read) nhưng không đọc / ghi ngoài quyền; không duyệt; không thao tác nội bộ / quản trị / của người
            assert (await h.post("/v1/queries/leadflow.queue", json={}, headers=_auth(tok))).status_code == 403          # leadflow chỉ ghi
            assert (await h.post("/v1/commands/crm.set_stage", json={"lead_id": out1["lead_id"], "stage": "prospect"},
                                 headers=_auth(tok, "k3"))).status_code == 403                                           # crm chỉ xem
            assert (await h.post("/v1/queries/admin.users", json={}, headers=_auth(tok))).status_code == 403
            assert (await h.post("/v1/commands/admin.set_grant", json={"role": "sale", "resource": "crm", "scope": "all", "actions": ["read"]},
                                 headers=_auth(tok, "k4"))).status_code == 403
            assert (await h.post("/v1/commands/core.decide", json={"approval_id": str(uuid.uuid4()), "approve": True},
                                 headers=_auth(tok, "k5"))).status_code == 403
            assert (await h.post("/v1/queries/core.notifications", json={}, headers=_auth(tok))).status_code == 403
            assert (await h.post("/v1/commands/leadflow.route", json={"lead_id": out1["lead_id"]}, headers=_auth(tok, "k6"))).status_code == 404
            assert (await h.get("/v1/manifest", headers=_auth(tok))).status_code == 403                                 # chỉ API lệnh / truy vấn
            ops = {o["name"] for o in (await h.get("/v1/ops", headers=_auth(tok))).json()}
            assert "leadflow.intake" in ops and "crm.lead_status" in ops and not any(o.startswith(("admin.", "connect.", "core.")) for o in ops)
            other = await h.post("/v1/queries/crm.lead_status", json={"lead_id": out1["lead_id"]},
                                 headers={**_auth(tok), "X-Tenant-Id": str(uuid.uuid4())})
            assert other.status_code == 403                                                                               # token gắn MỘT công ty

            # 7. Đổi chặng ⇒ thư chờ gửi; THU HỒI ⇒ token bị từ chối ngay, thư chưa gửi bị huỷ, sự kiện sau không sinh thư
            await ok(sale, "crm.set_stage", lead_id=out1["lead_id"], stage="prospect")
            assert await owner_val("SELECT count(*) FROM core.webhook_deliveries WHERE app_id = %s AND status = 'pending'", (app_id,)) == 1
            rv = await executor.run(admin, "connect.revoke", {"app_id": str(app_id), "reason": "thử"})
            assert rv.status == "ok" and rv.output["webhooks_cancelled"] == 1 and rv.output["tokens_revoked"] == 1
            assert (await h.post("/v1/queries/crm.lead_status", json={"lead_id": out1["lead_id"]}, headers=_auth(tok))).status_code == 401
            assert (await _token(h, client_id, secret)).status_code == 401
            await ok(sale, "crm.set_stage", lead_id=out1["lead_id"], stage="lead")
            assert await owner_val("SELECT count(*) FROM core.webhook_deliveries WHERE app_id = %s AND status = 'pending'", (app_id,)) == 0
            await webhooks.drain()
            assert len(fake.events) == 1
        finally:
            webhooks.use_sender(None)


async def test_app_access_never_exceeds_the_installer(company: dict[str, Any]) -> None:
    admin, owner = company["admin"], company["giamdoc"]
    # vượt quyền người cài (quản trị không có quyền sổ cái) ⇒ phiếu duyệt xong vẫn KHÔNG chạy
    r = await executor.run(admin, "connect.install", {"name": "App tham", "grants": "ledger:all:read"})
    d = await executor.run(edge(owner), "core.decide", {"approval_id": r.approval_id, "approve": True})
    assert d.output["status"] == "failed" and "vượt quyền" in d.output["error"]
    # duyệt / quản lý / quản trị lõi không bao giờ cấp được cho app
    for bad in ("crm:all:approve", "crm:all:manage", "core.admin:all:read"):
        bad_r = await executor.run(admin, "connect.install", {"name": "App x", "grants": bad})
        assert bad_r.status == "pending_approval"
        assert (await executor.run(edge(owner), "core.decide", {"approval_id": bad_r.approval_id, "approve": True})).output["status"] == "failed"
    # nhận sự kiện cần quyền xem cả công ty trên tài nguyên của sự kiện
    narrow = await executor.run(admin, "connect.install", {"name": "App hẹp", "grants": "crm:team:read", "events": "lead.assigned",
                                                           "webhook_url": HOOK_URL})
    assert (await executor.run(edge(owner), "core.decide", {"approval_id": narrow.approval_id, "approve": True})).output["status"] == "failed"
    # địa chỉ webhook nội bộ bị từ chối (chống SSRF)
    assert webhooks.url_allowed("http://10.0.0.5/x") and webhooks.url_allowed("https://127.0.0.1/x") and webhooks.url_allowed("https://localhost/x")
    assert webhooks.url_allowed(HOOK_URL) is None

    # lúc chạy: vai app ∩ quyền hiện tại của người bảo lãnh ∩ phạm vi token
    app_g = {"crm": Grant("all", frozenset({"read", "write"})), "leadflow": Grant("all", frozenset({"write"}))}
    sponsor = {"crm": Grant("team", frozenset({"read", "approve"}))}
    assert remote_apps.effective(app_g, sponsor) == {"crm": Grant("team", frozenset({"read"}))}
    assert remote_apps.effective(app_g, {**sponsor, "leadflow": Grant("all", frozenset({"write"}))}, ["crm:read"]) == \
        {"crm": Grant("team", frozenset({"read"}))}

    # người bảo lãnh bị khoá ⇒ app không gọi được nữa
    got = await _install(company, events="")
    claim = await ok(admin, "connect.claim_secret", app_id=got["app_id"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as h:
        tok = (await _token(h, got["client_id"], claim["once_client_secret"])).json()["access_token"]
        assert (await h.post("/v1/queries/crm.leads", json={}, headers=_auth(tok))).status_code == 200
        async with db.owner_tx() as c:
            await c.execute("UPDATE core.memberships SET active = false WHERE user_id = %s AND tenant_id = %s", (admin.user_id, company["tid"]))
        try:
            r2 = await h.post("/v1/queries/crm.leads", json={}, headers=_auth(tok))
            assert r2.status_code == 403 and "bảo lãnh" in r2.json()["detail"]
        finally:
            async with db.owner_tx() as c:
                await c.execute("UPDATE core.memberships SET active = true WHERE user_id = %s AND tenant_id = %s", (admin.user_id, company["tid"]))


async def test_rotating_the_secret_needs_approval_and_kills_old_tokens(company: dict[str, Any]) -> None:
    admin, owner = company["admin"], company["giamdoc"]
    got = await _install(company, events="")
    first = await ok(admin, "connect.claim_secret", app_id=got["app_id"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as h:
        old = (await _token(h, got["client_id"], first["once_client_secret"])).json()["access_token"]
        r = await executor.run(admin, "connect.rotate_secret", {"app_id": got["app_id"]})
        await _approve_as_owner(owner, r)
        assert (await h.post("/v1/queries/crm.leads", json={}, headers=_auth(old))).status_code == 200   # khoá cũ chạy tới lúc nhận khoá mới
        second = await ok(admin, "connect.claim_secret", app_id=got["app_id"])
        assert second["once_webhook_secret"] != first["once_webhook_secret"]
        assert (await h.post("/v1/queries/crm.leads", json={}, headers=_auth(old))).status_code == 401
        assert (await _token(h, got["client_id"], first["once_client_secret"])).status_code == 401
        assert (await _token(h, got["client_id"], second["once_client_secret"])).status_code == 200
        apps = await executor.query(admin, "connect.apps", {})
        assert apps.status == "ok" and apps.output["rows"][0]["grants"] == "crm:all:read; leadflow:team:write"


async def test_session_and_mcp_tokens_are_not_app_tokens(company: dict[str, Any]) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as h:
        assert (await h.post("/v1/queries/crm.leads", json={}, headers=_auth("bat_khongco"))).status_code == 401
        assert (await h.post("/v1/apps/token", data={"grant_type": "password"})).json()["error"] == "unsupported_grant_type"
        assert (await h.post("/v1/apps/token", data={"grant_type": "client_credentials", "client_id": "bca_x", "client_secret": "y"})).status_code == 401


async def test_connect_fails_closed_without_the_owner_approval_rule(company: dict[str, Any]) -> None:
    """Review 08/11 (CAO): công ty chưa có luật duyệt cho connect.* (chưa chạy seed --upgrade / ai đó đã bỏ) ⇒ cài / đổi quyền / xoay khoá bị từ
    chối, không chạy thẳng."""
    admin = company["admin"]
    got = await _install(company, events="")
    async with db.owner_tx() as c:
        await c.execute("DELETE FROM core.approval_rules WHERE tenant_id = %s AND command LIKE 'connect.%%'", (company["tid"],))
    for cmd, inp in (("connect.install", {"name": "App lén", "grants": "crm:all:read"}),
                     ("connect.set_access", {"app_id": got["app_id"], "grants": "crm:all:read"}),
                     ("connect.rotate_secret", {"app_id": got["app_id"]})):
        r = await executor.run(admin, cmd, inp)
        assert r.status == "denied" and "luật duyệt" in r.error, (cmd, r.status, r.error)
    assert await owner_val("SELECT count(*) FROM core.remote_apps WHERE tenant_id = %s", (company["tid"],)) == 1


async def test_the_approval_card_shows_parsed_access_not_free_text(company: dict[str, Any]) -> None:
    """Review 08/11 (VỪA): tên app dài + "quyền: crm:own:read" giả + 40 dòng trống từng che quyền thật trên phiếu. Tên có ký tự điều khiển / quá dài
    bị từ chối; phiếu ghi quyền đã phân tích ([tài nguyên] phạm vi · hành động), tên sự kiện có thật, chỉ tên máy của webhook."""
    admin = company["admin"]
    probe = "App vô hại · quyền: crm:own:read" + "\n" * 40 + "x"
    assert (await executor.run(admin, "connect.install", {"name": probe, "grants": "leadflow:team:write; deals:all:read"})).status == "invalid"
    bad = await executor.run(admin, "connect.install", {"name": "App · quyền: crm:own:read\n\n\nx", "grants": "leadflow:team:write"})
    assert bad.status == "invalid" and "điều khiển" in bad.error
    assert (await executor.run(admin, "connect.install", {"name": "A" * 61, "grants": "crm:all:read"})).status == "invalid"
    assert (await executor.run(admin, "connect.install", {"name": "App‮gia", "grants": "crm:all:read"})).status == "invalid"
    r = await executor.run(admin, "connect.install", {"name": "App thật", "grants": "leadflow:team:write;\n\n deals:all:read",
                                                       "events": "lead.assigned, x.y", "webhook_url": "https://hooks.app-gia.test/a?token=bi-mat"})
    summary = await owner_val("SELECT summary FROM core.approvals WHERE id = %s", (UUID(r.approval_id),))
    assert "[deals] all · read | [leadflow] team · write" in summary and "\n" not in summary
    assert "sự kiện: lead.assigned, [lạ]" in summary and "hooks.app-gia.test" in summary and "bi-mat" not in summary


async def test_bad_secrets_from_elsewhere_do_not_lock_the_real_app_out_and_are_audited(company: dict[str, Any]) -> None:
    """Review 08/11 (VỪA): 30 lần sai khoá theo client_id từng khoá luôn app thật. Nay lần sai đếm theo (client_id, địa chỉ gọi); mỗi lần xin vào nhật ký."""
    got = await _install(company, events="")
    claim = await ok(company["admin"], "connect.claim_secret", app_id=got["app_id"])

    def client(ip: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 4000)), base_url="http://test")

    async with client("203.0.113.9") as attacker, client("198.51.100.7") as real:
        for _ in range(30):
            assert (await _token(attacker, got["client_id"], "bcs_doan")).status_code == 401
        assert (await _token(attacker, got["client_id"], claim["once_client_secret"])).status_code == 429   # máy dò bị chặn
        assert (await _token(real, got["client_id"], claim["once_client_secret"])).status_code == 200      # app thật vẫn vào
    rows = await owner_val("""SELECT jsonb_agg(jsonb_build_object('s', status, 'ip', input->>'ip')) FROM core.audit_log
                              WHERE tenant_id = %s AND command = 'apps.token'""", (company["tid"],))
    assert sum(r["s"] == "denied" and r["ip"] == "203.0.113.9" for r in rows) == 30
    assert sum(r["s"] == "ok" and r["ip"] == "198.51.100.7" for r in rows) == 1


async def test_intake_ids_are_bound_to_the_app_and_hidden_when_the_app_cannot_read(company: dict[str, Any]) -> None:
    """Review 08/11 (THẤP): khách đã có của người khác — app không xem được thì không nhận mã lead / người; mã ngoài gắn theo app (app B gửi cùng
    (meta, mã) với app A không lấy được lead của A)."""
    admin, sale = company["admin"], company["a"]
    phone = fresh_phone()
    await ok(sale, "crm.create_lead", name="Khách sẵn có", phone=phone)
    a = await _install(company, grants="leadflow:team:write; crm:all:read", events="")
    b = await _install(company, grants="leadflow:team:write", events="")
    ka = await ok(admin, "connect.claim_secret", app_id=a["app_id"])
    kb = await ok(admin, "connect.claim_secret", app_id=b["app_id"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as h:
        ta = (await _token(h, a["client_id"], ka["once_client_secret"])).json()["access_token"]
        tb = (await _token(h, b["client_id"], kb["once_client_secret"])).json()["access_token"]
        ref = f"lg-{uuid.uuid4().hex[:8]}"
        new = {"name": "Khách mới A", "phone": fresh_phone(), "provider": "meta", "external_ref": ref}
        out_a = (await h.post("/v1/commands/leadflow.intake", json=new, headers=_auth(ta, "a1"))).json()["output"]
        assert out_a["lead_id"] and not out_a["duplicate"]
        # B gửi CÙNG (meta, mã) nhưng liên hệ khác: không thấy mapping của A ⇒ tạo lead riêng, và B không xem được khách ⇒ không nhận mã
        out_b = (await h.post("/v1/commands/leadflow.intake", json={**new, "phone": fresh_phone(), "name": "Khách B"},
                              headers=_auth(tb, "b1"))).json()["output"]
        assert out_b["lead_id"] is None and out_b["party_id"] is None and out_b["accepted"]
        # B gửi liên hệ của khách SẴN CÓ (của sale) ⇒ trung tính, không lộ lead / người của khách đó
        old = (await h.post("/v1/commands/leadflow.intake", json={"name": "X", "phone": phone, "provider": "meta", "external_ref": "lg-old"},
                            headers=_auth(tb, "b2"))).json()["output"]
        assert old == {"lead_id": None, "party_id": None, "duplicate": False, "assigned_to": None, "accepted": True}   # không lộ "SĐT đã có khách"
        # A (xem được khách) gửi liên hệ của khách sẵn có với mã mới ⇒ duplicate=False (chỉ True khi CHÍNH app đã gửi mã đó)
        seen = (await h.post("/v1/commands/leadflow.intake", json={"name": "X", "phone": phone, "provider": "meta", "external_ref": "lg-old"},
                             headers=_auth(ta, "a3"))).json()["output"]
        assert seen["duplicate"] is False and seen["lead_id"]
        # A gửi lại đúng mã của mình ⇒ lead của A
        again = (await h.post("/v1/commands/leadflow.intake", json=new, headers=_auth(ta, "a2"))).json()["output"]
        assert again["duplicate"] and again["lead_id"] == out_a["lead_id"]
    assert await owner_val("SELECT count(DISTINCT provider) FROM leadflow.external_refs WHERE tenant_id = %s AND ref = %s",
                           (company["tid"], ref)) == 2


def test_ssrf_guard_treats_embedded_and_special_ranges_as_private() -> None:
    """Review 08/11 (THẤP): NAT64, ánh xạ ::ffff:, IPv4-compatible, 6to4, Teredo, CGNAT, link-local… đều không công khai."""
    for ip in ("127.0.0.1", "10.1.2.3", "100.64.0.1", "169.254.169.254", "0.0.0.0", "192.168.1.1", "198.18.0.1", "224.0.0.1",
               "::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::127.0.0.1", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe", "2002:7f00:1::1",
               "2001:0:4136:e378:8000:63bf:3fff:fdd2", "fe80::1", "fd00::1", "2001:db8::1", "ff02::1", "fe80::1%en0"):
        assert not webhooks.is_public(ip), ip
    for ip in ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946", "::ffff:93.184.216.34"):
        assert webhooks.is_public(ip), ip
    for url in ("https://[::ffff:127.0.0.1]/h", "https://[64:ff9b::7f00:1]/h", "https://100.64.0.1/h", "https://user:pw@app-gia.test/h",
                "https://db.internal/h", "http://app-gia.test/h"):
        assert webhooks.url_allowed(url), url
    assert webhooks.pinned_url("https://app-gia.test:8443/h?x=1", "93.184.216.34") == "https://93.184.216.34:8443/h?x=1"
    assert webhooks.pinned_url("https://app-gia.test/h", "2606:2800::1") == "https://[2606:2800::1]/h"


async def test_dns_rebinding_to_a_private_address_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolver(host: str, port: int, **_: Any) -> list[Any]:
        return [(0, 0, 0, "", ("93.184.216.34", port)), (0, 0, 0, "", ("64:ff9b::a9fe:a9fe", port, 0, 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    monkeypatch.setattr(settings, "webhook_allow_private", False)
    with pytest.raises(PermissionError):
        await webhooks.http_send("https://rebind.app-gia.test/h", b"{}", {})


async def test_a_slow_endpoint_does_not_stall_others_and_late_results_do_not_overwrite_a_new_lease(
        company: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 08/11 (THẤP): gửi song song có trần mỗi máy nhận + hết giờ ngắn; kết quả chỉ ghi khi còn đúng lượt thuê."""
    admin, owner = company["admin"], company["giamdoc"]
    ids = {}
    for host in ("slow", "fast", "relet"):
        r = await executor.run(admin, "connect.install", {"name": f"App {host}", "grants": GRANTS, "events": "lead.assigned",
                                                           "webhook_url": f"https://{host}.app-gia.test/h"})
        got = await _approve_as_owner(owner, r)
        await ok(admin, "connect.claim_secret", app_id=got["app_id"])
        ids[host] = UUID(got["app_id"])
    monkeypatch.setattr(webhooks, "TIMEOUT_S", 0.3)

    async def sender(url: str, body: bytes, headers: dict[str, str]) -> int:
        if url.startswith("https://slow."):
            await asyncio.sleep(30)
        if url.startswith("https://relet."):   # bản khác đã thuê lại thư này trong lúc mình gửi
            async with db.owner_tx() as c:
                await c.execute("UPDATE core.webhook_deliveries SET next_at = now() + interval '1 hour' WHERE id = %s",
                                (UUID(headers["webhook-id"]),))
        return 200

    await ok(admin, "leadflow.intake", name="Khách song song", phone=fresh_phone())
    webhooks.use_sender(sender)
    try:
        t0 = asyncio.get_running_loop().time()
        await webhooks.dispatch_once()
        assert asyncio.get_running_loop().time() - t0 < 10
    finally:
        webhooks.use_sender(None)
    st = {h: await owner_val("SELECT jsonb_build_object('s', status, 'a', attempts, 'e', last_error) FROM core.webhook_deliveries WHERE app_id = %s",
                             (i,)) for h, i in ids.items()}
    assert st["fast"]["s"] == "done"
    assert st["slow"] == {"s": "pending", "a": 1, "e": "quá giờ"}
    assert st["relet"] == {"s": "pending", "a": 0, "e": ""}


async def test_apps_cannot_run_what_the_assistant_is_denied_unless_the_op_opts_in(company: dict[str, Any]) -> None:
    """Review 08/11 (THẤP): app không chạy thao tác trợ lý bị cấm (vd tạm ngừng nhận lead) — chỉ lệnh khai ``apps=True`` (nạp lead). Bảng ghi loại
    người làm nhận 'app'."""
    got = await _install(company, events="")
    claim = await ok(company["admin"], "connect.claim_secret", app_id=got["app_id"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as h:
        tok = (await _token(h, got["client_id"], claim["once_client_secret"])).json()["access_token"]
        ops = {o["name"] for o in (await h.get("/v1/ops", headers=_auth(tok))).json()}
        assert "leadflow.intake" in ops and "leadflow.pause" not in ops
        r = await h.post("/v1/commands/leadflow.pause", json={"user_email": company["a"].email}, headers=_auth(tok, "p1"))
        assert r.status_code == 403
    defs = await owner_val("""SELECT string_agg(pg_get_constraintdef(oid), ' ') FROM pg_constraint
                              WHERE conrelid IN ('memory.items'::regclass, 'apps.items'::regclass, 'apps.versions'::regclass) AND conname LIKE '%%via%%'""")
    assert defs.count("'app'") == 3
