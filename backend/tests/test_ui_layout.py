"""Hợp đồng thẻ (0166): chia cột theo ``w`` và biểu đồ ``combo`` — chỉ THÊM trường, đầu ra cũ không đổi một chữ."""

from typing import Any

import pytest

from barista.kernel import ui


def test_old_chart_output_is_unchanged() -> None:
    """Chuỗi không khai as / axis ⇒ không có khoá mới; biểu đồ không combo không có unit_right (client cũ đọc y như trước)."""
    assert ui.line("A", [1, None]) == {"name": "A", "values": [1, None], "tone": "blue"}
    assert ui.b_chart("bar", ["x", "y"], [ui.line("A", [1, 2])], title="t") == {
        "type": "chart", "title": "t", "kind": "bar", "unit": "count", "labels": ["x", "y"], "series": [{"name": "A", "values": [1, 2], "tone": "blue"}], "note": ""}


def test_combo_declares_bar_and_line_on_two_axes() -> None:
    b = ui.b_chart("combo", ["T1", "T2"], [ui.line("Giá trị", [10, 20], "blue", as_="bar"), ui.line("Tỉ lệ", [5.5, None], "orange", as_="line", axis="right")],
                   title="GMV và tỉ lệ", unit="vnd", unit_right="pct")
    assert b["kind"] == "combo" and b["unit_right"] == "pct"
    assert [(s.get("as"), s.get("axis")) for s in b["series"]] == [("bar", None), ("line", "right")]
    assert b["series"][1]["values"] == [5.5, None]   # null = không đo được, giữ nguyên


@pytest.mark.parametrize("bad", [dict(as_="pie"), dict(axis="top")])
def test_combo_rejects_unknown_series_style(bad: dict[str, Any]) -> None:
    with pytest.raises(AssertionError):
        ui.b_chart("combo", ["x"], [ui.line("A", [1], **bad)])


def test_as_axis_and_unit_right_are_for_combo_only() -> None:
    with pytest.raises(AssertionError):
        ui.b_chart("bar", ["x"], [ui.line("A", [1], as_="line")])
    with pytest.raises(AssertionError):
        ui.b_chart("bar", ["x"], [ui.line("A", [1])], unit_right="pct")


def test_series_length_must_match_labels() -> None:
    with pytest.raises(AssertionError):
        ui.b_chart("combo", ["x", "y"], [ui.line("A", [1], as_="bar")])


def test_span_adds_width_without_touching_the_block() -> None:
    k = ui.b_kpis([ui.kpi("A", 1)])
    s = ui.span(k, 4)
    assert s["w"] == 4 and {**s, "w": None}.keys() == {**k, "w": None}.keys() and "w" not in k
    for w in (0, 13):
        with pytest.raises(AssertionError):
            ui.span(k, w)


def test_compact_keeps_the_width_of_blocks_it_keeps() -> None:
    page = ui.card("t", [ui.span(ui.b_kpis([ui.kpi("A", 1)]), 6), ui.span(ui.b_chart("bar", ["x"], [ui.line("y", [1])]), 6)])
    out = ui.compact(page)
    assert out is not None and [b.get("w") for b in out["blocks"] if b["type"] == "kpis"] == [6]


def test_pick_is_for_cross_filter_and_must_match_the_labels() -> None:
    b = ui.b_chart("bar", ["A", "B"], [ui.line("s", [1, 2])], pick=("branch", ["a", "b"]))
    assert b["pick"] == {"param": "branch", "values": ["a", "b"]}
    assert "pick" not in ui.b_chart("bar", ["A"], [ui.line("s", [1])])        # không khai ⇒ không có khoá (đầu ra cũ không đổi)
    with pytest.raises(AssertionError):
        ui.b_chart("bar", ["A", "B"], [ui.line("s", [1, 2])], pick=("branch", ["a"]))


def test_scatter_needs_x_per_label_and_treemap_is_a_plain_kind() -> None:
    sc = ui.b_chart("scatter", ["A", "B"], [ui.line("y", [1, 2], x=[10, 20])])
    assert sc["series"][0]["x"] == [10, 20]
    with pytest.raises(AssertionError):
        ui.b_chart("scatter", ["A", "B"], [ui.line("y", [1, 2])])                  # scatter không có x
    with pytest.raises(AssertionError):
        ui.b_chart("bar", ["A", "B"], [ui.line("y", [1, 2], x=[10, 20])])          # x chỉ cho scatter
    with pytest.raises(AssertionError):
        ui.b_chart("scatter", ["A", "B"], [ui.line("y", [1, 2], x=[10])])          # lệch độ dài
    assert ui.b_chart("treemap", ["A"], [ui.line("v", [5])])["kind"] == "treemap"
