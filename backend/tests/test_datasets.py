"""Tập dữ liệu có chiều / chỉ số (0100): nhóm / cộng trên TOÀN BỘ dòng bằng quyền người xem; tổng các nhóm = ô tổng; ô tổng bấm ra đúng các
dòng gốc (khớp từng đồng); tên chiều / chỉ số lạ bị từ chối (không có đường chèn SQL)."""

from tests.conftest import Who, q


def _kpi(page: dict, key: str) -> dict:
    return next(k for k in page["kpis"] if k.get("key") == key)


async def test_ledger_and_receivables_by_month(who: Who) -> None:
    kt = await who("ketoan")
    led = await q(kt, "core.ds_ledger", measures=["debit", "credit"], by=["month"])
    assert _kpi(led, "debit")["value"] == _kpi(led, "credit")["value"]         # ghi kép: tổng Nợ = tổng Có
    assert next(c for c in led["columns"] if c["key"] == "month").get("order") == "asc"
    ar = await q(kt, "acct.ds_receivables", measures=["remaining"], by=["collect"])
    assert _kpi(ar, "remaining")["value"] == sum(r["remaining"] for r in ar["rows"])


async def test_ledger_amount_follows_the_account_nature(who: Who) -> None:
    """Doanh thu (bên Có) và chi phí (bên Nợ) đều ra số dương với chỉ số amount — không phải nhớ dấu."""
    kt = await who("ketoan")
    page = await q(kt, "core.ds_ledger", measures=["amount", "debit", "credit"], by=["kind"])
    by = {r["kind"]: r for r in page["rows"]}
    if "revenue" in by:
        assert by["revenue"]["amount"] == by["revenue"]["credit"] - by["revenue"]["debit"] >= 0
    if "expense" in by:
        assert by["expense"]["amount"] == by["expense"]["debit"] - by["expense"]["credit"] >= 0
    assert "revenue" in by or "expense" in by


async def test_ad_spend_by_campaign_matches_what_was_recorded(who: Who) -> None:
    """Chi phí quảng cáo (mkt.ds_spend): lọc một chiến dịch ra đúng số đã ghi; tổng các nhóm = ô tổng; kênh là nhãn."""
    import uuid
    from datetime import timedelta

    from barista.kernel.clock import today
    from tests.conftest import ok

    mkt = await who("mkt")
    code = f"T-DS-{uuid.uuid4().hex[:6]}".upper()
    await ok(mkt, "mkt.upsert_campaign", code=code, name="Chiến dịch tập dữ liệu", channel="facebook", offering_code="HSR", budget_vnd="10tr",
             start_on=(today() - timedelta(days=10)).isoformat())
    for back, amount in ((2, "3tr"), (1, "4tr")):
        await ok(mkt, "mkt.record_spend", campaign_code=code, day=(today() - timedelta(days=back)).isoformat(), amount_vnd=amount)
    one = await q(mkt, "mkt.ds_spend", measures=["spend", "days"], by=["campaign"], where={"campaign": [code]})
    assert [(r["campaign"], r["spend"], r["days"]) for r in one["rows"]] == [(code, 7_000_000, 2)]
    page = await q(mkt, "mkt.ds_spend", measures=["spend"], by=["channel", "month"])
    assert _kpi(page, "spend")["value"] == sum(r["spend"] for r in page["rows"])
    assert next(c for c in page["columns"] if c["key"] == "channel")["format"] == "badge"


async def test_leads_dataset_counts_every_lead_the_viewer_may_see_and_nothing_personal(who: Who) -> None:
    """Lead (crm.ds_leads): người xem cả công ty đếm đúng mọi lead của công ty; sale mới (phạm vi của mình) không thấy lead người khác;
    không có cột tên / SĐT khách."""
    from tests.conftest import fresh_sale, owner_val

    gd = await who("giamdoc")
    page = await q(gd, "crm.ds_leads", measures=["count", "won", "lost"], by=["stage"])
    total = await owner_val("SELECT count(*) FROM crm.leads WHERE tenant_id = %s", (gd.tenant_id,))
    assert _kpi(page, "count")["value"] == total == sum(r["count"] for r in page["rows"]) > 0
    by = {r["stage"]: r for r in page["rows"]}
    assert by.get("deal", {"won": 0})["won"] == _kpi(page, "won")["value"]
    keys = {c["key"] for c in page["columns"]}
    assert not keys & {"name", "phone", "email", "customer"}
    newbie = await fresh_sale(who)
    mine = await q(newbie, "crm.ds_leads", measures=["count"])
    assert _kpi(mine, "count")["value"] == 0     # chưa giữ lead nào ⇒ không đếm lead của người khác


async def test_ratio_measures_divide_sums_never_add_ratios_and_leave_zero_denominators_empty(who: Who) -> None:
    """Tỷ lệ (0220): mỗi nhóm và ô tổng = tổng tử ÷ tổng mẫu; mẫu 0 ⇒ trống (không đo được), không phải 0; cột tử / mẫu không chọn thì không hiện."""
    import uuid
    from datetime import timedelta

    from barista.kernel.clock import today
    from tests.conftest import ok

    mkt = await who("mkt")
    code = f"T-RATIO-{uuid.uuid4().hex[:6]}".upper()
    await ok(mkt, "mkt.upsert_campaign", code=code, name="Tỷ lệ", channel="facebook", offering_code="HSR", budget_vnd="10tr",
             start_on=(today() - timedelta(days=5)).isoformat())
    d1, d2 = (today() - timedelta(days=2)).isoformat(), (today() - timedelta(days=1)).isoformat()
    await ok(mkt, "mkt.record_spend", campaign_code=code, day=d1, amount_vnd="3tr", leads_platform=3, impressions=1000, clicks=50)
    await ok(mkt, "mkt.record_spend", campaign_code=code, day=d2, amount_vnd="4tr", leads_platform=0, impressions=1000, clicks=30)
    page = await q(mkt, "mkt.ds_spend", measures=["cpl_platform", "ctr"], by=["on_day"], where={"campaign": [code]})
    rows = {r["on_day"]: r for r in page["rows"]}
    assert rows[d1]["cpl_platform"] == 1_000_000 and rows[d2]["cpl_platform"] is None          # ngày không có lead ⇒ không đo được
    assert rows[d1]["ctr"] == 5.0 and rows[d2]["ctr"] == 3.0
    assert _kpi(page, "cpl_platform")["value"] == round(7_000_000 / 3) and _kpi(page, "ctr")["value"] == 4.0   # tổng ÷ tổng, không trung bình hai tỷ lệ
    assert [c["key"] for c in page["columns"]] == ["on_day", "cpl_platform", "ctr"] and "spend" not in rows[d1]
    assert page["rows"][0]["on_day"] == d1                                                        # xếp theo tỷ lệ đầu (giảm dần), không đo được xuống cuối


async def test_lead_rates_match_their_counts(who: Who) -> None:
    gd = await who("giamdoc")
    page = await q(gd, "crm.ds_leads", measures=["win_rate", "won", "count"], by=["stage"])
    won, total = _kpi(page, "won")["value"], _kpi(page, "count")["value"]
    assert _kpi(page, "win_rate")["value"] == (round(won / total * 100, 1) if total else None)


async def test_a_dashboard_chart_never_adds_ratios_together(who: Who) -> None:
    """Dashboard (0220): biểu đồ trên tập có tỷ lệ — mỗi cột là tỷ lệ của nhóm đó; gộp nhiều dòng (nhóm theo ít chiều hơn nguồn) ⇒ trống, không cộng."""
    from tests.conftest import ok

    gd = await who("giamdoc")
    app = await ok(gd, "apps.create", title="Tỷ lệ thành giao dịch")
    await ok(gd, "apps.add_block", app_id=app["app_id"], page="main",
             block={"kind": "chart", "title": "Theo chặng", "chart": "bar", "measure": "win_rate",
                    "source": {"dataset": "crm.leads", "measures": ["win_rate"], "by": ["stage"]}})
    await ok(gd, "apps.add_block", app_id=app["app_id"], page="main",
             block={"kind": "chart", "title": "Gộp hai chiều về một", "chart": "bar", "measure": "win_rate", "by": "stage",
                    "source": {"dataset": "crm.leads", "measures": ["win_rate"], "by": ["stage", "source"]}})
    page = await q(gd, "apps.open", app_id=app["app_id"])
    charts = [b for b in page["blocks"] if b.get("type") == "chart"]
    one = charts[0]["series"][0]["values"]
    assert one and all(v is None or 0 <= v <= 100 for v in one)                       # mỗi chặng một tỷ lệ 0–100
    deal_ix = charts[0]["labels"].index("Giao dịch") if "Giao dịch" in charts[0]["labels"] else None
    if deal_ix is not None:
        assert one[deal_ix] == 100.0                                                  # chặng giao dịch: mọi lead đều "thành giao dịch"
    merged = charts[1]["series"][0]["values"]
    assert all(v is None or 0 <= v <= 100 for v in merged)                            # không có giá trị > 100 do cộng tỷ lệ


async def test_numbers_carry_their_provenance_and_warn_beyond_coverage(who: Who) -> None:
    """Nguồn gốc của số (0220, 1.42): trang tập dữ liệu + mọi khối dashboard ghi nguồn, phạm vi quyền, số đủ tới ngày nào; kỳ xem vượt độ phủ ⇒ cảnh
    báo rõ (số thiếu không được đọc thành "không xảy ra"); kỳ nằm trong độ phủ ⇒ không cảnh báo."""
    from datetime import timedelta

    from barista.kernel.clock import today
    from tests.conftest import ok

    mkt, gd = await who("mkt"), await who("giamdoc")
    through, reliable = today() - timedelta(days=2), today() - timedelta(days=30)
    await ok(mkt, "mkt.set_coverage", spend_reliable_from=reliable.isoformat(), lead_reliable_from=reliable.isoformat(),
             spend_complete_through=through.isoformat())
    wide = await q(gd, "mkt.ds_spend", measures=["spend"], date_from=(today() - timedelta(days=40)).isoformat(),
                   date_before=(today() + timedelta(days=1)).isoformat())
    src = wide["source"]
    assert src["kind"] == "dataset" and src["label"] == "Chi phí quảng cáo" and src["scope"] and src["as_of"]
    assert src["through"] == through.isoformat() and f"{through:%d/%m/%Y}" in src["warn"] and f"{reliable:%d/%m/%Y}" in src["warn"]
    inside = await q(gd, "mkt.ds_spend", measures=["spend"], date_from=(today() - timedelta(days=10)).isoformat(),
                     date_before=(today() - timedelta(days=4)).isoformat())
    assert "warn" not in inside["source"]                                                        # kỳ nằm gọn trong độ phủ

    app = await ok(gd, "apps.create", title="Chi phí có nguồn gốc")
    await ok(gd, "apps.add_block", app_id=app["app_id"], page="main",
             block={"kind": "kpi", "title": "Chi phí", "measure": "spend", "source": {"dataset": "mkt.spend", "measures": ["spend"]}})
    page = await q(gd, "apps.open", app_id=app["app_id"])
    block = next(b for b in page["blocks"] if b.get("type") == "kpis")
    assert block["source"]["label"] == "Chi phí quảng cáo" and block["source"]["through"] == through.isoformat()
    assert "warn" in block["source"]                                                              # không lọc kỳ ⇒ tới hôm nay ⇒ vượt độ phủ
