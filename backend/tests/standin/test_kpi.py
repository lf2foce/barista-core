"""KPI trên CHỈ SỐ CỦA MỘT GÓI NGÀNH (0101) — bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_kpi.py``: thực tế KPI là ĐÚNG con số của chỉ số
module khai (``Module.metric`` — ở đây ``orderdesk.metric_signed`` / ``metric_fee`` của gói thế vai), chỉ tiêu đội cộng cả cây đội con, xếp hạng ẩn tên
người khác trừ khi công ty bật, chỉ tiêu đội lệch tổng chỉ tiêu người được đánh dấu. Công ty thế vai không có chỉ tiêu / đơn mẫu — test tự đặt."""

from datetime import date
from typing import Any

from barista.db import db
from barista.kernel.clock import today
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal
from tests.conftest import Who, fresh_phone, ok, q
from tests.standin import helpers
from tests.standin.conftest import DOMAIN
from tests.test_kpi import THIS

SIGNED, FEE, CALLS = "orderdesk.metric_signed", "orderdesk.metric_fee", "crm.metric_calls"
PEOPLE = {"sale1": 3, "sale2": 2, "sale3": 2}                                   # chỉ tiêu đơn ký từng người trong cây Miền Bắc: tổng 7
TEAMS = {"KV-MB": 8, "KHOI-1": 7, "KD-A": 5, "KD-B": 2}                         # khu vực đặt 8 ≠ 7 của người ⇒ lệch


async def _targets(who: Who) -> None:
    """Chỉ tiêu tháng này nhập qua đúng ``kpi.set_target`` (đặt lại cùng số ⇒ chạy lặp an toàn) + mỗi team một đơn ĐÃ KÝ tháng này."""
    giamdoc = await who("giamdoc")
    for local, n in PEOPLE.items():
        await ok(giamdoc, "kpi.set_target", period=THIS, metric=SIGNED, user_email=f"{local}@{DOMAIN}", target=n)
    for code, n in TEAMS.items():
        await ok(giamdoc, "kpi.set_target", period=THIS, metric=SIGNED, team_code=code, target=n)
    await ok(giamdoc, "kpi.set_target", period=THIS, metric=FEE, user_email=f"sale1@{DOMAIN}", target=500_000_000)
    await ok(giamdoc, "kpi.set_target", period=THIS, metric=CALLS, user_email=f"sale1@{DOMAIN}", target=40)
    for local in ("sale1", "sale3"):                                            # sale1 ở Team A, sale3 ở Team B — cùng Khối 1 / Miền Bắc
        await helpers.signed(who, owner=local, signed_on=today())


async def _metric(p: Principal, key: str, group_by: str = "") -> dict[str, Any]:
    start = today().replace(day=1)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    async with db.tenant_tx(p, rollback=True) as conn:
        return await core_ctx(conn, p, module="kpi").call_system(key, since=start.isoformat(), until=end.isoformat(), group_by=group_by)


async def test_kpi_actual_is_the_same_number_as_the_shared_metric(who: Who) -> None:
    await _targets(who)
    sale1 = await who("sale1")
    lead = await ok(sale1, "crm.create_lead", name="Khách gọi thử KPI", phone=fresh_phone())
    await ok(sale1, "crm.log_activity", lead_id=lead["lead_id"], kind="call", note="Gọi giới thiệu")
    mine = {r["metric"]: r for r in (await q(sale1, "kpi.mine", period=THIS))["rows"]}
    assert {SIGNED, CALLS, FEE} <= set(mine)
    assert mine[SIGNED]["actual"] >= 1 and mine[CALLS]["actual"] >= 1 and mine[FEE]["actual"] >= 120_000_000   # đơn 4 tỷ ⇒ phí 3%
    # ``additive``: tổng các dòng theo người ≤ tổng chung (dòng không có người chỉ vào tổng). Với số đếm không chắc cộng được (một đối tượng có thể tính
    # cho nhiều người), bất biến đúng là không ai vượt tổng chung.
    for key, additive in ((SIGNED, False), (CALLS, True), (FEE, True)):
        by_person = await _metric(sale1, key, "person")
        mine_value = next((r["value"] for r in by_person["rows"] if r["key"] == str(sale1.user_id)), 0)
        assert mine[key]["actual"] == mine_value, key
        assert by_person["value"] == (await _metric(sale1, key))["value"], key      # nhóm hay không nhóm: cùng một tổng
        if additive:
            assert sum(r["value"] for r in by_person["rows"]) <= by_person["value"]
        else:
            assert all(r["value"] <= by_person["value"] for r in by_person["rows"])


async def test_team_target_counts_the_whole_subtree(who: Who) -> None:
    await _targets(who)
    giamdoc = await who("giamdoc")
    rows = {r["subject"]: r for r in (await q(giamdoc, "kpi.board", period=THIS, metric=SIGNED))["rows"] if not r["user_id"]}
    region, blocks = rows["Đội: Khu vực Miền Bắc"], [rows["Đội: Team Kinh doanh A"], rows["Đội: Team Kinh doanh B"]]
    assert all(b["actual"] >= 1 for b in blocks)                       # mỗi team có đơn ký của người mình
    assert region["actual"] >= sum(b["actual"] for b in blocks) >= 2   # khu vực = khối = các team (+ đơn gắn thẳng khối / khu vực nếu có)
    assert rows["Đội: Khối kinh doanh 1"]["actual"] == region["actual"]   # khối 1 là nhánh duy nhất của Miền Bắc
    by_team = await _metric(giamdoc, SIGNED, "team")
    assert region["actual"] <= by_team["value"]                        # cây con không bao giờ vượt tổng cả công ty của chính chỉ số đó


async def test_ranking_hides_other_names_unless_the_company_allows(who: Who) -> None:
    await _targets(who)
    sale1, giamdoc = await who("sale1"), await who("giamdoc")
    page = await q(sale1, "kpi.ranking", period=THIS, metric=SIGNED)
    assert [r["id"] for r in page["rows"]] == [str(sale1.user_id)]                 # chỉ thấy chính mình
    assert next(k for k in page["kpis"] if k["label"] == "Hạng của bạn")["value"] != "—"
    assert next(k for k in page["kpis"] if k["label"] == "Người có chỉ tiêu")["value"] >= len(PEOPLE)   # hạng tính trên MỌI người, chỉ tên bị ẩn
    await ok(giamdoc, "kpi.set_policy", show_names=True)
    try:
        page = await q(sale1, "kpi.ranking", period=THIS, metric=SIGNED)
        assert len(page["rows"]) > 1                                                 # bật ⇒ thấy cả bảng
        assert [r["rank"] for r in page["rows"]] == sorted(r["rank"] for r in page["rows"])
    finally:
        await ok(giamdoc, "kpi.set_policy", show_names=False)
    page = await q(sale1, "kpi.ranking", period=THIS)                               # không nêu chỉ số ⇒ chỉ số gói đang bật khai làm mặc định
    assert "Đơn ký" in page["title"]


async def test_team_target_that_differs_from_its_people_is_flagged(who: Who) -> None:
    await _targets(who)
    giamdoc = await who("giamdoc")
    rows = {r["subject"]: r for r in (await q(giamdoc, "kpi.board", period=THIS, metric=SIGNED))["rows"] if not r["user_id"]}
    north = rows["Đội: Khu vực Miền Bắc"]                       # chỉ tiêu 8 — người trong cây: 3 + 2 + 2 = 7
    assert north["people_sum"] == "7 đơn" and north["mismatch"] == "Lệch tổng người"
    assert rows["Đội: Khối kinh doanh 1"]["people_sum"] == "7 đơn" and rows["Đội: Khối kinh doanh 1"]["mismatch"] == ""   # khớp ⇒ không đánh dấu


async def test_ranking_opens_on_the_metric_the_company_set_targets_on(who: Who) -> None:
    """Gói khai chỉ số xếp hạng mặc định (đơn ký) nhưng tháng đó công ty chỉ đặt chỉ tiêu trên chỉ số khác ⇒ bảng mở theo chỉ số có chỉ tiêu, không trống."""
    giamdoc = await who("giamdoc")
    period = "2031-03"
    await ok(giamdoc, "kpi.set_target", period=period, metric=FEE, user_email=f"sale2@{DOMAIN}", target=400_000_000)
    got = await q(giamdoc, "kpi.ranking", period=period)
    assert any(r.get("target_text") for r in got.get("rows", []))
