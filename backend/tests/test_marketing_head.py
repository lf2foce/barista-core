"""Vai mặc định "Trưởng phòng marketing" (chủ sản phẩm 14/11/2026): đọc HẾT chi phí quảng cáo và lead của cả công ty (như giám đốc), lead chỉ đọc;
trưởng đội vẫn chỉ thấy đội mình, sale chỉ của mình."""

from typing import Any

from barista.kernel import executor
from tests.conftest import Who, owner_val, q


def _ids(page: Any) -> set[str]:
    return {str(r["id"]) for r in page["rows"]}


async def test_marketing_head_reads_every_lead_and_campaign_but_cannot_write_leads(who: Who) -> None:
    head, boss, lead_a, sale = await who("tpmkt"), await who("giamdoc"), await who("truonga"), await who("sale1")
    assert head.scope("mkt") == "all" and head.scope("crm") == "all" and head.scope("leadflow") == "all"
    assert "write" not in head.grants["crm"].actions and "write" in head.grants["mkt"].actions
    every = _ids(await q(boss, "crm.leads"))
    assert every and _ids(await q(head, "crm.leads")) == every                       # cả công ty, như giám đốc
    assert _ids(await q(head, "mkt.campaigns")) == _ids(await q(boss, "mkt.campaigns"))
    team = _ids(await q(lead_a, "crm.leads"))
    assert team and team < every                                                     # trưởng đội: chỉ đội mình (+ đội con)
    assert _ids(await q(sale, "crm.leads")) <= team
    lead_id = next(iter(every))
    r = await executor.run(head, "crm.assign", {"lead_id": lead_id, "owner_email": sale.email})
    assert r.status == "denied", (r.status, r.error)                                 # lead chỉ đọc: giao / chuyển là việc của kinh doanh
    assert (await executor.run(boss, "crm.assign", {"lead_id": lead_id, "owner_email": sale.email})).ok   # cùng lệnh, đúng tham số: giám đốc làm được


async def test_marketing_head_controls_ads_company_wide_with_its_own_assistant_toolset(who: Who) -> None:
    """0209 §đội: marketing điều khiển quảng cáo theo đội (như chiến dịch), trưởng phòng cả công ty; vai có bộ công cụ trợ lý mặc định (0218) gồm sổ điều
    khiển quảng cáo."""
    head, mkt = await who("tpmkt"), await who("mkt")
    assert head.scope("mkt.ad_control") == "all" and mkt.scope("mkt.ad_control") == "unit" and mkt.scope("mkt") == "unit"   # 18/11: marketer thấy chiến dịch cả phòng (unit)
    tools = await owner_val("SELECT tools FROM core.role_tools WHERE tenant_id = %s AND role = 'marketing_head'", (head.tenant_id,))
    assert {"mkt.ad_findings", "mkt.ad_changes", "mkt.ad_conversions", "mkt.set_ad_status", "mkt.set_ad_budget", "mkt.overview"} <= set(tools)
