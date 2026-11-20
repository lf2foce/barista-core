"""STUDIO DASHBOARD (0175) trên tập dữ liệu của một gói ngành, qua gói thế vai: bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_dashboard_studio.py``
— khối có bề rộng, biểu đồ combo / scatter / treemap, lọc chéo gắn tham số app, cột lọc trái, sắp xếp / đổi cỡ khối bằng một lệnh — định nghĩa vẫn là
DỮ LIỆU, mở app vẫn bằng quyền người xem."""

from typing import Any

from barista import workspaces
from barista.kernel import executor
from tests.conftest import Who, ok, q
from tests.standin.test_apps import _app, _orders
from tests.test_apps import _blocks

PARAMS = [{"key": "ky", "label": "Kỳ", "kind": "period", "default": "all"},
          {"key": "dong", "label": "Dòng dịch vụ", "options_query": "orderdesk.line_options"}]
SRC = {"dataset": "orderdesk.orders", "measures": ["value", "count"], "by": ["line"], "where": {"line": "$dong"}, "date": "signed_on", "period": "$ky"}


async def _dashboard(p: Any) -> str:
    return await _app(p, "Dashboard bán hàng", [
        {"kind": "kpi", "title": "Giá trị", "source": {**SRC, "by": [], "measures": ["value"]}, "w": 3},
        {"kind": "kpi", "title": "Số đơn", "source": {**SRC, "by": [], "measures": ["count"]}, "w": 3},
        {"kind": "chart", "chart": "combo", "title": "Giá trị và số đơn theo dòng dịch vụ", "source": SRC, "measure": "value", "measure2": "count",
         "filter_param": "dong", "w": 8},
        {"kind": "chart", "chart": "treemap", "title": "Cơ cấu theo dòng dịch vụ", "source": SRC, "measure": "value", "filter_param": "dong", "w": 4},
        {"kind": "chart", "chart": "scatter", "title": "Số đơn × giá trị", "source": SRC, "measure": "count", "measure2": "value", "w": 6},
    ], params=PARAMS, filters="side")


async def test_dashboard_blocks_carry_width_and_new_chart_kinds(who: Who) -> None:
    await _orders(who)
    gd = await who("giamdoc")
    app = await _dashboard(gd)
    page = await q(gd, "apps.open", app_id=app)
    kpis = _blocks(page, "kpis")
    assert [b.get("w") for b in kpis] == [3, 3]                        # ô số mang w đứng riêng, không gộp một hàng
    charts = {c["title"]: c for c in _blocks(page, "chart")}
    combo = charts["Giá trị và số đơn theo dòng dịch vụ"]
    assert combo["kind"] == "combo" and combo["w"] == 8 and [s.get("as") for s in combo["series"]] == ["bar", "line"]
    assert combo["series"][1]["axis"] == "right" and combo["unit"] == "vnd" and combo["unit_right"] == "count"
    assert combo["pick"]["param"] == "dong" and len(combo["pick"]["values"]) == len(combo["labels"]) == 2   # lọc chéo gắn tham số app
    assert charts["Cơ cấu theo dòng dịch vụ"]["kind"] == "treemap"
    sc = charts["Số đơn × giá trị"]
    assert sc["kind"] == "scatter" and len(sc["series"][0]["x"]) == len(sc["labels"])


async def test_cross_filter_value_is_the_raw_dimension_value_and_narrows_the_page(who: Who) -> None:
    await _orders(who)
    gd = await who("giamdoc")
    app = await _dashboard(gd)
    page = await q(gd, "apps.open", app_id=app)
    combo = next(c for c in _blocks(page, "chart") if c["kind"] == "combo")
    assert set(combo["pick"]["values"]) == {"TV", "LD"} and set(combo["labels"]) == {"Tư vấn", "Lắp đặt"}   # giá trị GỐC của chiều, không phải nhãn hiển thị
    code = combo["pick"]["values"][0]
    narrowed = await q(gd, "apps.open", app_id=app, dong=code)           # bấm cột đầu ⇒ app nhận tham số = đúng mã dòng dịch vụ
    total = {k["label"]: k["value"] for b in _blocks(page, "kpis") for k in b["items"]}
    part = {k["label"]: k["value"] for b in _blocks(narrowed, "kpis") for k in b["items"]}
    assert 0 < part["Giá trị"] < total["Giá trị"]


async def test_filter_param_must_be_a_select_param_of_the_app(who: Who) -> None:
    gd = await who("giamdoc")
    app = await _app(gd, "Thiếu tham số", [], params=PARAMS)
    r = await executor.run(gd, "apps.add_block", {"app_id": app, "block": {"kind": "chart", "source": SRC, "filter_param": "khong_co"}})
    assert r.status == "invalid" and "khong_co" in r.error
    r2 = await executor.run(gd, "apps.add_block", {"app_id": app, "block": {"kind": "chart", "chart": "combo", "source": SRC, "measure": "value"}})
    assert r2.status == "invalid" and "measure2" in r2.error                                  # combo cần chỉ số thứ hai


async def test_side_filters_reach_the_menu_and_layout_reorders_and_resizes_only(who: Who) -> None:
    gd = await who("giamdoc")
    app = await _dashboard(gd)
    m = workspaces.manifest(gd, "", mine=await workspaces.personal(gd))
    screen = next(s for w in m["workspaces"] for s in w["screens"] if s["key"] == f"app-{app}")
    assert screen.get("filters_side") is True
    before = await q(gd, "apps.open", app_id=app)
    titles = [b.get("title") or b["items"][0]["label"] for b in before["blocks"]]
    await ok(gd, "apps.layout", app_id=app, order=[3, 4, 5, 1, 2], widths=[12, 6, 6, 6, 6])
    after = await q(gd, "apps.open", app_id=app)
    got = [b.get("title") or b["items"][0]["label"] for b in after["blocks"]]
    assert got == [titles[2], titles[3], titles[4], titles[0], titles[1]] and [b.get("w") for b in after["blocks"]] == [12, 6, 6, 6, 6]
    for bad in ({"order": [1, 1, 2, 3, 4]}, {"order": [1, 2, 3]}, {"widths": [13, 6, 6, 6, 6]}, {"widths": [6]}):
        r = await executor.run(gd, "apps.layout", {"app_id": app, **bad})
        assert r.status == "invalid", bad


async def test_dataset_measure_written_as_sum_is_understood_and_a_wrong_one_is_refused(who: Who) -> None:
    """Model hay viết "sum:value" cho tập dữ liệu (cách viết của nguồn bảng) ⇒ hiểu là "value"; chỉ số nguồn không tính ⇒ từ chối, không lặng lẽ hiện
    số đếm dưới nhãn "giá trị" (thử thật 11/11: ô "Tổng giá trị" ra 8)."""
    await _orders(who)
    gd = await who("giamdoc")
    app = await _app(gd, "Chỉ số", [{"kind": "kpi", "title": "Tổng giá trị", "source": {**SRC, "by": [], "measures": ["count", "value"]}, "measure": "sum:value"}],
                     params=PARAMS)
    page = await q(gd, "apps.open", app_id=app)
    v = next(k for b in _blocks(page, "kpis") for k in b["items"])
    assert v["label"] == "Tổng giá trị" and v["value"] > 1_000_000   # giá trị (đồng), không phải số đơn
    r = await executor.run(gd, "apps.add_block", {"app_id": app, "block": {"kind": "kpi", "source": {**SRC, "by": [], "measures": ["count"]}, "measure": "fee"}})
    assert r.status == "invalid" and "fee" in r.error
    page2 = await q(gd, "apps.open", app_id=await _dashboard(gd))
    combo = next(c for c in _blocks(page2, "chart") if c["kind"] == "combo")
    assert combo["series"][0]["name"] != combo["title"]                              # chú thích là tên chỉ số, không lặp tiêu đề biểu đồ
