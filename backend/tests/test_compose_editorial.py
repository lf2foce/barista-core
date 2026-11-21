"""Soạn với AI — trang TẠP CHÍ cho tờ giới thiệu (0224 §8): số kiểu Việt chỉ là hiển thị (kiểm số vẫn so giá trị gốc), bố cục `editorial`, trang gửi khách không mang chữ nội bộ,
thanh mốc thanh toán, chú thích cập nhật. Phần thuần (không DB) — thứ tự ảnh / quyền ảnh ở ``tests/standin/test_compose_brochure.py``."""

import base64
import io
from datetime import date
from typing import Any

import pytest

from barista.kernel.text import vnd_text
from barista.modules import compose
from barista.modules.compose import check, editorial, kinds, layout, render, vn
from barista.modules.compose import html as htmlpage

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
BRAND: dict[str, Any] = {"primary_color": "1F4E79", "font": "Arial", "footer": "Công ty Sàn Mẫu · sanmau.example", "logo": None, "logo_type": ""}
SRC = {"query": "x.q", "title": "Thông tin dự án (cho tờ giới thiệu)", "input": {}, "at": "2026-11-14T10:05:00+07:00"}


@pytest.mark.parametrize(("value", "text"), [
    (2_850_000_000, "2,85 tỷ"), (3_010_000_000, "3,01 tỷ"), (6_000_000_000, "6 tỷ"), (1_000_000_000, "1 tỷ"), (12_345_000_000, "12,35 tỷ"),
    (850_000_000, "850 triệu"), (1_500_000, "1,5 triệu"), (999_600_000, "1 tỷ"), (125_000, "125.000 đ"), (0, "0 đ"), ("2850000000", "2,85 tỷ"), (None, "—"),
])
def test_vietnamese_money_is_short_and_exact_to_two_decimals_of_a_billion(value: Any, text: str) -> None:
    assert vn.money(value) == text


def test_money_agrees_with_the_core_helper_wherever_both_apply() -> None:
    for n in (2_850_000_000, 4_500_000_000, 90_000_000, 125_000, 1_000_000_000):
        assert vn.money(n) == vnd_text(n)


@pytest.mark.parametrize(("lo", "hi", "text"), [
    (2_850_000_000, 3_010_000_000, "2,85 – 3,01 tỷ"), (850_000_000, 1_200_000_000, "850 triệu – 1,2 tỷ"), (850_000_000, 990_000_000, "850 – 990 triệu"),
    (2_850_000_000, 2_850_000_000, "2,85 tỷ"), (2_850_000_000, None, "2,85 tỷ"), (125_000, 200_000, "125.000 – 200.000 đ"),
])
def test_money_ranges_state_the_unit_once_and_collapse_equal_ends(lo: Any, hi: Any, text: str) -> None:
    assert vn.money_range(lo, hi) == text


def test_areas_percentages_and_equal_area_ends() -> None:
    assert vn.number(48, "m²") == "48 m²" and vn.number(48.5, "m²") == "48,5 m²" and vn.number(1234.5) == "1.234,5"
    assert vn.number_range(45, 62, "m²") == "45 – 62 m²" and vn.number_range(48, 48.0, "m²") == "48 m²" and vn.number_range(69.5, 72, "m²") == "69,5 – 72 m²"
    assert vn.percent(5) == "5%" and vn.percent(12.5) == "12,5%" and vn.percent("30") == "30%"


def _ranges() -> dict[str, Any]:
    return {"title": "", "note": "", "total_rows": 2, "kpis": [], "source": SRC,
            "columns": [{"key": "kind", "label": "Loại căn", "format": "text"}, {"key": "n", "label": "Số căn còn", "format": "number"},
                        {"key": "a0", "label": "Diện tích từ (m²)", "format": "number", "range_to": "a1", "range_label": "Diện tích", "unit": "m²"},
                        {"key": "a1", "label": "Đến (m²)", "format": "number"},
                        {"key": "p0", "label": "Giá từ", "format": "money", "range_to": "p1", "range_label": "Giá"}, {"key": "p1", "label": "Đến", "format": "money"}],
            "rows": [{"kind": "1 phòng ngủ", "n": 54, "a0": 48.0, "a1": 48.0, "p0": 2_850_000_000, "p1": 3_010_000_000},
                     {"kind": "2 phòng ngủ", "n": 113, "a0": 69.5, "a1": 72.0, "p0": 4_130_000_000, "p1": 4_130_000_000}]}


def test_the_table_view_merges_from_to_columns_and_keeps_the_underlying_values() -> None:
    d = _ranges()
    cols, rows = vn.table(d, render.fmt, vn.STYLE_VN)
    assert [c["label"] for c in cols] == ["Loại căn", "Số căn còn", "Diện tích", "Giá"]
    assert rows[0] == {"kind": "1 phòng ngủ", "n": "54", "a0": "48 m²", "p0": "2,85 – 3,01 tỷ"} and rows[1]["p0"] == "4,13 tỷ" and rows[1]["a0"] == "69,5 – 72 m²"
    assert d["rows"][0]["p0"] == 2_850_000_000 and d["rows"][0]["p1"] == 3_010_000_000                          # dữ liệu gốc không bị chạm
    plain_cols, plain_rows = vn.table(d, render.fmt, "")                                                         # mẫu cũ / nội bộ: in đủ từng đồng, không gộp cột
    assert len(plain_cols) == 6 and plain_rows[0]["p0"] == "2.850.000.000"


def test_the_number_check_still_validates_against_the_underlying_values_not_the_display() -> None:
    d = _ranges()
    values = check.values_of(d)
    today = date(2026, 11, 14)
    assert check.unmatched("Từ 2,85 tỷ đến 3,01 tỷ, diện tích 48 m², 113 căn 2 phòng ngủ", values, set(), today) == []     # chữ viết theo kiểu Việt khớp giá trị gốc
    assert check.unmatched("Giá chỉ từ 9,99 tỷ", values, set(), today) == ["9,99 tỷ"]                                        # số không có trong dữ liệu vẫn bị cờ
    para = {"id": "b2", "type": "paragraph", "text": "Giá từ 2,85 tỷ, căn lớn nhất 7,77 tỷ", "origin": "template", "flags": []}
    from barista.modules import compose
    out = compose.flag_numbers([{"id": "b1", "type": "data", "origin": "data", "flags": [], "data": d}, para])
    assert len(out[1]["flags"]) == 1 and "7,77 tỷ" in out[1]["flags"][0]


def test_highlight_pairs_merge_and_the_project_name_is_not_repeated() -> None:
    kpis = [{"label": "Dự án", "value": "Hồ Sen", "format": "text"}, {"label": "Chủ đầu tư", "value": "CĐT Mẫu", "format": "text"},
            {"label": "Căn còn bán", "value": 222, "format": "number"},
            {"label": "Giá từ", "value": 2_850_000_000, "format": "money", "range_to": "Giá đến", "range_label": "Giá"},
            {"label": "Giá đến", "value": 6_670_000_000, "format": "money"}]
    assert vn.tiles(kpis, render.fmt, vn.STYLE_VN, skip_values=("Hồ Sen",)) == [
        {"label": "Chủ đầu tư", "value": "CĐT Mẫu"}, {"label": "Căn còn bán", "value": "222"}, {"label": "Giá", "value": "2,85 – 6,67 tỷ"}]


def _plan_data() -> dict[str, Any]:
    return {"title": "", "note": "", "kpis": [], "total_rows": 3, "source": SRC,
            "columns": [{"key": "label", "label": "Đợt", "format": "text"}, {"key": "after", "label": "Sau (ngày)", "format": "number"},
                        {"key": "pct", "label": "Tỷ lệ", "format": "percent"}],
            "rows": [{"label": "Đặt cọc", "after": 0, "pct": 5}, {"label": "Ký HĐMB", "after": 30, "pct": 25}, {"label": "Bàn giao", "after": 365, "pct": 70}]}


def test_a_payment_schedule_becomes_a_milestone_bar_other_tables_do_not() -> None:
    plan = vn.plan(_plan_data())
    assert plan is not None and [p["text"] for p in plan] == ["5%", "25%", "70%"] and plan[1]["after"] == "Sau 30 ngày" and plan[0]["after"] == ""
    assert vn.plan(_ranges()) is None and vn.plan({**_plan_data(), "rows": _plan_data()["rows"][:1]}) is None                  # không phải lịch đợt / quá ít dòng


def _blocks() -> list[dict[str, Any]]:
    def data(bid: str, **kw: Any) -> dict[str, Any]:
        return {"id": bid, "type": "data", "origin": "data", "flags": [], "data": {"title": "", "note": "", "kpis": [], "rows": [], "columns": [], "source": SRC, **kw},
                **({"slot": kw.pop("slot")} if "slot" in kw else {})}
    img = {"type": "image", "origin": "kb", "flags": [], "image": {"document_id": "d", "filename": "a.png", "caption": "Phối cảnh"}}
    return [
        {**img, "id": "b1", "slot": "cover"}, {"id": "b2", "type": "paragraph", "text": "Quận Mẫu · CĐT Mẫu", "origin": "template", "flags": [], "slot": "cover"},
        {**data("b3", kpis=[{"label": "Chủ đầu tư", "value": "CĐT Mẫu", "format": "text"}, {"label": "Giá từ", "value": 2_850_000_000, "format": "money",
                                                                                          "range_to": "Giá đến", "range_label": "Giá"},
                            {"label": "Giá đến", "value": 3_010_000_000, "format": "money"}]), "slot": "tiles"},
        {"id": "b4", "type": "heading", "text": "Căn còn bán và khoảng giá", "level": 1, "origin": "template", "flags": []},
        data("b5", **{k: v for k, v in _ranges().items() if k in ("columns", "rows", "total_rows")}),
        {"id": "b6", "type": "heading", "text": "Tiến độ thanh toán", "level": 2, "origin": "template", "flags": [], "slot": "col1"},
        {**data("b7", **{k: v for k, v in _plan_data().items() if k in ("columns", "rows", "total_rows")}), "slot": "col1"},
        {**img, "id": "b8", "slot": "grid"}, {**img, "id": "b9", "slot": "grid", "image": {"document_id": "e", "filename": "b.png", "caption": ""}},
        {"id": "b10", "type": "paragraph", "text": "Liên hệ chuyên viên để được tư vấn", "origin": "template", "flags": [], "slot": "footer"},
        {**data("b11", kpis=[{"label": "Chuyên viên tư vấn", "value": "Nguyễn Văn An", "format": "text"}, {"label": "Điện thoại", "value": "0101 000 001", "format": "text"},
                             {"label": "Email", "value": "an@sanmau.example", "format": "text"}]), "slot": "footer"},
    ]


def test_editorial_is_data_default_for_brochures_and_customer_facing() -> None:
    t = layout.TEMPLATES["editorial"]
    assert kinds.template_for("brochure") == "editorial" and t["style"] == vn.STYLE_VN and t["customer"] and t["look"] == "editorial" and t["cover"] == "hero"
    assert {x["key"] for x in kinds.templates("brochure")} == {"brochure", "proposal", "editorial"}
    secs = layout.sections(_blocks(), t, "Hồ Sen")
    assert [s["kind"] for s in secs] == ["cover", "tiles", "flow", "columns", "grid", "footer"]
    assert sorted(layout.block_ids(secs)) == sorted(b["id"] for b in _blocks())                                   # vẫn mỗi khối đúng một lần
    tiles = secs[1]
    assert tiles["title"] == "Điểm nổi bật" and [(i["label"], i["value"]) for i in tiles["items"]] == [("Chủ đầu tư", "CĐT Mẫu"), ("Giá", "2,85 – 3,01 tỷ")]
    assert secs[4]["title"] == "Hình ảnh" and secs[5]["title"] == "Liên hệ"


def test_editorial_page_has_the_premium_parts_and_no_internal_wording() -> None:
    page = htmlpage.page("Hồ Sen Residence", _blocks(), BRAND, {"b1": (PNG, "image/png"), "b8": (PNG, "image/png"), "b9": (PNG, "image/png")}, {"template": "editorial"})
    assert page.startswith("<!DOCTYPE html>") and 'lang="vi"' in page and "<script" not in page and "http://" not in page and "https://" not in page
    assert '<header class="cover"' in page and "<h1>Hồ Sen Residence</h1>" in page and "Quận Mẫu · CĐT Mẫu" in page                       # bìa ảnh + tên dự án + vị trí · chủ đầu tư
    assert 'class="strip"' in page and "Điểm nổi bật" in page and "2,85 – 3,01 tỷ" in page and "2.850.000.000" not in page                  # dải điểm nổi bật, số kiểu Việt
    assert "<th scope=\"col\" class=num>Giá</th>" in page and "69,5 – 72 m²" in page and "4,13 tỷ" in page                                          # bảng thoáng, khoảng gộp
    assert 'class="plan"' in page and 'role="img"' in page and "flex:5 1 0" in page and "Sau 365 ngày" in page                           # lịch thanh toán = thanh mốc, không bảng
    assert page.count('class="plan"') == 1 and ">Tỷ lệ</th>" not in page
    assert 'class="mosaic"' in page and "<figcaption>Phối cảnh</figcaption>" in page                                                      # lưới ảnh có chú thích
    assert 'class="contact"' in page and "Nguyễn Văn An" in page and 'href="mailto:an@sanmau.example"' in page and 'href="tel:0101000001"' in page   # thẻ liên hệ
    assert "@page{size:A4" in page and "break-before:page" in page and "tr{break-inside:avoid}" in page                                  # in A4, ngắt trang, không cắt dòng bảng
    assert "@media(max-width:640px)" in page and "width=device-width" in page                                                            # điện thoại
    for internal in ("Nguồn:", "theo quyền xem", "cho tờ giới thiệu", "Tờ giới thiệu", "Lập ngày", "Cần ảnh"):
        assert internal not in page
    assert page.count("Thông tin cập nhật lúc 10:05 14/11/2026 — có thể thay đổi; vui lòng liên hệ chuyên viên để xác nhận.") == 1       # MỘT chú thích duy nhất


def test_editorial_without_a_cover_image_still_has_a_solid_cover_and_palette_comes_from_the_brand() -> None:
    page = htmlpage.page("Dự án Mẫu", _blocks(), {**BRAND, "primary_color": "FFE27A"}, {}, {"template": "editorial"})
    assert 'class="cover plainbg"' in page and "<img class=\"bg\"" not in page and "Dự án Mẫu" in page
    deep, accent = layout.shade("FFE27A", 0.45), layout.readable_on_white("FFE27A")
    assert f"--deep:#{deep}" in page and f"--accent:#{accent}" in page and accent != "FFE27A"                          # màu nhạt ⇒ màu nhấn được đậm lên cho đọc được
    assert page.count("@font-face") == 3 and page.count("data:font/woff2;base64,") == 3 and "url(http" not in page and "fonts.googleapis" not in page   # phông NHÚNG, không tải ngoài


def test_editorial_word_uses_vietnamese_numbers_and_one_footnote_instead_of_source_lines() -> None:
    from docx import Document

    raw = render.docx("Hồ Sen Residence", "brochure", _blocks(), BRAND, {"b1": PNG, "b8": PNG, "b9": PNG}, {"template": "editorial"})
    doc = Document(io.BytesIO(raw))
    text = "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells) + "\n".join(p.text for p in doc.paragraphs)
    assert "2,85 – 3,01 tỷ" in text and "69,5 – 72 m²" in text and "2.850.000.000" not in text
    assert "Nguồn:" not in text and "theo quyền xem" not in text and text.count("Thông tin cập nhật lúc 10:05 14/11/2026") == 1


def test_a_plain_internal_document_keeps_exact_money_and_source_lines() -> None:
    page = htmlpage.page("Báo cáo", [_blocks()[4]], BRAND, {}, {"template": "plain"})
    assert "Nguồn:" in page and "theo quyền xem của người soạn" in page            # mẫu Văn bản = nội bộ: nguồn đầy đủ (chữ nội bộ chỉ ở nơi nội bộ)
    assert render.fmt(2_850_000_000, "money") == "2.850.000.000" and render.fmt(2_850_000_000, "money", vn.STYLE_VN) == "2,85 tỷ"


def test_embedded_fonts_are_vendored_with_their_licence_and_stay_small() -> None:
    sizes = {}
    for _fam, f, w in editorial.FONTS:
        raw = (editorial.FONT_DIR / f).read_bytes()
        assert raw[:4] == b"wOF2" and w in (400, 500, 600)                                                       # woff2 thật, tối đa 3 độ đậm
        sizes[f] = len(raw)
    assert len(editorial.FONTS) == 3 and sum(sizes.values()) < 100_000                                           # cắt còn Latin + tiếng Việt
    for lic in ("OFL-CormorantGaramond.txt", "OFL-BeVietnamPro.txt"):
        text = (editorial.FONT_DIR / lic).read_text()
        assert "SIL OPEN FONT LICENSE Version 1.1" in text and "Copyright" in text
    assert editorial.SERIF.startswith('"Cormorant Garamond"') and editorial.SANS.startswith('"Be Vietnam Pro"')   # có phông thay thế hệ thống phía sau


def test_the_share_page_csp_allows_data_fonts_and_nothing_else_new() -> None:
    csp = compose.PUBLIC_HEADERS["Content-Security-Policy"]
    assert "font-src data:" in csp and "default-src 'none'" in csp and "script-src" not in csp and "connect-src" not in csp
    assert "http" not in csp.replace("https", "")


def test_word_names_the_same_fonts_without_embedding_them() -> None:
    import zipfile

    from docx import Document

    raw = render.docx("Hồ Sen Residence", "brochure", _blocks(), BRAND, {"b1": PNG}, {"template": "editorial"})
    xml = zipfile.ZipFile(io.BytesIO(raw)).read("word/document.xml").decode()
    assert "Cormorant Garamond" in xml                                                                           # tiêu đề / số lớn
    styles = zipfile.ZipFile(io.BytesIO(raw)).read("word/styles.xml").decode()
    assert "Be Vietnam Pro" in styles and not [n for n in zipfile.ZipFile(io.BytesIO(raw)).namelist() if "font" in n.lower() and n.endswith((".odttf", ".ttf"))]   # chỉ đặt tên, không nhúng
    assert Document(io.BytesIO(raw)).paragraphs is not None
    old = zipfile.ZipFile(io.BytesIO(render.docx("Bản cũ", "brochure", _blocks()[2:3], BRAND, {}))).read("word/styles.xml").decode()
    assert "Be Vietnam Pro" not in old and "Arial" in old                                                        # mẫu khác: giữ phông của bộ nhận diện
