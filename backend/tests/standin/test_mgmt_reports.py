"""TRANG BÁO CÁO QUẢN TRỊ của một gói ngành (0166) — bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_mgmt_reports.py``: trang do module SỞ HỮU con
số dựng bằng khối của lõi (bố cục ``w`` mỗi khối 1–12, biểu đồ ``combo`` chuỗi cùng độ dài nhãn), chỉ người phạm vi cả công ty xem (``min_scope="all"``),
menu Báo cáo quản trị tắt mặc định, và chỉ tiêu CẢ CÔNG TY đọc qua họ ``company_figures`` (``kpi.company_targets``: chỉ đội gốc, theo tháng)."""

from typing import Any
from uuid import UUID

from barista.kernel import executor
from barista.kernel.clock import today
from barista.workspaces import manifest
from tests.conftest import Who, ok, owner_val, q
from tests.standin import helpers
from tests.test_journey import _blocks

PAGE, METRIC = "orderdesk.report_year", "orderdesk.metric_signed"


def _rows_fit(blocks: list[dict[str, Any]]) -> bool:
    """Mỗi khối có ``w`` hợp lệ 1–12 (khối liền nhau dồn hàng — hàng tràn thì sang hàng mới, không cắt)."""
    return all(1 <= b["w"] <= 12 for b in blocks if "w" in b)


async def test_the_page_renders_for_every_year_and_its_numbers_match_the_data(who: Who, company: UUID) -> None:
    gd = await who("giamdoc")
    await helpers.signed(who, signed_on=today())
    for year in (0, today().year, today().year - 1, 2020):
        page = await q(gd, PAGE, year=year)
        assert page["kind"] == "card" and _rows_fit(page["blocks"]) and all("w" in b for b in page["blocks"]), year
        for b in _blocks(page, "chart"):
            assert all(len(s["values"]) == len(b["labels"]) for s in b["series"]), (year, b["title"])
    page = await q(gd, PAGE)
    kpis = {k["label"]: k["value"] for k in _blocks(page, "kpis")[0]["items"]}
    sql = "FROM orderdesk.orders WHERE tenant_id = %s AND status = 'signed' AND extract(year FROM signed_on) = %s"
    assert kpis["Đơn ký"] == await owner_val(f"SELECT count(*) {sql}", (company, today().year)) >= 1          # số cả công ty, không theo người xem
    assert kpis["Giá trị đã ký"] == await owner_val(f"SELECT coalesce(sum(amount_vnd), 0) {sql}", (company, today().year))
    table = _blocks(page, "table")[0]["page"]
    assert sum(r["value"] for r in table["rows"]) == kpis["Giá trị đã ký"] and len(table["rows"]) == 12      # bảng tháng cộng lại = ô tổng


async def test_only_company_wide_viewers_and_menu_is_off_by_default(who: Who) -> None:
    for local in ("sale1", "mkt", "truonga"):
        r = await executor.query(await who(local), PAGE, {})
        assert r.status == "denied", (local, r.status)                          # trưởng nhóm đọc được đơn của đội nhưng không phải số cả công ty
    gd = await who("giamdoc")
    assert "mgmt" not in {w["key"] for w in manifest(gd, "x")["workspaces"]}   # gói Báo cáo quản trị tắt mặc định


async def test_company_target_chart_reads_only_root_team_targets_and_states_the_even_split(who: Who) -> None:
    """Có chỉ tiêu cả công ty (đội gốc) ⇒ trang vẽ thực tế so với chỉ tiêu và NÓI RÕ khi các tháng bằng nhau (chia đều). Chỉ tiêu của đội con / của người
    KHÔNG cộng vào chỉ tiêu công ty. Dùng năm đã qua (không ai đặt chỉ tiêu) để không chạm chỉ tiêu tháng này của test KPI trên cùng công ty."""
    gd = await who("giamdoc")
    year = 2020
    chart_of = lambda page: [b for b in _blocks(page, "chart") if b["title"].startswith("Đơn ký so với chỉ tiêu công ty")]   # noqa: E731
    await ok(gd, "kpi.set_target", metric=METRIC, team_code="KD-A", period=f"{year}-01", target=9)            # đội con + người: không phải của công ty
    await ok(gd, "kpi.set_target", metric=METRIC, user_email=(await who("sale1")).email, period=f"{year}-01", target=4)
    await ok(gd, "kpi.set_target", metric="crm.metric_calls", team_code="HQ", period=f"{year}-01", target=700)   # chỉ số khác: không lẫn
    if not await owner_val("SELECT count(*) FROM kpi.targets t JOIN core.teams c ON c.id = t.team_id WHERE c.code = 'HQ' AND t.metric = %s"
                           " AND t.tenant_id = %s AND t.period >= %s AND t.period < %s", (METRIC, gd.tenant_id, f"{year}-01-01", f"{year + 1}-01-01")):
        assert not chart_of(await q(gd, PAGE, year=year))                                                       # chưa khai ở đội gốc ⇒ không vẽ
    for mo in range(1, 13):
        await ok(gd, "kpi.set_target", metric=METRIC, team_code="HQ", period=f"{year}-{mo:02d}", target=25)
    chart = chart_of(await q(gd, PAGE, year=year))[0]
    assert chart["kind"] == "combo" and chart["series"][1]["values"] == [25] * 12
    assert "BẰNG NHAU" in chart["note"] and "chia đều" in chart["note"] and "300 đơn" in chart["note"]
    await ok(gd, "kpi.set_target", metric=METRIC, team_code="HQ", period=f"{year}-12", target=40)
    chart = chart_of(await q(gd, PAGE, year=year))[0]
    assert chart["series"][1]["values"] == [25] * 11 + [40] and "BẰNG NHAU" not in chart["note"] and "315 đơn" in chart["note"]
    assert not chart_of(await q(gd, PAGE, year=year - 1))                                                       # năm khác: không có chỉ tiêu ⇒ không vẽ
