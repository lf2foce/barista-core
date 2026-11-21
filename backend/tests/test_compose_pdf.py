"""Ấn phẩm nhiều trang + PDF dựng từ HTML + ảnh minh hoạ (ADR 0252). Phần thuần (không DB); các ca cần trình duyệt tự bỏ qua khi máy chưa có."""

import asyncio
import io
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from barista.kernel import convert
from barista.modules.compose import html as htmlpage
from barista.modules.compose import kinds, layout, render, stock

from . import compose_sample as S

needs_chrome = pytest.mark.skipif(convert.chrome_binary() is None, reason="máy chưa có trình duyệt không giao diện")
SAMPLES = {"memo10": S.memo10, "teaser4": S.teaser4}


def _images(blocks: list[dict[str, Any]]) -> dict[str, tuple[bytes, str]]:
    return {b["id"]: got for b in blocks if b["type"] == "image" and (got := stock.read(b["image"]["stock"]))}


def test_stock_manifest_matches_files_is_light_and_has_every_format() -> None:
    doc = json.loads((stock.DIR / "manifest.json").read_text(encoding="utf-8"))
    on_disk = {p.name for p in stock.DIR.glob("*.jpg")}
    assert on_disk == {i["file"] for i in doc["images"]} and 12 <= len(on_disk) <= 16
    for i in doc["images"]:
        size = (stock.DIR / i["file"]).stat().st_size
        assert size <= 270 * 1024 and size == i["bytes"] and i["prompt"] and i["theme"] in doc["themes"]
    assert {i["ratio"] for i in doc["images"]} >= {"16:9", "A4 ngang", "3:4", "1:1", "3:1"}
    assert "AI" in doc["origin"] and doc["model"].startswith("gemini") and "pro" not in doc["model"]


def test_stock_images_are_a_separate_ai_source_not_mixed_with_real_ones() -> None:
    it = stock.pick("bat-dong-san", "16:9", limit=1)[0]
    b = stock.block(it)
    assert stock.is_ai(b) and b["origin"] == "ai" and b["image"]["stock"] == it.id and b["image"]["document_id"] == ""
    assert not stock.is_ai({"type": "image", "origin": "kb", "image": {"document_id": "x"}})
    assert htmlpage.badge(b).count("Ảnh minh hoạ") == 1 and htmlpage.badge({"type": "image", "origin": "kb"}) == ""


@pytest.mark.parametrize(("name", "pages"), [("memo10", 10), ("teaser4", 4)])
def test_multipage_templates_are_data_with_fixed_pages_and_every_block_once(name: str, pages: int) -> None:
    tpl = layout.TEMPLATES[name]
    assert len(tpl["pages"]) == pages and tpl["landscape"]
    title, blocks = SAMPLES[name]()
    secs = layout.sections(blocks, tpl, title)
    assert [s["n"] for s in secs] == list(range(1, pages + 1)) and all(s["of"] == pages for s in secs)
    ids = layout.block_ids(secs)
    assert sorted(ids) == sorted(b["id"] for b in blocks) and len(ids) == len(set(ids))
    assert set(layout.slot_options(tpl)) == {p["key"] for p in tpl["pages"]} | {"footer"}


def test_blocks_without_a_page_follow_the_previous_block_and_empty_pages_are_dropped() -> None:
    tpl = layout.TEMPLATES["memo10"]
    blocks = [{"id": "b1", "type": "paragraph", "text": "a", "slot": "p3"}, {"id": "b2", "type": "paragraph", "text": "b"},
              {"id": "b3", "type": "heading", "text": "c", "slot": "p5"}, {"id": "b4", "type": "paragraph", "text": "d", "slot": "col1"}]
    secs = layout.sections(blocks, tpl, "")
    assert [(s["key"], s["ids"]) for s in secs] == [("p3", ["b1", "b2"]), ("p5", ["b3", "b4"])] and [s["n"] for s in secs] == [1, 2]


def test_the_two_kinds_are_registered_in_the_one_list_with_a_shape_built_from_the_pages() -> None:
    assert kinds.of("memo").templates == ("memo10",) and kinds.of("teaser").templates == ("teaser4",) and "memo" in kinds.OFFERED
    assert kinds.of("memo").formats == ("pdf", "html")   # .docx giữ cho văn bản; mẫu nhiều trang chỉ PDF / web
    shape = kinds.of("memo").shape
    assert "p10" in shape and "CÂU KẾT LUẬN" in shape and "p4" in kinds.of("teaser").shape and "p5" not in kinds.of("teaser").shape
    mig = (Path(__file__).parent.parent / "src/barista/migrations/compose/074_multipage_kinds.sql").read_text(encoding="utf-8")
    assert all(f"'{k}'" in mig for k in kinds.KINDS)   # ràng buộc loại ở DB khớp danh sách loại trong mã


@pytest.mark.parametrize(("name", "pages"), [("memo10", 10), ("teaser4", 4)])
def test_one_html_has_every_page_numbers_only_from_data_blocks_and_no_outside_loads(name: str, pages: int) -> None:
    title, blocks = SAMPLES[name]()
    page = htmlpage.page(title, blocks, S.BRAND, _images(blocks), {"template": name})
    assert page.count('<section class="page') == pages
    assert not re.search(r"(?:src|href)=[\"']https?:", page) and "<script" not in page and "url(http" not in page
    ai = sum(1 for b in blocks if stock.is_ai(b))
    assert page.count('class="illus"') == ai > 0
    assert "Ước tính" in page and "Nguồn: số liệu công ty" in page
    assert "Quy trình" not in page   # tiêu đề trang là câu kết luận do khối viết, không nhãn cố định của mẫu
    only = htmlpage.page(title, blocks, S.BRAND, _images(blocks), {"template": name}, only=2)
    assert only.count('<section class="page') == 1 and "aria-label=\"Trang 2\"" in only


def test_every_template_prints_the_illustration_label_on_every_ai_image() -> None:
    it = stock.pick("bat-dong-san", "16:9", limit=1)[0]
    got = stock.read(it.id)
    assert got is not None
    img = {"b2": got}
    for key in ("plain", "brochure", "proposal", "editorial", "flyer"):
        blocks = [{"id": "b1", "type": "heading", "text": "Tiêu đề", "level": 1, "slot": "cover" if key != "plain" else ""},
                  {**stock.block(it), "id": "b2", "slot": "cover" if key != "plain" else "", "flags": []},
                  {**stock.block(it), "id": "b3", "slot": "grid" if key != "plain" else "", "flags": []}]
        page = htmlpage.page("Mẫu", blocks, S.BRAND, {**img, "b3": img["b2"]}, {"template": key})
        on_page = page.count(htmlpage.data_url(img["b2"]))   # có mẫu bỏ ảnh không có chỗ (bìa dải màu) — ảnh nào CÓ trên trang thì phải có nhãn
        assert on_page >= 1 and page.count('class="illus"') == on_page, key


def test_docx_prints_the_label_and_the_export_check_catches_a_missing_one() -> None:
    from docx import Document

    it = stock.pick("bat-dong-san", "16:9", limit=1)[0]
    blocks = [{"id": "b1", "type": "heading", "text": "Tiêu đề", "level": 1, "flags": []}, {**stock.block(it), "id": "b2", "flags": []}]
    data = render.docx("Mẫu", "flyer", blocks, S.BRAND, {"b2": stock.read(it.id)[0]}, {})   # type: ignore[index]
    render.assert_illustrations(data, 1)
    with pytest.raises(ValueError, match="thiếu"):
        render.assert_illustrations(data, 2)
    plain = io.BytesIO()
    Document().save(plain)
    with pytest.raises(ValueError, match="thiếu"):
        render.assert_illustrations(plain.getvalue(), 1)


def test_the_browser_runner_seals_the_page_and_fails_loudly_without_a_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "default-src 'none'" in convert._sealed("<html><head><title>x</title></head></html>")
    with pytest.raises(convert.Unavailable):
        convert._sealed("<html><body>không có head</body></html>")
    monkeypatch.setattr(convert, "chrome_binary", lambda: None)
    with pytest.raises(convert.Unavailable, match="Chromium"):
        asyncio.run(convert.html_to_pdf("<html><head></head></html>"))


@needs_chrome
@pytest.mark.parametrize(("name", "pages"), [("memo10", 10), ("teaser4", 4)])
def test_pdf_is_printed_from_the_same_html_a4_landscape_one_sheet_per_page(name: str, pages: int, tmp_path: Path) -> None:
    from pypdf import PdfReader

    title, blocks = SAMPLES[name]()
    pdf = asyncio.run(convert.html_to_pdf(htmlpage.page(title, blocks, S.BRAND, _images(blocks), {"template": name})))
    r = PdfReader(io.BytesIO(pdf))
    assert len(r.pages) == pages
    w, h = float(r.pages[0].mediabox.width), float(r.pages[0].mediabox.height)
    assert abs(w - 841.9) < 2 and abs(h - 595.3) < 2
    assert len(pdf) < 6 * 1024 * 1024   # ảnh JPEG đi vào PDF nguyên bản — không phình
    text = " ".join(p.extract_text() for p in r.pages)
    assert "Ảnh minh hoạ" in text and title in text


@needs_chrome
def test_the_browser_cannot_reach_the_network_even_for_a_page_that_asks() -> None:
    hits: list[str] = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/x.png"
        page = f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>@page{{size:A4}}body{{background:url({url})}}</style></head><body><img src="{url}"><script>fetch("{url}")</script></body></html>'
        assert asyncio.run(convert.html_to_pdf(page)).startswith(b"%PDF")
    finally:
        srv.shutdown()
    assert hits == []


@needs_chrome
def test_one_page_image_for_the_self_check_step() -> None:
    title, blocks = S.teaser4()
    png = asyncio.run(convert.html_to_png(htmlpage.page(title, blocks, S.BRAND, _images(blocks), {"template": "teaser4"}, only=2), 1123, 794))
    assert png.startswith(b"\x89PNG") and len(png) > 5000


def test_the_same_illustration_used_twice_counts_each_appearance_and_a_missing_label_is_caught() -> None:
    from barista.modules import compose

    it = stock.pick("bat-dong-san", "16:9", limit=1)[0]
    got = stock.read(it.id)
    assert got is not None
    blocks = [{"id": "b1", "type": "heading", "text": "Tiêu đề", "level": 1, "slot": "cover", "flags": []}, {**stock.block(it), "id": "b2", "slot": "cover", "flags": []},
              {**stock.block(it), "id": "b3", "slot": "grid", "flags": []}, {**stock.block(it), "id": "b4", "slot": "grid", "flags": []}]
    images = {"b2": got, "b3": got, "b4": got}
    page = htmlpage.page("Mẫu", blocks, S.BRAND, images, {"template": "editorial"})
    n = compose.ai_appearances(page, blocks, images)
    assert n == 3 and page.count('class="illus"') == 3        # một ảnh nguồn, ba lần xuất hiện, đủ nhãn ⇒ không báo động giả
    stripped = page.replace('<span class="illus">Ảnh minh hoạ</span>', "", 1)
    assert stripped.count('class="illus"') < compose.ai_appearances(stripped, blocks, images)   # thiếu một nhãn thật ⇒ bị chặn


@needs_chrome
@pytest.mark.parametrize("name", ["memo10", "teaser4"])
def test_the_normal_samples_fit_every_page(name: str) -> None:
    from barista.modules.compose import paged

    title, blocks = SAMPLES[name]()
    assert asyncio.run(paged.overflow(htmlpage.page(title, blocks, S.BRAND, _images(blocks), {"template": name}))) == []


@needs_chrome
def test_a_long_page_is_reported_as_overflowing_and_the_export_guard_blocks_it() -> None:
    from barista.api import Invalid
    from barista.modules import compose
    from barista.modules.compose import paged

    title, blocks = S.memo10()
    long = " ".join(["Đoạn rất dài làm tràn khung trang hai của hồ sơ."] * 40)
    blocks = [*blocks[:5], {"id": "b90", "type": "paragraph", "text": long, "level": 1, "origin": "ai", "flags": [], "slot": "p2"}, *blocks[5:]]
    page = htmlpage.page(title, blocks, S.BRAND, _images(blocks), {"template": "memo10"})
    assert asyncio.run(paged.overflow(page)) == [2]
    with pytest.raises(Invalid, match=r"Trang 2 tràn nội dung.*rút gọn"):
        asyncio.run(compose._guard_overflow(layout.TEMPLATES["memo10"], page))
    asyncio.run(compose._guard_overflow(layout.TEMPLATES["plain"], page))   # mẫu dòng chảy tự ngắt trang ⇒ không đo


@needs_chrome
def test_the_measuring_script_is_the_only_script_allowed_to_run() -> None:
    page = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><script>document.documentElement.setAttribute("data-x","bad")</script></body></html>'
    got = asyncio.run(convert.html_measure(page, 'document.documentElement.setAttribute("data-ok","1")', "data-ok"))
    assert got == "1"
    with pytest.raises(convert.Unavailable):
        asyncio.run(convert.html_measure(page, 'void 0', "data-x"))   # script của trang (không nonce) bị chặn ⇒ không ghi được
