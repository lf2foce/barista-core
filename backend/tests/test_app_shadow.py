"""App CHỈ GHI NHẬN (0207): người vận hành bật cho một app từ xa ⇒ lệnh app gửi chạy như NẠP LỊCH SỬ (0185) — dữ liệu vào đủ (lead có trong Barista, luật
chia vẫn ghi người sẽ nhận) nhưng lead nằm đỗ (``imported_at``), không ai bị báo, không tính hạn. Tắt ⇒ lead chạy thật từ request kế tiếp. Canh:
· chỉ cửa nhà vận hành đổi được (bắt buộc người + lý do, không có đường HTTP / vai của khách); app không tự bật / tắt;
· app không chỉ ghi nhận (mặc định) giữ nguyên hành vi cũ."""

import uuid
from typing import Any
from uuid import UUID

import httpx
import pytest

from barista.config import settings
from barista.http_api import app
from barista.kernel import executor, registry
from barista.kernel.principal import system_principal
from tests.conftest import Who, owner_val

OP = {"by": "Người vận hành thử", "why": "chạy song song (test)"}


async def _op(tid: UUID, command: str, **inp: Any) -> executor.Result:
    return await executor.run(system_principal(tid), command, {**OP, **inp})


async def _feed_app(tid: UUID) -> dict[str, Any]:
    role = "feed_" + "".join(chr(97 + int(ch, 16)) for ch in uuid.uuid4().hex[:6])   # mã vai: chữ thường + gạch dưới
    made = await _op(tid, "admin.operator_create_role", key=role, label="Nguồn lead thử")
    assert made.status == "ok", made.error
    for res in ("crm", "leadflow"):
        assert (await _op(tid, "admin.operator_set_grant", role=role, resource=res, scope="all", actions=["read", "write"])).status == "ok"
    out = await _op(tid, "connect.operator_install", name=f"Nguồn {role}", like_role=role, grants="crm:all:read,write; leadflow:all:read,write",
                    events="", webhook_url="")
    assert out.status == "ok", out.error
    return out.output


async def _token(c: httpx.AsyncClient, a: dict[str, Any]) -> dict[str, str]:
    r = await c.post("/v1/apps/token", data={"grant_type": "client_credentials", "client_id": a["client_id"], "client_secret": a["once_client_secret"]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _intake(c: httpx.AsyncClient, h: dict[str, str], ref: str) -> dict[str, Any]:
    phone = "010" + str(uuid.uuid4().int)[:7]
    r = await c.post("/v1/commands/leadflow.intake", headers={**h, "Idempotency-Key": f"t:{ref}"},
                     json={"name": f"Khách {ref}", "phone": phone, "provider": "phenau", "external_ref": ref, "source": "ads"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok", body
    return body["output"]


async def test_a_shadow_app_lands_leads_parked_and_bothers_no_one_until_the_operator_turns_it_off(who: Who,
                                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apps_signing_secret", "test-signing-secret")
    admin = await who("admin")
    tid = admin.tenant_id
    a = await _feed_app(tid)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_api_url.rstrip("/")) as c:
        h = await _token(c, a)
        live = await _intake(c, h, f"truoc-{uuid.uuid4().hex[:6]}")                                      # mặc định: chạy thật như cũ
        assert await owner_val("SELECT imported_at FROM leadflow.lead_state WHERE lead_id = %s", (live["lead_id"],)) is None

        sw = await _op(tid, "connect.operator_shadow", app_id=str(a["app_id"]), on=True)
        assert sw.status == "ok", sw.error
        notes = await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", (tid,))
        parked = await _intake(c, h, f"bong-{uuid.uuid4().hex[:6]}")
        assert parked["lead_id"]                                                                          # lead VÀO Barista
        assert await owner_val("SELECT imported_at FROM leadflow.lead_state WHERE lead_id = %s", (parked["lead_id"],)) is not None   # nằm đỗ
        assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", (tid,)) == notes                      # không báo ai
        assert await owner_val("SELECT count(*) FROM core.events WHERE payload->>'lead_id' = %s AND NOT historical",
                               (str(parked["lead_id"]),)) == 0                                            # không sự kiện thật ⇒ không webhook

        assert (await _op(tid, "connect.operator_shadow", app_id=str(a["app_id"]), on=False)).status == "ok"
        after = await _intake(c, h, f"sau-{uuid.uuid4().hex[:6]}")                                      # tắt ⇒ chạy thật từ request kế tiếp
        assert await owner_val("SELECT imported_at FROM leadflow.lead_state WHERE lead_id = %s", (after["lead_id"],)) is None


async def test_only_the_operator_door_switches_shadow(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "apps_signing_secret", "test-signing-secret")
    admin = await who("admin")
    a = await _feed_app(admin.tenant_id)
    op = registry.get("connect.operator_shadow")
    assert op is not None and op.internal and op.agent == "deny" and op.resource == "core.operator"
    inp = {**OP, "app_id": str(a["app_id"]), "on": True}
    assert (await executor.run(admin, "connect.operator_shadow", inp)).status == "not_found"            # quản trị của khách không có đường
    assert (await _op(admin.tenant_id, "connect.operator_shadow", app_id=str(a["app_id"]), on=True, by="", why="")).status == "invalid"
    assert await owner_val("SELECT shadow FROM core.remote_apps WHERE id = %s", (a["app_id"],)) is False
