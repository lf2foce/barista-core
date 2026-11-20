"""Mẫu quảng cáo ra khách (0210): lead nhớ MẪU quảng cáo (nguồn gửi mã) ⇒ xếp mẫu theo KHÁCH THẬT, không theo lead form. Canh:
· lead nhớ mẫu qua nhập tay / file (crm.create_lead) và qua nguồn ngoài (leadflow.intake); mẫu lạ không chặn lead (chỉ không biết mẫu);
· chỉ mẫu ĐỦ MẪU (chi phí + số lead tối thiểu) mới được xếp; "tốt nhất" chỉ khi có ≥ 2 mẫu đủ mẫu để so, theo chi phí / khách quan tâm;
· dữ liệu chưa tin cậy ⇒ ẩn tỷ lệ, không gắn "tốt nhất" (cùng luật với màn Hiệu quả quảng cáo);
· lưu ý nói rõ bao nhiêu lead biết mẫu;
· mỗi test lọc theo CHIẾN DỊCH của mình (so mẫu trong một chiến dịch) ⇒ không phụ thuộc dữ liệu test khác trong cùng DB."""

import uuid
from datetime import timedelta
from typing import Any

from barista.kernel.clock import today
from tests.conftest import Who, fresh_phone, ok, owner_val, q


def _rows(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    tbl = next(b for b in page["blocks"] if b["type"] == "table")
    return {r["code"]: r for r in tbl["page"]["rows"]}


def _facts(page: dict[str, Any]) -> list[str]:
    return [f for b in page["blocks"] if b["type"] == "callout" for f in b.get("facts", [])]


async def _setup(mkt: Any, tag: str) -> str:
    code = f"CR-{tag}".upper()
    await ok(mkt, "mkt.upsert_campaign", code=code, name=f"Chiến dịch mẫu {tag}", channel="facebook", offering_code="HSR", budget_vnd="100tr",
             start_on=(today() - timedelta(days=20)).isoformat())
    day = (today() - timedelta(days=2)).isoformat()
    for ad, vnd in (("a", "1000000"), ("b", "1000000"), ("c", "50000")):
        await ok(mkt, "mkt.upsert_ad", campaign_code=code, code=f"{ad}-{tag}", name=f"Mẫu {ad.upper()}", title=f"Tiêu đề {ad}", media_type="image")
        await ok(mkt, "mkt.record_ad_day", ad_code=f"{ad}-{tag}", day=day, amount_vnd=vnd, leads_platform=5)
    return code


async def test_leads_remember_the_ad_and_the_best_ad_is_the_one_bringing_interested_customers(who: Who) -> None:
    mkt, sale, admin = await who("mkt"), await who("sale1"), await who("admin")
    tag = uuid.uuid4().hex[:6]
    code = await _setup(mkt, tag)
    await ok(mkt, "mkt.set_coverage", spend_reliable_from=(today() - timedelta(days=90)).isoformat(),
             lead_reliable_from=(today() - timedelta(days=90)).isoformat(), spend_complete_through=today().isoformat())

    async def lead(ad: str, interested: bool = False) -> Any:
        out = await ok(sale, "crm.create_lead", name=f"Khách {ad} {uuid.uuid4().hex[:4]}", phone=fresh_phone(), offering_code="HSR", source="ads",
                       campaign_code=code, ad_code=f"{ad}-{tag}")
        if interested:
            await ok(sale, "crm.set_stage", lead_id=out["lead_id"], stage="prospect")
        return out["lead_id"]

    # Mẫu A: 4 lead, 2 khách quan tâm (chi phí / khách quan tâm 500k). Mẫu B: 4 lead, 1 khách quan tâm (1tr) — B nhiều lead form như A nhưng ít khách thật.
    for i in range(4):
        await lead("a", interested=i < 2)
        await lead("b", interested=i < 1)
    await lead("c")   # mẫu C: chi ít, 1 lead ⇒ chưa đủ mẫu
    stray = await ok(sale, "crm.create_lead", name="Khách mẫu lạ", phone=fresh_phone(), offering_code="HSR", source="ads", campaign_code=code,
                     ad_code="khong-co")
    assert await owner_val("SELECT ad_id FROM crm.leads WHERE id = %s", (stray["lead_id"],)) is None   # mẫu lạ: lead vẫn vào, không biết mẫu
    other = uuid.uuid4().hex[:6]
    await _setup(mkt, other)   # mẫu có thật nhưng của CHIẾN DỊCH KHÁC ⇒ không gắn nhầm
    cross = await ok(sale, "crm.create_lead", name="Khách mẫu chéo", phone=fresh_phone(), offering_code="HSR", source="ads", campaign_code=code,
                     ad_code=f"a-{other}")
    assert await owner_val("SELECT ad_id FROM crm.leads WHERE id = %s", (cross["lead_id"],)) is None

    # nguồn ngoài (leadflow.intake) cũng nhớ mẫu
    got = await ok(admin, "leadflow.intake", name="Khách từ nguồn", phone=fresh_phone(), offering_code="HSR", source="ads", campaign_code=code,
                   ad_code=f"b-{tag}", provider="thu", external_ref=f"x-{tag}")
    ad_b = await owner_val("SELECT id FROM mkt.ads WHERE code = %s", (f"b-{tag}",))
    assert await owner_val("SELECT ad_id FROM crm.leads WHERE id = %s", (got["lead_id"],)) == ad_b

    page = await q(mkt, "mkt.creatives", period="last_7", offering_code="HSR", campaign_code=code)
    rows = _rows(page)
    a, b, c = rows[f"a-{tag}"], rows[f"b-{tag}"], rows[f"c-{tag}"]
    assert (a["leads"], a["qualified"], a["platform_leads"]) == (4, 2, 5) and (b["leads"], b["qualified"]) == (5, 1)
    assert a["verdict"] == "win" and b["verdict"] == "ok" and c["verdict"] == "thin"          # A thắng vì ra KHÁCH QUAN TÂM rẻ hơn, không vì lead form
    assert a["cpql"] == 500_000 and b["cpql"] == 1_000_000
    assert any("Mẫu tốt nhất HSR: Mẫu A" in i["title"] for blk in page["blocks"] if blk["type"] == "list" for i in blk["items"])
    assert set(rows) == {f"{x}-{tag}" for x in "abc"}   # lọc chiến dịch: chỉ mẫu của chiến dịch này
    assert any("10/12 lead của các chiến dịch này biết mẫu quảng cáo" in f for f in _facts(page))  # lead mẫu lạ ⇒ nói rõ phần không biết mẫu

    # ngưỡng do người xem chỉnh: nâng số lead tối thiểu ⇒ không mẫu nào đủ ⇒ không ai "tốt nhất"
    strict = _rows(await q(mkt, "mkt.creatives", period="last_7", campaign_code=code, min_leads=10))
    assert {r["verdict"] for r in strict.values()} == {"thin"}


async def test_unreliable_data_hides_ratios_and_crowns_no_ad(who: Who) -> None:
    mkt, sale = await who("mkt"), await who("sale1")
    tag = uuid.uuid4().hex[:6]
    code = await _setup(mkt, tag)
    await ok(mkt, "mkt.set_coverage", spend_reliable_from=today().isoformat(), lead_reliable_from=today().isoformat(),
             spend_complete_through=today().isoformat())   # kỳ xem bắt đầu trước mốc tin cậy ⇒ không an toàn
    for ad in ("a", "b"):
        for _ in range(3):
            out = await ok(sale, "crm.create_lead", name=f"Khách {ad}", phone=fresh_phone(), offering_code="HSR", source="ads", campaign_code=code,
                           ad_code=f"{ad}-{tag}")
            await ok(sale, "crm.set_stage", lead_id=out["lead_id"], stage="prospect")
    try:
        rows = list(_rows(await q(mkt, "mkt.creatives", period="last_7", campaign_code=code)).values())
        assert rows and all(r["cpl"] is None and r["cpql"] is None for r in rows) and "win" not in {r["verdict"] for r in rows}
    finally:   # trả mốc tin cậy về như các test khác dùng — không để "chưa tin cậy" rò sang test sau trong cùng DB
        await ok(mkt, "mkt.set_coverage", spend_reliable_from=(today() - timedelta(days=90)).isoformat(),
                 lead_reliable_from=(today() - timedelta(days=90)).isoformat(), spend_complete_through=today().isoformat())
