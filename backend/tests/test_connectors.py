"""KHUNG KẾT NỐI CHUNG (ADR 0244): một gói kết nối GIẢ khai trong test (bộ nối + lệnh nhận + lệnh tạo kết nối, chỉ dùng ``barista.api``) trên công ty KHÔNG
bật gói ngành thật ⇒ chứng minh lõi tự lo kết nối / khoá mã hoá / lịch kéo có con trỏ / thử lại từng mục / webhook / gói tắt / quyền sở hữu."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import httpx
import pytest

from barista import seed
from barista.api import Ctx, Input, Invalid, Module, Pack, connectors, field
from barista.config import settings
from barista.db import db
from barista.kernel import connectors as kc
from barista.kernel import executor, packs, registry, screens
from barista.kernel.principal import system_principal
from tests.conftest import owner_val


async def _exec(sql: Any, params: Any = None) -> None:
    async with db.owner.connection() as conn:
        await conn.execute(sql, params)

KEY = "conn_demo"
LIVE: tuple[Any, ...] = (registry.PACKS, registry.MODULES, registry.MODULE_PACK, registry.RESOURCES, registry.RESOURCE_MODULE, registry.OPS,
                         registry.SETUP_RESOURCES, registry.PACK_MODULE, registry.ROUTES, screens.WORKSPACES, screens.CONTRIB, kc.KINDS)
PEOPLE = [("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True)]
TOKEN = "tok-" + "x" * 30
HOOK_KEY = "hook-" + "y" * 30
SERVER: dict[str, Any] = {"pages": {}, "status": 200, "calls": 0}


class Thing(connectors.Connector):
    key = "thing_api"
    label = "Hệ thử"
    secrets = (connectors.Secret("token", "Khoá API"), connectors.Secret("webhook", "Khoá webhook", required=False))
    every_min = 5
    webhook = True

    async def check(self) -> str:
        async with connectors.client("https://api.thing.test", {"authorization": f"Bearer {self.secret['token']}"}) as c:
            r = await c.get("/me")
        if r.status_code == 401:
            raise connectors.ConnectorError("auth", "Khoá API sai")
        return "2 mục"

    async def pull(self, cursor: str) -> connectors.Batch:
        async with connectors.client("https://api.thing.test", {"authorization": f"Bearer {self.secret['token']}"}) as c:
            r = await c.get("/things", params={"after": cursor})
        if r.status_code == 401:
            raise connectors.ConnectorError("auth", "Khoá API sai")
        if r.status_code >= 500:
            raise connectors.ConnectorError("transient", "Hệ thử lỗi")
        body = r.json()
        return connectors.Batch(items=[{"ref": t["id"], "name": t["name"]} for t in body["things"]], cursor=body["next"], more=bool(body.get("more")))

    def hook_parse(self, payload: Any) -> connectors.Hook | None:
        if not isinstance(payload, dict) or not payload.get("event"):
            return None
        return connectors.Hook(event_id=payload["event"], items=[{"ref": t["id"], "name": t["name"]} for t in payload.get("things", [])],
                              wake=bool(payload.get("wake")))


def _api(request: httpx.Request) -> httpx.Response:
    SERVER["calls"] += 1
    if request.headers.get("authorization") != f"Bearer {TOKEN}":
        return httpx.Response(401, json={"error": "bad token"})
    if SERVER["status"] != 200:
        return httpx.Response(SERVER["status"])
    if request.url.path == "/me":
        return httpx.Response(200, json={"ok": True})
    after = request.url.params.get("after", "")
    page = SERVER["pages"].get(after, {"things": [], "next": after})
    return httpx.Response(200, json=page)


@pytest.fixture
def fake_pack() -> Iterator[None]:
    snap = [type(s)(s) for s in LIVE]
    Pack(KEY, "Kết nối thử", "Gói kết nối giả (test khung 0244)", kind="capability",
         grants=(("admin", KEY, "all", "read write manage"),))
    m = Module(KEY, "Kết nối thử", pack=KEY, resources={KEY: "Dữ liệu kết nối thử"})
    m.consumes("connector")

    @m.connector(Thing)
    async def receive(ctx: Ctx, inp: connectors.Delivery) -> dict[str, Any]:
        if inp.item.get("name") == "HỎNG":
            raise Invalid("Mục thử hỏng")
        if inp.item.get("name") == "LẠ":
            raise KeyError("so_dien_thoai_0900")   # lỗi lạ của gói: câu có thể mang dữ liệu khách — không được lên màn
        got = await ctx.val("""INSERT INTO conn_demo_test.things (tenant_id, connection_id, ref, name) VALUES (core.tenant(), %s, %s, %s)
                               ON CONFLICT DO NOTHING RETURNING true""", (inp.connection_id, inp.item["ref"], inp.item["name"]))
        return {"recorded": bool(got)}

    class AddIn(Input):
        label: str = field("Tên", "Hệ thử")
        token: str = field("Khoá API", "", audit=False)

    @m.command("add", resource=KEY, action="manage", title="Thêm kết nối hệ thử")
    async def add(ctx: Ctx, inp: AddIn) -> dict[str, Any]:
        return await connectors.save(ctx, Thing, label=inp.label, settings={"region": "vn"}, secrets={"token": inp.token, "webhook": HOOK_KEY})

    registry.validate()
    kc.use_transport(httpx.MockTransport(_api))
    SERVER.update(pages={}, status=200, calls=0)
    yield
    kc.use_transport(None)
    for live, old in zip(LIVE, snap, strict=True):
        live.clear()
        live.extend(old) if isinstance(live, list) else live.update(old)


@pytest.fixture
async def co(fake_pack: None) -> dict[str, Any]:
    await _exec("""CREATE SCHEMA IF NOT EXISTS conn_demo_test;
                       CREATE TABLE IF NOT EXISTS conn_demo_test.things (tenant_id uuid NOT NULL, connection_id uuid NOT NULL, ref text NOT NULL,
                           name text NOT NULL, PRIMARY KEY (tenant_id, ref));
                       GRANT USAGE ON SCHEMA conn_demo_test TO barista_app; GRANT SELECT, INSERT ON conn_demo_test.things TO barista_app""")
    d = f"cn{uuid.uuid4().hex[:8]}.demo"
    tid = await seed.create_tenant(f"Công ty kết nối {d}", d, people=PEOPLE, packs=(KEY,), about="công ty thử (test)")
    admin = await seed.who(tid, "admin", d)
    r = await executor.run(admin, f"{KEY}.add", {"label": "Hệ thử A", "token": TOKEN})
    assert r.status == "ok", r.error
    return {"tid": tid, "d": d, "admin": admin, "cid": UUID(r.output["connection_id"]), "hook": r.output["webhook_url"]}


async def _sync(tid: UUID) -> dict[str, Any]:
    await _exec("UPDATE connectors.connections SET next_try_at = now() WHERE tenant_id = %s AND status = 'active'", (tid,))
    r = await executor.run(system_principal(tid), "connectors.sync", {})
    assert r.status == "ok", r.error
    return r.output


async def _conn(cid: UUID) -> dict[str, Any]:
    return await owner_val("SELECT to_jsonb(c) FROM connectors.connections c WHERE id = %s", (cid,))


async def _things(tid: UUID) -> list[str]:
    return list(await owner_val("SELECT coalesce(array_agg(ref ORDER BY ref), '{}') FROM conn_demo_test.things WHERE tenant_id = %s", (tid,)))


async def test_the_key_is_stored_sealed_and_never_shown(co: dict[str, Any]) -> None:
    raw = await owner_val("SELECT sealed FROM connectors.secrets WHERE connection_id = %s AND name = 'token'", (co["cid"],))
    assert TOKEN.encode() not in bytes(raw)
    page = (await executor.query(co["admin"], "connectors.list", {})).output
    row = next(r for r in page["rows"] if r["id"] == str(co["cid"]))
    assert TOKEN not in json.dumps(page, default=str) and "…" + TOKEN[-4:] in row["keys"]
    assert row["webhook"] == co["hook"] and co["hook"].endswith(f"/v1/hooks/connectors/{co['tid']}/{co['cid']}")
    audit = await owner_val("SELECT string_agg(input::text, ' ') FROM core.audit_log WHERE tenant_id = %s", (co["tid"],))
    assert TOKEN not in (audit or "") and HOOK_KEY not in (audit or "")


async def test_pull_delivers_each_item_advances_the_cursor_and_never_duplicates(co: dict[str, Any]) -> None:
    SERVER["pages"] = {"": {"things": [{"id": "a1", "name": "Một"}, {"id": "a2", "name": "Hai"}], "next": "c1", "more": True},
                       "c1": {"things": [{"id": "a3", "name": "Ba"}], "next": "c2"}}
    out = await _sync(co["tid"])
    assert out["items"] == 2 and (await _conn(co["cid"]))["cursor"] == "c1"
    assert await owner_val("SELECT next_try_at <= now() FROM connectors.connections WHERE id = %s", (co["cid"],))   # còn nữa ⇒ kéo tiếp ngay lượt sau
    await _sync(co["tid"])
    assert await _things(co["tid"]) == ["a1", "a2", "a3"] and (await _conn(co["cid"]))["cursor"] == "c2"
    await _exec("UPDATE connectors.connections SET cursor = '' WHERE id = %s", (co["cid"],))   # kéo lại từ đầu (vd sửa con trỏ) ⇒ không ghi trùng
    await _sync(co["tid"])
    assert await _things(co["tid"]) == ["a1", "a2", "a3"]
    runs = await owner_val("SELECT count(*) FROM connectors.runs WHERE connection_id = %s AND via = 'pull' AND ok", (co["cid"],))
    assert runs == 3


async def test_a_bad_item_waits_for_retry_and_does_not_hold_back_the_batch(co: dict[str, Any]) -> None:
    SERVER["pages"] = {"": {"things": [{"id": "b1", "name": "Tốt"}, {"id": "b2", "name": "HỎNG"}, {"id": "b3", "name": "Tốt nữa"}], "next": "n1"}}
    out = await _sync(co["tid"])
    assert out == {**out, "items": 2, "parked": 1}
    assert await _things(co["tid"]) == ["b1", "b3"] and (await _conn(co["cid"]))["cursor"] == "n1"
    pend = await owner_val("SELECT to_jsonb(p) FROM connectors.pending p WHERE connection_id = %s", (co["cid"],))
    assert pend["state"] == "retry" and pend["item"]["ref"] == "b2" and "hỏng" in pend["error"]
    for _ in range(kc_tries := 5):
        await _exec("UPDATE connectors.pending SET next_try_at = now() WHERE connection_id = %s", (co["cid"],))
        await _sync(co["tid"])
    assert kc_tries and await owner_val("SELECT state FROM connectors.pending WHERE connection_id = %s", (co["cid"],)) == "failed"
    health = await executor.run(system_principal(co["tid"]), "health.overview", {}) if "health.overview" in registry.OPS else None
    assert health is None or health.status in ("ok", "not_found", "denied")


async def test_a_provider_error_keeps_the_cursor_and_backs_off(co: dict[str, Any]) -> None:
    SERVER["pages"] = {"": {"things": [{"id": "e1", "name": "Một"}], "next": "z1"}}
    SERVER["status"] = 503
    await _sync(co["tid"])
    c = await _conn(co["cid"])
    assert c["cursor"] == "" and c["fails"] == 1 and c["error_kind"] == "transient" and await _things(co["tid"]) == []
    SERVER["status"] = 200
    await _exec("UPDATE connectors.secrets SET sealed = %s WHERE connection_id = %s AND name = 'token'",
                    (kc.seal(co["tid"], co["cid"], "token", "sai-" + "z" * 30), co["cid"]))
    await _sync(co["tid"])
    c = await _conn(co["cid"])
    assert c["error_kind"] == "auth" and c["fails"] == 2 and "Khoá API sai" in c["last_error"]
    assert await owner_val("SELECT next_try_at > now() + interval '50 minutes' FROM connectors.connections WHERE id = %s", (co["cid"],))


async def test_a_switched_off_pack_gets_nothing_and_the_cursor_stays(co: dict[str, Any]) -> None:
    SERVER["pages"] = {"": {"things": [{"id": "p1", "name": "Một"}], "next": "q1"}}
    await _exec("DELETE FROM core.tenant_packs WHERE tenant_id = %s AND pack = %s", (co["tid"], KEY))
    packs._cache.clear()
    await _sync(co["tid"])
    c = await _conn(co["cid"])
    assert c["cursor"] == "" and await _things(co["tid"]) == [] and "đang tắt" in c["last_error"]
    assert SERVER["calls"] == 0   # không gọi hệ ngoài khi gói tắt


async def test_check_calls_the_provider_outside_the_transaction_and_logs_the_result(co: dict[str, Any]) -> None:
    r = await executor.run(co["admin"], "connectors.check", {"connection_id": str(co["cid"])})
    assert r.status == "ok" and "2 mục" in r.output["summary"]
    await _exec("UPDATE connectors.secrets SET sealed = %s WHERE connection_id = %s AND name = 'token'",
                    (kc.seal(co["tid"], co["cid"], "token", "sai-" + "z" * 30), co["cid"]))
    r = await executor.run(co["admin"], "connectors.check", {"connection_id": str(co["cid"])})
    assert r.status == "invalid" and "Khoá API sai" in (r.error or "")
    r = await executor.run(co["admin"], "connectors.set_secret", {"connection_id": str(co["cid"]), "name": "token", "value": TOKEN})
    assert r.status == "ok" and TOKEN not in json.dumps(r.output)
    assert (await executor.run(co["admin"], "connectors.check", {"connection_id": str(co["cid"])})).status == "ok"


def _signed(body: bytes) -> dict[str, str]:
    t = int(time.time())
    return {"x-signature": f"t={t},v1=" + hmac.new(HOOK_KEY.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()}


async def _hook(co: dict[str, Any], payload: dict[str, Any], headers: dict[str, str] | None = None) -> executor.Result:
    body = json.dumps(payload).encode()
    return await executor.run(system_principal(co["tid"]), "connectors.receive_hook",
                              {"ref": str(co["cid"]), "headers": headers if headers is not None else _signed(body), "body_b64": base64.b64encode(body).decode()})


async def test_webhook_is_signed_deduplicated_and_delivers_or_wakes(co: dict[str, Any]) -> None:
    r = await _hook(co, {"event": "ev1", "things": [{"id": "h1", "name": "Webhook"}]})
    assert r.status == "ok" and r.output["items"] == 1 and await _things(co["tid"]) == ["h1"]
    assert (await _hook(co, {"event": "ev1", "things": [{"id": "h1", "name": "Webhook"}]})).output["duplicate"] is True
    assert (await _hook(co, {"event": "ev2"}, headers={"x-signature": "t=1,v1=00"})).status == "denied"
    assert (await _hook(co, {"no": "event"})).status == "invalid"
    await _exec("UPDATE connectors.connections SET next_try_at = now() + interval '1 hour' WHERE id = %s", (co["cid"],))
    assert (await _hook(co, {"event": "ev3", "wake": True})).status == "ok"
    assert await owner_val("SELECT next_try_at <= now() FROM connectors.connections WHERE id = %s", (co["cid"],))
    log = await owner_val("SELECT string_agg(input::text, ' ') FROM core.audit_log WHERE tenant_id = %s AND command = 'connectors.receive_hook'", (co["tid"],))
    assert "Webhook" not in (log or "") and HOOK_KEY not in (log or "")   # thân / khoá không vào nhật ký


async def test_record_only_connections_still_deliver(co: dict[str, Any]) -> None:
    assert (await executor.run(co["admin"], "connectors.set_mode", {"connection_id": str(co["cid"]), "mode": "record_only"})).status == "ok"
    SERVER["pages"] = {"": {"things": [{"id": "r1", "name": "Ghi nhận"}], "next": "r"}}
    await _sync(co["tid"])
    assert await _things(co["tid"]) == ["r1"]


async def test_only_the_module_that_declared_the_connector_saves_or_lists_it(co: dict[str, Any]) -> None:
    other = Module("conn_other", "Gói khác", pack=KEY)
    other.consumes("connector")
    p = system_principal(co["tid"])
    from barista.kernel.executor import core_ctx
    async with db.tenant_tx(p) as conn:
        ctx = core_ctx(conn, p, module="conn_other")
        with pytest.raises(executor.Denied):
            await connectors.save(ctx, Thing, label="Chiếm", secrets={"token": TOKEN})
        with pytest.raises(executor.Denied):
            await connectors.connections(ctx, Thing)
        ctx = core_ctx(conn, p, module=KEY)
        mine = await connectors.connections(ctx, Thing)
        assert [str(r["id"]) for r in mine] == [str(co["cid"])] and "sealed" not in mine[0]


async def test_without_an_instance_key_secrets_are_refused_clearly(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "connector_key", "")
    r = await executor.run(co["admin"], f"{KEY}.add", {"label": "Hệ thử B", "token": TOKEN})
    assert r.status == "invalid" and "BARISTA_CONNECTOR_KEY" in (r.error or "")


def test_a_connector_must_declare_a_webhook_key_to_receive_webhooks() -> None:
    class NoKey(connectors.Connector):
        key = "no_key_hook"
        label = "Thiếu khoá"
        webhook = True
    with pytest.raises(ValueError, match="webhook"):
        kc.register(NoKey, "x", "x.connector_no_key_hook")


async def test_an_unexpected_receive_error_parks_the_item_without_its_text_and_the_rest_goes_on(co: dict[str, Any]) -> None:
    SERVER["pages"] = {"": {"things": [{"id": "x1", "name": "Tốt"}, {"id": "x2", "name": "LẠ"}, {"id": "x3", "name": "Tốt nữa"}], "next": "k1"}}
    out = await _sync(co["tid"])
    assert out["items"] == 2 and out["parked"] == 1 and await _things(co["tid"]) == ["x1", "x3"]
    assert (await _conn(co["cid"]))["cursor"] == "k1" and (await _conn(co["cid"]))["fails"] == 0
    err = await owner_val("SELECT error FROM connectors.pending WHERE connection_id = %s", (co["cid"],))
    assert err == "Lệnh nhận lỗi (KeyError)" and "0900" not in err
    await _exec("UPDATE connectors.pending SET state = 'failed', attempts = 5 WHERE connection_id = %s", (co["cid"],))
    assert (await executor.run(co["admin"], "connectors.sync_now", {"connection_id": str(co["cid"])})).status == "ok"
    assert await owner_val("SELECT state || attempts FROM connectors.pending WHERE connection_id = %s", (co["cid"],)) == "retry0"


def test_addresses_typed_by_a_company_cannot_point_inside_the_server() -> None:
    for bad in ("http://cms.example.com", "https://localhost/api", "https://10.0.0.5", "https://169.254.169.254/latest", "https://db.internal",
                "https://[::1]/x", "https://"):
        assert kc.check_url_setting(bad), bad
    assert kc.check_url_setting("https://cms.example.com/wp-json") == ""


async def test_client_for_refuses_a_private_address_before_any_call() -> None:
    kc.use_transport(None)
    try:
        with pytest.raises(kc.ConnectorError):
            await connectors.client_for("https://127.0.0.1:5432")
    finally:
        kc.use_transport(httpx.MockTransport(_api))


def test_sealed_keys_carry_a_version_and_survive_a_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    tid, cid = uuid.uuid4(), uuid.uuid4()
    old = "old-key-" + "o" * 40
    monkeypatch.setattr(settings, "connector_key", old)
    blob = kc.seal(tid, cid, "token", "bi-mat")
    assert blob[:1] == kc.BLOB_V1
    monkeypatch.setattr(settings, "connector_key", "new-key-" + "n" * 40)
    with pytest.raises(kc.SecretsOff):
        kc.unseal(tid, cid, "token", blob)
    monkeypatch.setattr(settings, "connector_key_old", old)
    assert kc.unseal(tid, cid, "token", blob) == "bi-mat"
    with pytest.raises(kc.SecretsOff):
        kc.unseal(tid, uuid.uuid4(), "token", blob)   # chép bản mã sang kết nối khác không giải được (AAD)


async def test_a_pack_can_keep_the_mode_switch_for_itself(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Thing, "mode_command", f"{KEY}.set_intake")
    r = await executor.run(co["admin"], "connectors.set_mode", {"connection_id": str(co["cid"]), "mode": "record_only"})
    assert r.status == "invalid" and f"{KEY}.set_intake" in (r.error or "")


# ── Webhook CHUNG (api 1.66): một địa chỉ cho cả bản cài, khoá của bản cài, định tuyến theo khoá đã xác minh ──

APP_KEY = "app-" + "z" * 30
SHARED_KEY = "shared_demo"


class Shared(connectors.Connector):
    """Bộ nối giả kiểu Meta: một webhook cho cả app (chữ ký HMAC bằng khoá BẢN CÀI), tin nêu mã "trang"; lượt kéo đọc các trang khai trong cài đặt và
    chỉ CHỨNG MINH được trang mà token của kết nối thấy (máy giả: token thấy trang trong SERVER["owns"][token])."""
    key = SHARED_KEY
    label = "Hệ chung thử"
    secrets = (connectors.Secret("token", "Khoá API"),)
    every_min = 60
    shared_hook = True

    @classmethod
    def claims(cls, settings: Any) -> list[str]:
        return [str(p) for p in settings.get("pages", [])]

    @classmethod
    def shared_check(cls, headers: Any, body: bytes) -> str:
        want = "sha256=" + hmac.new(APP_KEY.encode(), body, hashlib.sha256).hexdigest()
        return "" if hmac.compare_digest(headers.get("x-hub-signature-256", ""), want) else "bad_signature"

    @classmethod
    def shared_verify(cls, query: Any) -> str | None:
        return query.get("hub.challenge") if hmac.compare_digest(query.get("hub.verify_token", ""), "verify-" + "v" * 20) else None

    @classmethod
    def route_keys(cls, payload: Any) -> list[str]:
        return [str(e.get("id")) for e in payload.get("entry", []) if isinstance(e, dict)] if isinstance(payload, dict) else []

    async def pull(self, cursor: str) -> connectors.Batch:
        mine = SERVER.setdefault("owns", {}).get(self.secret["token"], set())
        return connectors.Batch(cursor=cursor, claimed=tuple(p for p in self.settings.get("pages", []) if p in mine))


@pytest.fixture
def shared_pack(fake_pack: None) -> Iterator[None]:
    Pack(SHARED_KEY, "Kết nối chung thử", "Gói kết nối giả (webhook chung)", kind="capability", grants=(("admin", SHARED_KEY, "all", "read write manage"),))
    m = Module(SHARED_KEY, "Kết nối chung thử", pack=SHARED_KEY, resources={SHARED_KEY: "Kết nối chung thử"})
    m.consumes("connector")

    @m.connector(Shared)
    async def receive(ctx: Ctx, inp: connectors.Delivery) -> dict[str, Any]:
        return {}

    class AddIn(Input):
        token: str = field("Khoá API", "", audit=False)
        pages: list[str] = field("Trang", [])

    @m.command("add", resource=SHARED_KEY, action="manage", title="Thêm kết nối chung thử")
    async def add(ctx: Ctx, inp: AddIn) -> dict[str, Any]:
        return await connectors.save(ctx, Shared, label="Chung thử", settings={"pages": inp.pages}, secrets={"token": inp.token})

    registry.validate()
    SERVER["owns"] = {}
    yield


async def _shared_company(token: str, pages: list[str]) -> dict[str, Any]:
    d = f"sh{uuid.uuid4().hex[:8]}.demo"
    tid = await seed.create_tenant(f"Công ty chung {d}", d, people=PEOPLE, packs=(SHARED_KEY,), about="công ty thử (test)")
    admin = await seed.who(tid, "admin", d)
    r = await executor.run(admin, f"{SHARED_KEY}.add", {"token": token, "pages": pages})
    assert r.status == "ok", r.error
    return {"tid": tid, "cid": UUID(r.output["connection_id"])}


async def _post_shared(body: dict[str, Any], *, key: str = APP_KEY) -> httpx.Response:
    from barista.http_api import app
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(f"/v1/hooks/shared/{SHARED_KEY}", content=raw, headers={"x-hub-signature-256": sig, "content-type": "application/json"})


async def _due(cid: UUID) -> bool:
    return bool(await owner_val("SELECT next_try_at <= now() FROM connectors.connections WHERE id = %s", (cid,)))


async def test_the_shared_webhook_wakes_only_the_connection_whose_token_proved_the_page(shared_pack: None) -> None:
    SERVER["owns"] = {"tok-a" + "a" * 30: {"P1"}, "tok-b" + "b" * 30: {"P2"}}
    a = await _shared_company("tok-a" + "a" * 30, ["P1"])
    b = await _shared_company("tok-b" + "b" * 30, ["P1", "P2"])          # B gõ cả trang P1 của A
    for c in (a, b):
        await _sync(c["tid"])                                            # lượt kéo: token của từng kết nối chứng minh trang nó thấy
    assert await owner_val("SELECT count(*) FROM connectors.routes WHERE kind = %s AND route_key = 'P1' AND verified_at IS NOT NULL", (SHARED_KEY,)) == 1
    assert await owner_val("SELECT connection_id FROM connectors.routes WHERE route_key = 'P1' AND verified_at IS NOT NULL") == a["cid"]
    await _exec("UPDATE connectors.connections SET next_try_at = now() + interval '1 hour' WHERE id = ANY(%s)", ([a["cid"], b["cid"]],))
    r = await _post_shared({"object": "page", "entry": [{"id": "P1"}]})
    assert r.status_code == 200 and r.json()["woken"] == 1
    assert await _due(a["cid"]) and not await _due(b["cid"])            # chỉ chủ đã xác minh của P1 được đánh thức
    assert (await _post_shared({"object": "page", "entry": [{"id": "P2"}]})).json()["woken"] == 1 and await _due(b["cid"])


async def test_the_shared_webhook_refuses_a_bad_signature_before_touching_anything(shared_pack: None) -> None:
    SERVER["owns"] = {"tok-c" + "c" * 30: {"P3"}}
    c = await _shared_company("tok-c" + "c" * 30, ["P3"])
    await _sync(c["tid"])
    await _exec("UPDATE connectors.connections SET next_try_at = now() + interval '1 hour' WHERE id = %s", (c["cid"],))
    assert (await _post_shared({"entry": [{"id": "P3"}]}, key="sai-" + "k" * 30)).status_code == 403
    assert not await _due(c["cid"])
    assert (await _post_shared({"entry": [{"id": "KHONG-AI"}]})).json() == {"accepted": True, "woken": 0}   # trang lạ ⇒ 200 im lặng


async def test_saving_new_pages_starts_unverified_and_dropping_a_page_drops_its_route(shared_pack: None) -> None:
    SERVER["owns"] = {"tok-d" + "d" * 30: {"P4", "P5"}}
    c = await _shared_company("tok-d" + "d" * 30, ["P4", "P5"])
    assert await owner_val("SELECT count(*) FROM connectors.routes WHERE connection_id = %s AND verified_at IS NULL", (c["cid"],)) == 2
    await _sync(c["tid"])
    assert await owner_val("SELECT count(*) FROM connectors.routes WHERE connection_id = %s AND verified_at IS NOT NULL", (c["cid"],)) == 2
    async with db.tenant_tx(system_principal(c["tid"])) as conn:   # gói sửa kết nối: bỏ trang P5
        from barista.kernel.executor import core_ctx
        await core_ctx(conn, system_principal(c["tid"]), module=SHARED_KEY).call_system(
            "connectors.save", kind=SHARED_KEY, label="Chung thử", settings={"pages": ["P4"]}, connection_id=str(c["cid"]))
    assert await owner_val("SELECT array_agg(route_key ORDER BY route_key) FROM connectors.routes WHERE connection_id = %s", (c["cid"],)) == ["P4"]
    assert await owner_val("SELECT verified_at IS NOT NULL FROM connectors.routes WHERE connection_id = %s AND route_key = 'P4'", (c["cid"],))   # trang giữ lại không mất xác minh


def test_a_shared_hook_connector_must_write_its_checks() -> None:
    class Lazy(connectors.Connector):
        key = "lazy_shared"
        label = "Thiếu kiểm"
        shared_hook = True
    with pytest.raises(ValueError, match="webhook chung"):
        kc.register(Lazy, "x", "x.connector_lazy_shared")



def test_outgoing_request_urls_with_their_query_never_reach_the_log(caplog: pytest.LogCaptureFixture) -> None:
    """httpx ghi URL đầy đủ (kèm ?access_token=…, client_secret=…) ở INFO — bản chạy (barista.main, cũng là lớp edge) chặn ở mức WARNING."""
    import asyncio
    import logging

    import barista.main  # noqa: F401 — cấu hình log của bản chạy

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    kc.use_transport(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    try:
        async def call() -> None:
            async with connectors.client("https://graph.example.test") as c:
                await c.get("/oauth/access_token", params={"client_secret": "BIMAT-APP", "code": "MA-DUNG-MOT-LAN"})
        with caplog.at_level(logging.INFO):
            asyncio.run(call())
    finally:
        kc.use_transport(None)
    assert "BIMAT-APP" not in caplog.text and "MA-DUNG-MOT-LAN" not in caplog.text


async def test_items_of_a_paused_connection_do_not_hold_up_retries_of_others(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Rà 20/11: mục chờ của kết nối TẠM DỪNG từng được chọn rồi bỏ qua trong cửa sổ ``PENDING_PER_SWEEP`` (xếp theo id) ⇒ đủ nhiều thì mục của kết nối đang
    chạy không bao giờ tới lượt, và công ty bị quét mỗi phút mãi."""
    from barista.modules import connectors as mc
    other = await executor.run(co["admin"], f"{KEY}.add", {"label": "Hệ thử B", "token": TOKEN})
    paused, live = co["cid"], UUID(other.output["connection_id"])
    await _exec("UPDATE connectors.connections SET status = 'paused' WHERE id = %s", (paused,))
    await _exec("""INSERT INTO connectors.pending (tenant_id, connection_id, item, via, next_try_at) VALUES
                   (%s, %s, '{"ref": "old", "name": "Chờ"}', 'pull', now() - interval '1 hour'),
                   (%s, %s, '{"ref": "z1", "name": "Đang chạy"}', 'pull', now() - interval '1 minute')""", (co["tid"], paused, co["tid"], live))
    monkeypatch.setattr(mc, "PENDING_PER_SWEEP", 1)
    await _sync(co["tid"])
    assert "z1" in await _things(co["tid"]) and "old" not in await _things(co["tid"])
    assert await owner_val("SELECT state FROM connectors.pending WHERE connection_id = %s", (paused,)) == "retry"
    await _exec("UPDATE connectors.connections SET next_try_at = now() + interval '1 hour' WHERE tenant_id = %s", (co["tid"],))
    due = await owner_val(f"SELECT count(*) FROM ({mc.DUE}) d WHERE tenant_id = %s", (co["tid"],))
    assert due == 0   # chỉ còn mục của kết nối tạm dừng ⇒ không quét công ty này nữa
