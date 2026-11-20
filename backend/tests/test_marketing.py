"""Hiệu quả quảng cáo (port có chọn lọc từ hợp đồng số liệu chuẩn Phê Nâu): ROAS từ phí đã ký, so kỳ trước, ẩn tỷ lệ khi dữ liệu chưa tin cậy,
"chưa thấy lead" chỉ là gợi ý kiểm tra (không kết luận cắt), lead nhập muộn tính đúng ngày khách để lại thông tin."""

import uuid
from datetime import timedelta
from typing import Any

from barista import seed
from barista.kernel.clock import today
from tests.conftest import Who, fresh_phone, ok, owner_val, q


def _recs(page: dict[str, Any]) -> list[str]:
    return [i["title"] for b in page["blocks"] if b["type"] == "list" and b["title"] == "Gợi ý" for i in b["items"]]


async def _fresh_sale(who: Who) -> Any:
    """Sale mới tinh: ký giao dịch cho sale mẫu sẽ đổi bậc hoa hồng của họ và làm lệch test khác."""
    admin = await who("admin")
    email = f"sale-mkt-{uuid.uuid4().hex[:6]}@{seed.DOMAIN}"
    await ok(admin, "admin.invite_user", email=email, name="Sale Thử MKT", role="sale")
    return await seed.who(await owner_val("SELECT m.tenant_id FROM core.memberships m JOIN core.users u ON u.id = m.user_id WHERE u.email = %s", (email,)),
                          email.split("@")[0], seed.DOMAIN)


async def _campaign(mkt: Any, tag: str, spend: str) -> str:
    code = f"T-{tag}".upper()  # mã chiến dịch lưu viết hoa
    await ok(mkt, "mkt.upsert_campaign", code=code, name=f"Chiến dịch thử {tag}", channel="facebook", offering_code="HSR", budget_vnd="100tr",
             start_on=(today() - timedelta(days=20)).isoformat())
    await ok(mkt, "mkt.record_spend", campaign_code=code, day=(today() - timedelta(days=2)).isoformat(), amount_vnd=spend)
    return code


async def test_spend_without_leads_is_a_check_not_a_verdict_and_late_leads_use_captured_day(who: Who) -> None:
    mkt, sale3 = await who("mkt"), await _fresh_sale(who)
    tag = uuid.uuid4().hex[:6]
    code = await _campaign(mkt, tag, "7tr")
    await ok(mkt, "mkt.set_coverage", spend_reliable_from=(today() - timedelta(days=90)).isoformat(),
             lead_reliable_from=(today() - timedelta(days=90)).isoformat(), spend_complete_through=today().isoformat())
    page = await q(mkt, "mkt.overview", period="last_30")
    rec = next(t for t in _recs(page) if code not in t and f"Chiến dịch thử {tag}" in t)
    assert rec.startswith("Chi mà chưa thấy lead")  # không nói "lãng phí", không nói "cắt"

    # lead nhập hôm nay nhưng khách để lại thông tin 45 ngày trước ⇒ KHÔNG tính vào kỳ 30 ngày
    await ok(sale3, "crm.create_lead", name=f"Khách nhập muộn {tag}", phone=fresh_phone(), offering_code="HSR", source="ads", campaign_code=code,
             captured_on=(today() - timedelta(days=45)).isoformat())
    page = await q(mkt, "mkt.overview", period="last_30")
    camp = next(r for b in page["blocks"] if b["type"] == "table" for r in b["page"]["rows"] if r["code"] == code)
    assert camp["leads"] == 0
    out = await ok(sale3, "crm.create_lead", name=f"Khách mới {tag}", phone=fresh_phone(), offering_code="HSR", source="ads", campaign_code=code)
    assert out["lead_id"]
    page = await q(mkt, "mkt.overview", period="last_30")
    camp = next(r for b in page["blocks"] if b["type"] == "table" for r in b["page"]["rows"] if r["code"] == code)
    assert camp["leads"] == 1


# ── 0195: số giao nhận + tiền gốc; tên gốc + mã kế hoạch ──

def _campaign_row(page: dict[str, Any], code: str) -> dict[str, Any]:
    return next(r for r in page["rows"] if r["code"] == code)


async def test_spend_keeps_native_money_and_delivery_ratios_need_every_day(who: Who) -> None:
    from barista.kernel import executor

    mkt, tag = await who("mkt"), uuid.uuid4().hex[:6]
    code = f"D-{tag}".upper()
    await ok(mkt, "mkt.upsert_campaign", code=code, name=f"Giao nhận {tag}", start_on=(today() - timedelta(days=10)).isoformat())
    d1, d2 = (today() - timedelta(days=3)).isoformat(), (today() - timedelta(days=2)).isoformat()
    await ok(mkt, "mkt.record_spend", campaign_code=code, day=d1, amount_vnd=500_000, impressions=10_000, clicks=200,
             amount_native="19.03", currency_native="usd", fx_rate="26280", fx_source="tỷ giá nền tảng")
    await ok(mkt, "mkt.record_spend", campaign_code=code, day=d2, amount_vnd=300_000)          # ngày này nền tảng không báo số giao nhận
    row = _campaign_row(await q(mkt, "mkt.campaigns"), code)
    assert row["spent"] == 800_000 and row["impressions"] is None and row["cpc"] is None   # thiếu một ngày ⇒ không tính (không phải 0)
    await ok(mkt, "mkt.record_spend", campaign_code=code, day=d2, amount_vnd=300_000, impressions=6_000, clicks=120)   # nhập lại = ghi đè
    row = _campaign_row(await q(mkt, "mkt.campaigns"), code)
    assert (row["impressions"], row["clicks"], row["ctr"], row["cpc"], row["cpm"]) == (16_000, 320, 2.0, 2_500, 50_000)
    native = await owner_val("SELECT s.amount_native::text || ' ' || s.currency_native || ' ' || s.fx_rate::int FROM mkt.spend s "
                             "JOIN mkt.campaigns c ON c.id = s.campaign_id WHERE c.code = %s AND s.day = %s", (code, d1))
    assert native == "19.0300 USD 26280"
    r = await executor.run(mkt, "mkt.record_spend", {"campaign_code": code, "day": d1, "amount_vnd": 1, "amount_native": "1.5"})
    assert r.status == "invalid"   # tiền gốc không kèm mã tiền


async def test_campaign_keeps_original_name_and_plan_code(who: Who) -> None:
    from barista.kernel import executor

    mkt, tag = await who("mkt"), uuid.uuid4().hex[:6]
    code = f"N-{tag}".upper()
    await ok(mkt, "mkt.upsert_campaign", code=code, name=f"Tên đầu {tag}", plan_code="KH-1")
    await ok(mkt, "mkt.upsert_campaign", code=code, name=f"Tên sau {tag}")                    # đổi tên, không nêu mã kế hoạch
    row = _campaign_row(await q(mkt, "mkt.campaigns"), code)
    assert row["name"] == f"Tên sau {tag}" and row["renamed"] == f"Tên đầu {tag}" and row["plan_code"] == "KH-1"
    code2 = f"M-{tag}".upper()   # nạp từ nền tảng: tên gốc khác tên hiện tại
    await ok(mkt, "mkt.upsert_campaign", code=code2, name="Hiện tại", original_name="Lúc tạo")
    assert _campaign_row(await q(mkt, "mkt.campaigns"), code2)["renamed"] == "Lúc tạo"
    r = await executor.run(mkt, "mkt.upsert_campaign", {"code": code2, "name": "x", "owner_email": "khong-co@vi-du.vn"})
    assert r.status == "not_found"


# ── 0196: đồng chi trả ──

async def test_cost_share_is_data_capped_at_100_and_statement_is_exact(who: Who) -> None:
    from barista.kernel import executor

    mkt, boss, tag = await who("mkt"), await who("giamdoc"), uuid.uuid4().hex[:6]
    plan = f"KH-{tag}"
    day = (today() - timedelta(days=2)).isoformat()
    for i, amount in enumerate((1_000_001, 333_333)):
        code = f"S{i}-{tag}".upper()
        await ok(mkt, "mkt.upsert_campaign", code=code, name=f"Đồng chi {i} {tag}", plan_code=plan, start_on=(today() - timedelta(days=9)).isoformat())
        await ok(mkt, "mkt.record_spend", campaign_code=code, day=day, amount_vnd=amount)
    start = (today() - timedelta(days=5)).isoformat()
    r = await executor.run(mkt, "mkt.set_cost_share", {"plan_code": plan, "team_code": "KD-A", "share_pct": 30, "valid_from": start})
    assert r.status == "denied"   # tỷ lệ góp tiền là chính sách — marketing không tự đặt
    await ok(boss, "mkt.set_cost_share", plan_code=plan, team_code="KD-A", share_pct=30, valid_from=start)
    r = await executor.run(boss, "mkt.set_cost_share", {"plan_code": plan, "team_code": "KD-B", "share_pct": 71, "valid_from": start})
    assert r.status == "invalid"  # 30 + 71 > 100
    await ok(boss, "mkt.set_cost_share", plan_code=plan, team_code="KD-B", share_pct="27,5", valid_from=start)
    page = await q(boss, "mkt.cost_share_statement", date_from=start, date_to=today().isoformat())
    mine = [x for x in page["rows"] if x["scope"] == f"Kế hoạch {plan}"]
    assert sorted(x["owed"] for x in mine) == [366_667, 400_000]   # 1.333.334 × 27,5% = 366.666,85 ⇒ làm tròn MỘT lần; × 30% = 400.000,2
    assert all(x["scope_spend"] == 1_333_334 for x in mine)
    sid = await owner_val("SELECT id FROM mkt.cost_shares WHERE plan_code = %s AND share_bps = 2750", (plan,))
    await ok(boss, "mkt.end_cost_share", cost_share_id=str(sid), valid_to=(today() - timedelta(days=3)).isoformat())
    page = await q(boss, "mkt.cost_share_statement", date_from=start, date_to=today().isoformat())
    assert sorted(x["owed"] for x in page["rows"] if x["scope"] == f"Kế hoạch {plan}") == [0, 400_000]   # đã kết thúc trước ngày có chi phí


# ── 0198: tầng quảng cáo ──

async def test_ad_days_roll_up_to_campaign_day_rounded_once(who: Who) -> None:
    from barista.kernel import executor

    mkt, tag = await who("mkt"), uuid.uuid4().hex[:6]
    code = f"A-{tag}".upper()
    day = (today() - timedelta(days=2)).isoformat()
    await ok(mkt, "mkt.upsert_campaign", code=code, name=f"Theo quảng cáo {tag}", start_on=(today() - timedelta(days=9)).isoformat())
    for i, (vnd, usd, imp, inbox) in enumerate((("1000.4", "0.0381", 100, 2), ("2000.4", "0.0761", 200, None), ("0.4", "0.0001", None, 0))):
        await ok(mkt, "mkt.upsert_ad", campaign_code=code, code=f"ad{i}-{tag}", name=f"QC {i}", adset_code=f"set-{tag}", title=f"Tiêu đề {i}", media_type="image")
        await ok(mkt, "mkt.record_ad_day", ad_code=f"ad{i}-{tag}", day=day, amount_vnd=vnd, amount_native=usd, currency_native="USD", fx_rate="26280",
                 impressions=imp, clicks=imp and imp // 10, leads_platform=1, inbox=inbox)
    spend = await owner_val("""SELECT row_to_json(s) FROM (SELECT s.amount_vnd, s.impressions, s.inbox, s.leads_platform, s.amount_native::text AS usd
                               FROM mkt.spend s JOIN mkt.campaigns c ON c.id = s.campaign_id WHERE c.code = %s AND s.day = %s) s""", (code, day))
    assert spend == {"amount_vnd": 3001, "impressions": None, "inbox": None, "leads_platform": 3, "usd": "0.1143"}   # 3001,2 làm tròn MỘT lần; thiếu số ⇒ trống
    r = await executor.run(mkt, "mkt.record_spend", {"campaign_code": code, "day": day, "amount_vnd": 1})
    assert r.status == "invalid"   # ngày đã có số theo quảng cáo: không ghi đè tay
    rows = await q(mkt, "mkt.ads", days=7, campaign_code=code)
    assert {r["name"]: r["inbox"] for r in rows["rows"]} == {"QC 0": 2, "QC 1": None, "QC 2": 0}
    exported = [x for x in await q(await who("giamdoc"), "mkt.export_ad_days", date_from=day, date_to=day) if x["campaign_code"] == code]
    assert sorted(x["amount_vnd"] for x in exported) == ["0.4000", "1000.4000", "2000.4000"]   # chuỗi thập phân, không làm tròn ở dòng


async def test_ad_results_is_the_overview_numbers_for_any_window_behind_the_company_figures_family(who: Who, monkeypatch: Any) -> None:
    """``mkt.ad_results`` (họ company_figures, API 1.27 — gói kết nối nền tảng quảng cáo, ADR 0208): cùng số với màn Hiệu quả quảng cáo cho cùng kỳ,
    kèm từng quảng cáo; module không khai họ thì không gọi được; ``export_campaigns`` mang ngân sách để bên đồng bộ gọi lại upsert không xoá nó."""
    import pytest

    from barista.db import db
    from barista.kernel import registry
    from barista.kernel.errors import Denied
    from barista.kernel.executor import core_ctx

    mkt, gd, tag = await who("mkt"), await who("giamdoc"), uuid.uuid4().hex[:6]
    code = await _campaign(mkt, f"R{tag}", "7tr")
    await ok(mkt, "mkt.upsert_ad", campaign_code=code, code=f"adr-{tag}", name="QC kết quả", adset_code=f"setr-{tag}")
    await ok(mkt, "mkt.record_ad_day", ad_code=f"adr-{tag}", day=(today() - timedelta(days=1)).isoformat(), amount_vnd="1500000.6", leads_platform=0)
    d_from, d_to = today() - timedelta(days=29), today()
    async with db.tenant_tx(gd, rollback=True) as conn:
        with pytest.raises(Denied, match="company_figures"):
            await core_ctx(conn, gd, module="talk").call_system("mkt.ad_results", date_from=d_from, date_to=d_to)
    monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS, "company_figures": {*registry.FAMILY_CALLERS["company_figures"], "talk"}})
    async with db.tenant_tx(gd, rollback=True) as conn:
        got = await core_ctx(conn, gd, module="talk").call_system("mkt.ad_results", date_from=d_from, date_to=d_to)
    mine = next(c for c in got["campaigns"] if c["code"] == code)
    page = await q(gd, "mkt.overview", period="last_30")
    row = next(r for b in page["blocks"] if b["type"] == "table" for r in b["page"]["rows"] if r["code"] == code)
    assert (mine["spend"], mine["leads"], mine["qualified"], mine["outcome_count"]) == (row["spend"], row["leads"], row["qualified"], row["outcome"])
    assert mine["offering_code"] == "HSR" and mine["spend"] == 7_000_000 + 1_500_001
    assert [(a["campaign_code"], a["spend"], a["leads_platform"]) for a in got["ads"] if a["code"] == f"adr-{tag}"] == [(code, 1_500_001, 0)]
    assert isinstance(got["reliable"], bool) and isinstance(got["notes"], list)
    exported = next(c for c in await q(gd, "mkt.export_campaigns") if c["code"] == code)
    assert exported["budget_vnd"] == 100_000_000


async def test_campaign_value_needs_all_scope_of_the_value_resource() -> None:
    """Giá trị kết quả bán trên màn Marketing là số GỘP cả chiến dịch (gồm giao dịch của người khác) ⇒ chỉ mở cho người đọc được tài nguyên giá trị
    với phạm vi "all". Phạm vi hẹp hơn mà vẫn thấy = lộ giá trị của người khác (rà ADR 0239 F1, 17/11/2026). Không có tài nguyên khai ⇒ ai xem màn cũng thấy."""
    from types import SimpleNamespace
    from uuid import UUID

    from barista.kernel import needs, ui
    from barista.kernel.principal import Grant, Principal
    from barista.modules.mkt import _outcome_for_viewer

    def ctx(info: dict[str, Any] | None, grants: dict[str, Grant]) -> Any:
        async def call_system(name: str, **_: Any) -> dict[str, Any] | None:
            assert name == "crm.outcome_info"
            return info
        p = Principal(tenant_id=UUID(int=1), user_id=UUID(int=2), grants=grants)
        return SimpleNamespace(call_system=call_system, p=p, sees=lambda n: needs.meets(p, ui.needs_of(n)))   # cùng luật cửa ra (lớp 3, 1.59)

    info = {"count_label": "Giao dịch đã ký", "value_label": "Phí đã ký", "value_resource": "ledger"}
    for scope, expect in [("own", False), ("unit", False), ("team", False), ("all", True)]:
        assert (await _outcome_for_viewer(ctx(info, {"ledger": Grant(scope, frozenset({"read"}))})))[2] is expect, scope
    assert (await _outcome_for_viewer(ctx(info, {})))[2] is False                                                    # không có quyền
    assert (await _outcome_for_viewer(ctx(info, {"ledger": Grant("all", frozenset({"write"}))})))[2] is False         # all nhưng không đọc
    assert (await _outcome_for_viewer(ctx({**info, "value_resource": ""}, {})))[2] is True                           # gói không khai tài nguyên
    assert (await _outcome_for_viewer(ctx(None, {})))[2] is False                                                    # không gói nào đo kết quả bán
