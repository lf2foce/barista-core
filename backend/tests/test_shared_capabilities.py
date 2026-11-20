"""0124: KPI và Marketing là năng lực dùng chung. KPI có gói riêng, không bắt buộc gói nào, phân hệ riêng; Marketing chỉ cần Khách hàng — chữ và cột
của ngành BĐS (giao dịch ký, phí môi giới, ROAS từ phí) chỉ hiện khi một gói cắm vào ``crm.lead_outcome``."""

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest

from barista import seed
from barista.agent import chat
from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.workspaces import manifest
from tests.conftest import fresh_phone, ok, q
from tests.test_packs_industry import INDUSTRY_WORDS
from tests.test_vocab import _surfaces

PEOPLE = [("admin", "QT", "admin", "QT", True), ("giamdoc", "GĐ", "director", "QT", True), ("sale1", "Tư vấn", "sale", "QT", False),
          ("mkt", "Marketing", "marketer", "MKT", True)]


@pytest.fixture
async def shared() -> tuple[Any, str]:
    """Công ty KHÔNG phải BĐS: chỉ Khách hàng + KPI + Marketing."""
    d = f"k{uuid.uuid4().hex[:6]}.demo"
    return await seed.create_tenant(f"Trung tâm {d}", d, people=PEOPLE, packs=("crm", "kpi", "marketing"), about="trung tâm dịch vụ cho khách doanh nghiệp"), d


def _kpis(page: dict[str, Any]) -> list[str]:
    return [k["label"] for b in page["blocks"] if b["type"] == "kpis" for k in b["items"]]


async def test_a_company_without_real_estate_runs_kpi_and_marketing_with_no_industry_words(shared: tuple[Any, str]) -> None:
    tid, d = shared
    admin, sale, mkt = await seed.who(tid, "admin", d), await seed.who(tid, "sale1", d), await seed.who(tid, "mkt", d)
    spaces = {w["key"]: [s["query"] for s in w["screens"]] for w in manifest(admin, "x")["workspaces"]}
    assert {"kpi", "marketing", "sales"} <= set(spaces) and "kpi.board" in spaces["kpi"] and not [q_ for q_ in spaces["sales"] if q_.startswith("kpi.")]
    # KPI chạy với chỉ số của Khách hàng (không cần giao dịch ký): đặt chỉ tiêu → xem của tôi / đội / xếp hạng không chỉ số mặc định của gói khác
    await ok(admin, "kpi.set_target", metric="crm.metric_calls", user_email=sale.email, target=20)
    assert [r["metric"] for r in (await q(sale, "kpi.mine"))["rows"]] == ["crm.metric_calls"]
    assert len((await q(admin, "kpi.board"))["rows"]) >= 1
    assert (await q(sale, "kpi.ranking"))["title"].startswith("Xếp hạng · ")
    # Marketing: chiến dịch, chi phí, hiệu quả — không có kết quả bán ⇒ không cột / ô giao dịch ký, phí, ROAS
    code = f"K-{uuid.uuid4().hex[:5]}".upper()
    await ok(mkt, "mkt.upsert_campaign", code=code, name="Chiến dịch thử", channel="facebook", budget_vnd="50tr", start_on=(today() - timedelta(days=5)).isoformat())
    await ok(mkt, "mkt.record_spend", campaign_code=code, day=(today() - timedelta(days=1)).isoformat(), amount_vnd="3tr")
    await ok(sale, "crm.create_lead", name="Khách quảng cáo", phone=fresh_phone(), source="ads", campaign_code=code)
    page = await q(mkt, "mkt.overview", period="last_30")
    assert _kpis(page) == ["Đã chi", "Lead vào CRM", "Chi phí / lead"]
    table = next(b["page"] for b in page["blocks"] if b["type"] == "table")
    assert [c["key"] for c in table["columns"]] == ["code", "name", "channel", "spend", "leads", "qualified", "cpl", "cpql"]
    assert (await executor.query(admin, "crm.outcome_info", {})).status == "not_found"            # nội bộ: người thường không gọi được
    for who_ in (admin, mkt):
        # chỉ phần của ba gói này (+ menu và lời dặn trợ lý): ma trận quyền của quản trị liệt kê MỌI tài nguyên của mọi gói (kể cả gói tắt); tổng quan sức khoẻ và công
        # cụ tìm nhanh của lõi có chữ ví dụ riêng — không thuộc đợt này
        mine = ("manifest", "instructions")
        hits = [f"{k}: {m.group(0)}" for k, text in (await _surfaces(who_)).items()
                if k in mine or k.split(" ", 1)[-1].startswith(("kpi.", "mkt.", "crm.", "leadflow."))
                for m in INDUSTRY_WORDS.finditer(text)]
        assert not hits, hits[:6]
    assert "Dự án" not in json.dumps(await q(mkt, "mkt.campaigns"), ensure_ascii=False)          # chưa có gói nào đặt chữ "dự án"
    text = await chat.instructions(admin)
    assert "bất động sản" not in text and "hoa hồng" not in text.lower()


async def test_without_a_pack_default_the_ranking_uses_most_targets_then_declared_order(shared: tuple[Any, str]) -> None:
    tid, d = shared
    admin, sale = await seed.who(tid, "admin", d), await seed.who(tid, "sale1", d)
    readable = [mt for mt in registry.METRICS.values() if sale.can(mt.resource, "read")]
    assert f"· {readable[0].label} ·" in (await q(sale, "kpi.ranking", period="2030-01"))["title"]   # không chỉ tiêu nào ⇒ chỉ số khai đầu tiên, không theo nhãn chữ
    await ok(admin, "kpi.set_target", metric="crm.metric_calls", user_email=sale.email, target=20)
    assert f"· {registry.METRICS['crm.metric_calls'].label} ·" in (await q(sale, "kpi.ranking"))["title"]   # có chỉ tiêu ⇒ chỉ số có nhiều chỉ tiêu nhất
