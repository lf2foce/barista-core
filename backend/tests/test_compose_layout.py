"""Soạn với AI — BỐ CỤC tờ giới thiệu (0224): trang mẫu là dữ liệu, khối chọn chỗ đứng bằng ``slot`` (chỉ THÊM trường), kiểm số áp mọi chỗ đứng như nhau,
xuất HTML một tệp + Word theo bố cục không vỡ. Phần thuần (không DB) — phần có quyền / link / công thức ở ``tests/standin/test_compose_brochure.py``."""

import base64
import io
import re
from datetime import date
from typing import Any

from barista.modules import compose
from barista.modules.compose import html as htmlpage
from barista.modules.compose import layout, render

# ảnh PNG 1×1 thật (đủ để python-docx / Pillow mở)
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
BRAND: dict[str, Any] = {"primary_color": "1F4E79", "font": "Arial", "footer": "Công ty Sàn Mẫu · sanmau.example", "logo": None, "logo_type": ""}


def _data(bid: str, kpis: list[dict[str, Any]] | None = None, rows: list[dict[str, Any]] | None = None, slot: str = "", **extra: Any) -> dict[str, Any]:
    cols = [{"key": "code", "label": "Mã căn", "format": "text"}, {"key": "price", "label": "Giá", "format": "money"}] if rows else []
    d = {"title": "Số liệu", "columns": cols, "rows": rows or [], "total_rows": len(rows or []), "kpis": kpis or [], "note": "",
         "source": {"query": "x.q", "title": "Nguồn thử", "input": {}, "at": "2026-11-14T10:00:00+07:00"}, **extra}
    return {"id": bid, "type": "data", "origin": "data", "flags": [], "data": d, **({"slot": slot} if slot else {})}


def _text(bid: str, text: str, slot: str = "", origin: str = "ai", t: str = "paragraph") -> dict[str, Any]:
    return {"id": bid, "type": t, "text": text, "level": 1, "origin": origin, "flags": [], **({"slot": slot} if slot else {})}


def _img(bid: str, slot: str = "") -> dict[str, Any]:
    return {"id": bid, "type": "image", "origin": "kb", "flags": [], "image": {"document_id": "d", "filename": f"{bid}.png", "caption": "Phối cảnh"},
            **({"slot": slot} if slot else {})}


def _sample() -> list[dict[str, Any]]:
    return [
        _img("b1", "cover"), _text("b2", "Căn hộ ven sông", "cover", "template"),
        _data("b3", kpis=[{"label": "Căn còn bán", "value": 12, "format": "number"}, {"label": "Giá từ", "value": 3_200_000_000, "format": "money"}], slot="tiles"),
        _text("b4", "Bảng giá", t="heading"),
        _data("b5", rows=[{"code": "A01", "price": 3_200_000_000}, {"code": "A02", "price": 4_100_000_000}]),
        _text("b6", "Tiến độ", "col1", "template", "heading"), _text("b7", "Thanh toán chia đợt", "col1", "template"),
        _text("b8", "Chính sách", "col2", "template", "heading"), _text("b9", "Ưu đãi cho khách đặt sớm", "col2", "template"),
        _img("b10", "grid"), _img("b11", "grid"),
        _text("b12", "Liên hệ ngay hôm nay", "footer", "template"),
    ]


def test_every_block_lands_in_exactly_one_section_in_reading_order() -> None:
    blocks = _sample()
    secs = layout.sections(blocks, layout.TEMPLATES["brochure"])
    assert [s["kind"] for s in secs] == ["cover", "tiles", "flow", "columns", "grid", "footer"]
    assert sorted(layout.block_ids(secs)) == sorted(b["id"] for b in blocks)           # đủ khối, không lặp, không thừa
    cover, tiles, _, cols, grid, foot = secs
    assert cover["hero"] == "b1" and cover["text"] == ["b2"] and cover["style"] == "hero"
    assert [i["value"] for i in tiles["items"]] == ["12", "3.200.000.000"] and tiles["cols"] == 4   # định dạng ở máy chủ — web không tự tính
    assert cols["cols"] == 2 and cols["columns"] == [["b6", "b7"], ["b8", "b9"]]
    assert grid["ids"] == ["b10", "b11"] and foot["ids"] == ["b12"]


def test_column_rows_break_when_the_column_steps_back() -> None:
    blocks = [_text("b1", "A", "col1"), _text("b1x", "A2", "col1"), _text("b2", "B", "col2"), _text("b3", "C", "col3"), _text("b4", "D", "col1"), _text("b5", "E", "col2")]
    secs = layout.sections(blocks, layout.TEMPLATES["plain"])
    assert [(s["kind"], s["cols"], s["columns"]) for s in secs] == [("columns", 3, [["b1", "b1x"], ["b2"], ["b3"]]), ("columns", 2, [["b4"], ["b5"]])]


def test_slots_that_do_not_fit_fall_back_to_flow_and_old_blocks_flow_as_before() -> None:
    table = _data("b1", kpis=[{"label": "n", "value": 1}], rows=[{"code": "A", "price": 1}], slot="tiles")   # có dòng bảng ⇒ không thành ô nổi bật (khỏi giấu bảng)
    stray = _text("b2", "chữ", "grid")                                                                      # chữ đặt vào lưới ảnh ⇒ chảy
    legacy = _text("b3", "khối cũ không có slot")
    junk = {**_text("b4", "slot lạ"), "slot": "không-có"}
    secs = layout.sections([table, stray, legacy, junk], layout.TEMPLATES["plain"])
    assert [s["kind"] for s in secs] == ["flow"] and secs[0]["ids"] == ["b1", "b2", "b3", "b4"]
    assert layout.clean({"template": "brochure"}) == {"template": "brochure"} and layout.clean({"template": "lạ"}) == {} and layout.clean(None) == {}
    assert layout.template_of({}) is None and (layout.template_of({"template": "proposal"}) or {})["cover"] == "band"


def test_number_check_covers_every_layout_slot() -> None:
    """Chữ AI / công thức viết ở bìa, cột, chân đều bị kiểm số như chữ ở dòng chảy — số phải khớp khối số liệu của bản."""
    data = _data("b1", kpis=[{"label": "Căn còn bán", "value": 12, "format": "number"}, {"label": "Giá từ", "value": 3_200_000_000, "format": "money"}], slot="tiles")
    bad = [_text(f"b{i}", "Chỉ còn 987 căn", slot, origin) for i, (slot, origin) in enumerate(
        [("cover", "ai"), ("col1", "ai"), ("footer", "template"), ("", "ai")], start=2)]
    ok_ = [_text("b9", "Còn 12 căn, giá từ 3,2 tỷ", "col2", "ai"), _text("b10", "Số 987 do người sửa tay", "footer", "human")]
    out = compose.flag_numbers([data, *bad, *ok_])
    assert [bool(b["flags"]) for b in out] == [False, True, True, True, True, False, False]
    assert "987" in out[1]["flags"][0]


def test_html_page_is_self_contained_escaped_branded_and_accessible() -> None:
    blocks = [*_sample(), _text("b13", "<script>alert(1)</script> & [cần số liệu: giá bàn giao]", "")]
    page = htmlpage.page("Tờ <b>giới thiệu</b>", blocks, {**BRAND, "logo": PNG, "logo_type": "image/png"}, {"b1": (PNG, "image/png"), "b10": (PNG, "image/png")},
                         {"template": "brochure"})
    assert page.startswith("<!DOCTYPE html><html lang=\"vi\">") and "<script" not in page.lower().replace("&lt;script&gt;", "")
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp;" in page and "Tờ &lt;b&gt;giới thiệu&lt;/b&gt;" in page          # thoát ký tự HTML
    assert "<mark>[cần số liệu: giá bàn giao]</mark>" in page                                                           # chỗ giữ được tô
    assert "--brand:#1F4E79" in page and "Công ty Sàn Mẫu · sanmau.example" in page and 'alt="Logo công ty"' in page
    assert page.count("data:image/png;base64,") >= 3 and "http://" not in page and "https://" not in page          # ảnh + logo nhúng, không tải gì ngoài
    assert "<th scope=\"col\">Mã căn</th>" in page and "3.200.000.000" in page                                       # bảng có tiêu đề cột
    assert "Nguồn thử" not in page and "theo quyền xem" not in page and "Thông tin cập nhật lúc 10:00 14/11/2026" in page   # trang gửi khách: một chú thích, không chữ nội bộ
    assert "Nguồn thử" in htmlpage.page("T", [_data("b3", rows=[{"code": "A", "price": 1}])], BRAND, {}, {"template": "plain"})   # trang nội bộ (mẫu Văn bản) vẫn ghi nguồn
    assert '<main>' in page and 'aria-label="Bìa"' in page and 'class="cover hero"' in page
    assert re.search(r'<img [^>]*alt="[^"]+"', page)                                                                  # ảnh có chữ thay thế
    # ảnh mà người xuất không còn quyền đọc ⇒ không có trong trang (không lộ gì khác)
    assert htmlpage.page("T", [_img("b1", "grid")], BRAND, {}, {"template": "brochure"}).count("<img") == 0


def test_text_contrast_flips_on_light_brand_colours() -> None:
    assert layout.on_color("1F4E79") == "FFFFFF" and layout.on_color("FFE27A") == "111111" and layout.on_color("000000") == "FFFFFF"
    assert layout.tint("000000", 1) == "FFFFFF" and layout.tint("1F4E79", 0) == "1F4E79"


def test_word_with_layout_opens_has_the_cover_tiles_columns_and_old_drafts_are_unchanged() -> None:
    from docx import Document

    blocks = _sample()
    raw = render.docx("Tờ giới thiệu thử", "brochure", blocks, BRAND, {"b1": PNG, "b10": PNG, "b11": PNG}, {"template": "brochure"})
    doc = Document(io.BytesIO(raw))
    text = "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
    assert raw[:2] == b"PK" and "Tờ giới thiệu thử" in text and "Căn hộ ven sông" in text              # bìa là dải màu có tên + dòng phụ
    assert "3.200.000.000" in text and "Căn còn bán" in text and "Liên hệ ngay hôm nay" in text          # ô nổi bật + chân
    assert len(doc.tables) >= 6 and len(doc.inline_shapes) >= 3                                          # bìa · ô số · bảng giá · cột · lưới ảnh · chân; ảnh nhúng
    # không trang mẫu ⇒ đúng bố cục cũ: không bảng bố cục, tiêu đề là một đoạn
    old = Document(io.BytesIO(render.docx("Bản cũ", "brochure", [_text("b1", "Chữ", "", "ai"), _data("b2", rows=[{"code": "A", "price": 1}])], BRAND, {})))
    assert old.paragraphs[0].text == "Bản cũ" and len(old.tables) == 1
    # slot trên khối nhưng bản không có trang mẫu ⇒ vẫn chảy (bản cũ giữ nguyên diện mạo)
    assert len(Document(io.BytesIO(render.docx("Bản cũ", "brochure", [_text("b1", "Chữ", "footer")], BRAND, {}))).tables) == 0


def test_data_texts_travel_verbatim_and_feed_the_number_check() -> None:
    page = {"kind": "detail", "title": "Chính sách", "sections": [{"title": "Chính sách bán hàng", "fields": [], "text": "Chiết khấu 5% khi thanh toán sớm"}]}
    snap = compose.snapshot(page)
    assert snap is not None and snap["texts"] == [{"title": "Chính sách bán hàng", "text": "Chiết khấu 5% khi thanh toán sớm"}]
    out = compose.flag_numbers([{"id": "b1", "type": "data", "origin": "data", "flags": [], "data": snap}, _text("b2", "Chiết khấu 5% cho khách đặt sớm"),
                                _text("b3", "Chiết khấu 9% cho khách đặt sớm")])
    assert [bool(b["flags"]) for b in out] == [False, False, True]
    assert "Chiết khấu 5%" in htmlpage.page("T", [out[0]], BRAND, {}, {"template": "plain"})
    assert compose.check.unmatched("Năm 2026", compose.check.values_of(snap), set(), date(2026, 11, 14)) == []


def test_one_list_kind_decides_templates_and_formats() -> None:
    """0231 / ADR 0249 lát 2: MỘT danh sách loại ấn phẩm (``kinds.py``) quyết trang mẫu + định dạng; mọi mẫu nhắc tới đều có thật; AI không chọn mẫu; mẫu không hợp
    loại (kể cả mẫu gói nêu) về mặc định của loại; ``document`` không còn mời chọn / không còn nhận ở lệnh soạn mới; bộ SLOTS / TEMPLATES lệch của lõi đã bỏ."""
    import typing

    from barista.kernel import brochure as kernel_brochure
    from barista.modules import compose
    from barista.modules.compose import kinds, layout

    assert kinds.OFFERED == ("flyer", "brochure", "slides", "post", "memo", "teaser") and not kinds.KINDS["document"].offered
    for k in kinds.KINDS.values():
        assert k.shape and k.formats and set(k.templates) <= set(layout.TEMPLATES) and set(k.formats) <= set(kinds.FORMATS)
    assert kinds.template_for("flyer") == "flyer" and kinds.template_for("flyer", "editorial") == "flyer"          # mẫu không hợp loại ⇒ mặc định của loại
    assert kinds.template_for("brochure") == "editorial" and kinds.template_for("brochure", "proposal") == "proposal"
    assert kinds.template_for("slides", "brochure") == "" and [t["key"] for t in kinds.templates("flyer")] == ["flyer"]
    assert kinds.of("lạ") is kinds.KINDS["document"] and layout.TEMPLATES["flyer"]["customer"]
    assert "template" not in compose.Draft.model_fields                                                            # AI không chọn mẫu: loại chọn
    assert set(typing.get_args(compose.Start.model_fields["kind"].annotation)) == set(kinds.OFFERED)               # "Văn bản" không còn soạn mới
    assert not hasattr(kernel_brochure, "SLOTS") and not hasattr(kernel_brochure, "TEMPLATES")
    assert layout.TEMPLATES["plain"]["label"] != "Văn bản"
