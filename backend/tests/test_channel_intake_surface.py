"""Bề mặt lõi cho gói KÊNH nhận dữ liệu thẳng từ nhà cung cấp (1.31 — 0216, 0208 §0.3.0: form lead quảng cáo vào ``leadflow.intake`` không qua hệ khác):
· ``Ctx.call_record_only``: cùng quyền người gọi, cả chuỗi ở chế độ CHỈ GHI NHẬN (0185 / 0207) — lead vào đủ nhưng nằm đỗ, không báo ai, sự kiện mang dấu
  ``historical``; ``call`` thường ⇒ lead sống như cũ;
· ``leadflow.external_lead_days`` (họ company_figures): chỉ số đếm theo nhà cung cấp × ngày — luật 4 của 0216 (một công ty một đường nhận lead Facebook);
· ``prepare=`` chỉ cho lệnh nội bộ là vòng quét; cửa GET kiểm webhook chỉ có khi module khai ``Module.inbound(verify=)``.
Đường đầy đủ (webhook Meta thật trên HTTP, vòng quét gọi Meta ngoài giao dịch) kiểm ở gói ``meta_ads`` (repo gói)."""

import uuid
from typing import Any

import httpx
import pytest

from barista.config import settings
from barista.db import db
from barista.http_api import app
from barista.kernel import executor, registry
from barista.kernel.executor import core_ctx
from barista.kernel.principal import system_principal
from tests.conftest import Who, fresh_phone


def _lead(ref: str, provider: str = "") -> dict[str, Any]:
    return {"name": f"Khách {ref}", "phone": fresh_phone(), "source": "ads", **({"provider": provider, "external_ref": ref} if provider else {})}


async def test_record_only_lands_the_lead_parked_and_quiet_while_a_plain_call_stays_live(who: Who) -> None:
    tid = (await who("admin")).tenant_id
    sys_p = system_principal(tid)
    async with db.tenant_tx(sys_p, rollback=True) as conn:
        ctx = core_ctx(conn, sys_p)
        notes = await ctx.val("SELECT count(*) FROM core.notifications")
        parked = await ctx.call_record_only("leadflow.intake", **_lead(f"do-{uuid.uuid4().hex[:6]}", "t-chan"))
        assert parked["lead_id"]
        assert await ctx.val("SELECT imported_at FROM leadflow.lead_state WHERE lead_id = %s", (parked["lead_id"],)) is not None
        assert await ctx.val("SELECT count(*) FROM core.notifications") == notes
        assert await ctx.val("SELECT count(*) FROM core.events WHERE payload->>'lead_id' = %s AND NOT historical", (str(parked["lead_id"]),)) == 0
        assert not executor.HISTORICAL.get()                                     # chế độ chỉ bao đúng lần gọi đó
        live = await ctx.call("leadflow.intake", **_lead(f"song-{uuid.uuid4().hex[:6]}", "t-chan"))
        assert await ctx.val("SELECT imported_at FROM leadflow.lead_state WHERE lead_id = %s", (live["lead_id"],)) is None
        again = await ctx.call_record_only("leadflow.intake", **{**_lead("x"), "provider": "t-chan",
                                                                 "external_ref": (await ctx.val("SELECT ref FROM leadflow.external_refs WHERE lead_id = %s",
                                                                                                (parked["lead_id"],)))})
        assert again["duplicate"] and again["lead_id"] == parked["lead_id"]        # cùng mã ngoài ⇒ không lead thứ hai, cả ở chế độ chỉ ghi nhận


async def test_external_lead_days_counts_deliveries_per_provider_and_day_only(who: Who) -> None:
    tid = (await who("admin")).tenant_id
    prov = f"t-{uuid.uuid4().hex[:8]}"
    for i in range(2):
        r = await executor.run(system_principal(tid), "leadflow.intake", _lead(f"{prov}-{i}", prov))
        assert r.ok, r.error
    got = await executor.query(system_principal(tid), "leadflow.external_lead_days", {"days": 2})
    assert got.ok, got.error
    mine = [x for x in got.output if x["provider"] == prov]
    assert len(mine) == 1 and mine[0]["leads"] == 2 and set(mine[0]) == {"provider", "day", "leads"}   # chỉ số đếm, không mã lead
    assert (await executor.query(system_principal(tid), "leadflow.external_lead_days", {"days": 93})).status == "invalid"
    assert "company_figures" == registry.OPS["leadflow.external_lead_days"].family


def test_prepare_is_for_people_and_sweeps_not_for_other_internal_commands() -> None:
    m = registry.Module.__new__(registry.Module)   # không đăng ký module mới: lỗi ném TRƯỚC khi ghi sổ
    m.key = "leadflow"

    async def prep(prep: Any, inp: Any) -> None:
        return None

    with pytest.raises(ValueError, match="vòng quét"):
        m.command("t_internal_prep", resource="leadflow", title="t", internal=True, prepare=prep)


async def test_the_get_check_exists_only_for_modules_that_declare_one(who: Who) -> None:
    tid = (await who("admin")).tenant_id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_api_url.rstrip("/")) as c:
        r = await c.get(f"/v1/hooks/khong_co/{tid}/x", params={"hub.challenge": "1"})
    assert r.status_code == 404
    assert all(op.startswith(mod + ".") for mod, op in registry.INBOUND_VERIFY.items())
