"""MCP chỉ đọc qua OAuth (0093) — trọn vòng như Claude / ChatGPT nối vào: dò quyền ở GỐC, đăng ký client, /authorize ⇒ trang đồng ý trên web,
người dùng đồng ý bằng phiên + công ty đang chọn, đổi mã (PKCE) lấy token, gọi công cụ — chỉ thấy trong quyền người đó, không có đường ghi; token
làm mới xoay vòng; mã / token dùng lại bị từ chối."""

import base64
import hashlib
import json
import secrets
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from barista import mcp_server
from barista.config import settings
from barista.http_api import app
from tests.conftest import Who, fresh_phone, ok, owner_val

REDIRECT = "http://localhost:9999/callback"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


async def _rpc(c: httpx.AsyncClient, token: str, method: str, params: dict[str, Any], rid: int) -> httpx.Response:
    return await c.post("/mcp", headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                                         "MCP-Protocol-Version": "2025-06-18"},
                        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params})


async def _call(c: httpx.AsyncClient, token: str, tool: str, args: dict[str, Any]) -> str:
    r = await _rpc(c, token, "tools/call", {"name": tool, "arguments": args}, 2)
    assert r.status_code == 200, r.text
    return r.json()["result"]["content"][0]["text"]


async def test_ai_app_connects_with_oauth_and_reads_only_within_the_users_rights(who: Who, monkeypatch: Any) -> None:
    sale1, sale2 = await who("sale1"), await who("sale2")
    mine, theirs = f"Khách MCP của An {uuid.uuid4().hex[:4]}", f"Khách MCP của Bích {uuid.uuid4().hex[:4]}"
    await ok(sale1, "crm.create_lead", name=mine, phone=fresh_phone(), offering_code="HSR")
    await ok(sale2, "crm.create_lead", name=theirs, phone=fresh_phone(), offering_code="HSR")
    base = settings.public_api_url.rstrip("/")
    async with mcp_server.server.session_manager.run(), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as c:
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()                # dò quyền ở GỐC (RFC 8414)
        assert meta["authorization_endpoint"] == f"{base}/authorize" and "S256" in meta["code_challenge_methods_supported"]
        prm = (await c.get("/.well-known/oauth-protected-resource/mcp")).json()               # RFC 9728
        assert prm["resource"] == f"{base}/mcp" and prm["scopes_supported"] == ["read"]
        assert (await c.post("/mcp", json={})).status_code == 401                            # chưa có token ⇒ 401
        assert (await c.get("/v1/manifest")).status_code == 401                              # đường /v1 vẫn của API, không bị MCP nuốt

        reg = (await c.post("/register", json={"client_name": "Claude (test)", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                                                "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})).json()
        verifier, challenge = _pkce()
        r = await c.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT, "state": "s1",
                                              "code_challenge": challenge, "code_challenge_method": "S256", "scope": "read"})
        assert r.status_code == 302 and r.headers["location"].startswith(f"{settings.web_url.rstrip('/')}/connect/ai?req=")
        req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]

        dev = {"X-Dev-User": sale1.email, "X-Tenant-Id": str(sale1.tenant_id)}               # trang đồng ý: phiên + công ty đang chọn
        info = (await c.get(f"/v1/mcp/requests/{req}", headers=dev)).json()
        assert info["client_name"] == "Claude (test)" and info["scopes"] == ["read"] and info["user"] == sale1.name
        assert info["redirect_host"] == "localhost"                                           # trang đồng ý hiện tên miền quay về
        back = (await c.post("/v1/mcp/approve", headers=dev, json={"request_id": req, "approve": True})).json()["redirect"]
        q = parse_qs(urlparse(back).query)
        assert back.startswith(REDIRECT) and q["state"] == ["s1"]
        assert (await c.post("/v1/mcp/approve", headers=dev, json={"request_id": req, "approve": True})).status_code == 410   # yêu cầu dùng một lần

        form = {"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT, "client_id": reg["client_id"],
                "code_verifier": verifier}
        tok = (await c.post("/token", data=form)).json()
        assert tok["token_type"].lower() == "bearer" and tok["scope"] == "read"
        assert (await c.post("/token", data=form)).status_code == 400                       # mã dùng lại ⇒ từ chối

        init = await _rpc(c, tok["access_token"], "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                                 "clientInfo": {"name": "test", "version": "1"}}, 1)
        assert init.status_code == 200, init.text
        tools = {t["name"] for t in (await _rpc(c, tok["access_token"], "tools/list", {}, 3)).json()["result"]["tools"]}
        assert tools == {"find", "list_reports", "run_report"}
        found = await _call(c, tok["access_token"], "find", {"q": "Khách MCP của"})
        assert mine in found and theirs not in found                                         # chỉ khách của chính người đã đồng ý
        reports = json.loads(await _call(c, tok["access_token"], "list_reports", {}))
        assert "crm.leads" in {x["name"] for x in reports} and not {x["name"] for x in reports} & {"crm.create_lead", "core.decide"}
        assert "Không có báo cáo" in await _call(c, tok["access_token"], "run_report", {"name": "crm.create_lead",
                                                                                         "input": {"name": "x", "phone": "0100000000"}})

        new = (await c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                            "client_id": reg["client_id"]})).json()
        assert new["access_token"] != tok["access_token"]
        assert (await c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                             "client_id": reg["client_id"]})).status_code == 400   # token làm mới xoay vòng

        # máy chủ MCP chỉ khởi động MỘT lần mỗi tiến trình (session_manager.run) ⇒ các kịch bản soạn đề xuất (0228) chạy trong cùng phiên này
        await _drafts_a_proposal_and_nothing_is_written_until_the_person_confirms(c, sale1, sale2)
        await _read_only_connection_cannot_draft_and_cannot_upgrade_itself(c, sale1, monkeypatch)


async def test_redirect_allowlist_and_rate_limit(monkeypatch: Any) -> None:
    """Bản thật: DCR mở nhưng mã chỉ quay về tên miền đã cho phép; mỗi người × ứng dụng có trần tần suất."""
    from barista.kernel import oauth, ratelimit

    monkeypatch.setattr(settings, "mcp_redirect_hosts", "claude.ai, chatgpt.com")
    assert oauth.redirect_allowed("https://claude.ai/api/mcp/auth_callback")
    assert oauth.redirect_allowed("https://www.chatgpt.com/cb")
    assert not oauth.redirect_allowed("https://claude.ai.evil.example/cb") and not oauth.redirect_allowed(REDIRECT)
    base = settings.public_api_url.rstrip("/")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as c:
        reg = (await c.post("/register", json={"client_name": "Lạ", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                                                "grant_types": ["authorization_code"], "response_types": ["code"]})).json()
        _, challenge = _pkce()
        r = await c.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT, "state": "s",
                                              "code_challenge": challenge, "code_challenge_method": "S256"})
        assert r.status_code == 302 and "error=unauthorized_client" in r.headers["location"]       # không tới trang đồng ý

    ratelimit.reset()
    limit, _ = ratelimit.LIMITS["mcp"]
    assert all(ratelimit.allow("u:t:c", "mcp", now=0) for _ in range(limit)) and not ratelimit.allow("u:t:c", "mcp", now=1)
    assert ratelimit.allow("u:t:other", "mcp", now=1)                                          # ứng dụng khác đếm riêng
    ratelimit.reset()


async def _connect(c: httpx.AsyncClient, person: Any, scope: str, name: str = "Claude (test)") -> tuple[dict[str, Any], dict[str, Any], str]:
    """Đăng ký → /authorize → đồng ý bằng phiên của ``person`` → đổi mã: (token, thông tin trang đồng ý, client_id)."""
    reg = (await c.post("/register", json={"client_name": name, "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                                            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                                            "scope": scope})).json()
    verifier, challenge = _pkce()
    r = await c.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT, "state": "s",
                                          "code_challenge": challenge, "code_challenge_method": "S256", "scope": scope})
    assert r.status_code == 302 and "/connect/ai?req=" in r.headers["location"], r.headers["location"]
    req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
    dev = {"X-Dev-User": person.email, "X-Tenant-Id": str(person.tenant_id)}
    info = (await c.get(f"/v1/mcp/requests/{req}", headers=dev)).json()
    back = (await c.post("/v1/mcp/approve", headers=dev, json={"request_id": req, "approve": True})).json()["redirect"]
    tok = (await c.post("/token", data={"grant_type": "authorization_code", "code": parse_qs(urlparse(back).query)["code"][0],
                                        "redirect_uri": REDIRECT, "client_id": reg["client_id"], "code_verifier": verifier})).json()
    return tok, info, reg["client_id"]


async def _drafts_a_proposal_and_nothing_is_written_until_the_person_confirms(c: httpx.AsyncClient, sale1: Any, sale2: Any) -> None:
    """0228: phạm vi ``propose`` — ứng dụng AI ngoài soạn thẻ đề xuất (chạy thử rồi bỏ); lệnh chỉ chạy khi CHÍNH người đó bấm Xác nhận trong Barista."""
    name = f"Khách soạn từ ngoài {uuid.uuid4().hex[:6]}"
    dev = {"X-Dev-User": sale1.email, "X-Tenant-Id": str(sale1.tenant_id)}
    if True:
        tok, info, _ = await _connect(c, sale1, "read propose")
        assert set(info["scopes"]) == {"read", "propose"} and "soạn đề xuất" in info["action"] and "Xác nhận" in info["points"][0]
        assert set(tok["scope"].split()) == {"read", "propose"}
        t = tok["access_token"]
        tools = {x["name"] for x in (await _rpc(c, t, "tools/list", {}, 3)).json()["result"]["tools"]}
        assert tools == {"find", "list_reports", "run_report", "list_actions", "propose_action", "proposal_status"}
        actions = {x["name"] for x in json.loads(await _call(c, t, "list_actions", {}))}
        assert "crm.create_lead" in actions and "core.decide" not in actions and "crm.leads" not in actions   # việc duyệt không soạn được; truy vấn ở list_reports

        out = json.loads(await _call(c, t, "propose_action", {"name": "crm.create_lead", "note": "khách vừa nhắn qua email",
                                                               "input": {"name": name, "phone": fresh_phone(), "offering_code": "HSR"}}))
        assert out["status"] == "proposed" and out["id"], out
        assert "Không thấy" in await _call(c, t, "find", {"q": name})                             # CHƯA ghi gì
        assert json.loads(await _call(c, t, "proposal_status", {"id": out["id"]}))["status"] == "open"

        cards = (await c.get("/v1/assistant/morning", headers=dev)).json()                        # thẻ nằm cùng chỗ đề xuất buổi sáng
        card = next(x for x in cards if x["id"] == out["id"])
        assert card["command"] == "crm.create_lead" and "Claude (test)" in card["reason"] and "khách vừa nhắn" in card["reason"]
        other = {"X-Dev-User": sale2.email, "X-Tenant-Id": str(sale2.tenant_id)}
        r = await c.post(f"/v1/assistant/morning/{out['id']}/decide", headers=other, json={"decision": "confirm", "key": uuid.uuid4().hex})
        assert r.json()["status"] == "not_found"                                                 # người khác không bấm hộ được
        r = await c.post(f"/v1/assistant/morning/{out['id']}/decide", headers=dev, json={"decision": "confirm", "key": uuid.uuid4().hex})
        assert r.json()["status"] == "ok", r.text
        assert "Không thấy" not in await _call(c, t, "find", {"q": name})                         # người bấm ⇒ mới có
        assert json.loads(await _call(c, t, "proposal_status", {"id": out["id"]}))["status"] == "done"
        trail = await owner_val("""SELECT jsonb_agg(jsonb_build_array(via, status) ORDER BY id) FROM core.audit_log WHERE command = 'crm.create_lead'
                                   AND trace = (SELECT 'morning:' || user_id || ':' || day || ':' || key FROM core.proposals WHERE id = %s)""",
                                (out["id"],))
        assert trail == [["agent", "proposed"], ["human", "ok"]]                                 # vết: app soạn → người xác nhận

        bad = json.loads(await _call(c, t, "propose_action", {"name": "crm.create_lead", "input": {"name": ""}}))
        assert bad["status"] == "invalid" and "id" not in bad                                    # sai tham số báo ngay, không để lại thẻ hỏng
        for forbidden in ("core.decide", "crm.leads", "khong.co", "admin.set_grant", "admin.invite_user", "admin.save_field"):   # kể cả lệnh quyền / thiết lập gọi thẳng tên                               # duyệt / truy vấn / lệnh không có ⇒ không thành thẻ
            assert json.loads(await _call(c, t, "propose_action", {"name": forbidden, "input": {}}))["status"] in ("denied", "not_found", "invalid")
        assert "Không có đề xuất" in await _call(c, t, "proposal_status", {"id": str(uuid.uuid4())})
        other_app, _, _ = await _connect(c, sale1, "read propose", name="App khác (test)")       # app khác của CÙNG người không đọc được thẻ của app này
        assert "Không có đề xuất" in await _call(c, other_app["access_token"], "proposal_status", {"id": out["id"]})
        assert "localhost" in card["reason"]                                                    # thẻ nêu tên miền quay về, không chỉ tên tự khai


async def _read_only_connection_cannot_draft_and_cannot_upgrade_itself(c: httpx.AsyncClient, sale1: Any, monkeypatch: Any) -> None:
    from barista.agent import morning

    if True:
        tok, info, cid = await _connect(c, sale1, "read", name="Chỉ đọc (test)")
        assert info["scopes"] == ["read"] and info["action"] == "đọc dữ liệu"
        t = tok["access_token"]
        tools = {x["name"] for x in (await _rpc(c, t, "tools/list", {}, 3)).json()["result"]["tools"]}
        assert tools == {"find", "list_reports", "run_report"}                                    # không thấy công cụ soạn
        r = await _rpc(c, t, "tools/call", {"name": "propose_action", "arguments": {"name": "crm.create_lead", "input": {}}}, 4)
        assert '"isError":true' in r.text                                                       # token chỉ đọc gọi thẳng vẫn bị từ chối
        up = await c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid,
                                          "scope": "read propose"})
        assert up.status_code == 400 or "propose" not in up.json().get("scope", "")              # làm mới không tự thêm phạm vi

        _, challenge = _pkce()                                                                    # "propose" phải đi cùng "read"; phạm vi lạ bị từ chối
        for scope in ("propose", "read write"):
            r = await c.get("/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "state": "s",
                                                  "code_challenge": challenge, "code_challenge_method": "S256", "scope": scope})
            assert "error=invalid_scope" in r.headers.get("location", "") or r.status_code == 400, (scope, r.status_code, r.headers.get("location"))

        tok2, _, _ = await _connect(c, sale1, "read propose", name="Soạn nhiều (test)")          # trần thẻ mỗi ngày từ ứng dụng ngoài
        monkeypatch.setattr(morning, "OUTSIDE_PER_DAY", 0)
        out = json.loads(await _call(c, tok2["access_token"], "propose_action", {"name": "crm.create_lead", "input": {"name": "x"}}))
        assert out["status"] == "limit"
