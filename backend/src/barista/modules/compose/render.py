"""XUẤT bản soạn: .docx (python-docx) cho văn bản / tờ giới thiệu, .pptx (python-pptx) cho slide; PDF đổi từ một trong hai bằng LibreOffice
(``kernel/convert.py``). Số trong tệp CHỈ in từ khối số liệu (ảnh chụp kết quả truy vấn) — kèm dòng nguồn + lúc chụp. Bộ nhận diện (màu,
phông, logo, chân trang) là dữ liệu công ty (``compose.brand``)."""

import io
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from barista.api import phone_display, zone
from barista.modules.compose import vn

log = logging.getLogger("barista.compose.render")

DEFAULT_BRAND = {"primary_color": "1F4E79", "font": "Arial", "footer": "", "logo": None}


def fmt(v: Any, f: str, style: str = "", unit: str = "") -> str:
    """Giá trị ô ⇒ chữ in (kiểu Việt). Không tính gì thêm — chỉ định dạng. ``style`` = ``vn`` ⇒ số gọn kiểu Việt ("2,85 tỷ", "48 m²" — ``vn.py``); trống ⇒ đủ từng đồng."""
    if v is None or v == "":
        return "—"
    if f == "phone":   # SĐT lưu E.164 ⇒ chữ theo quốc gia công ty như trên màn (0199)
        return phone_display(str(v))
    if style == vn.STYLE_VN:
        return vn.cell(v, f, unit=unit) if f in ("money", "number", "percent") else fmt(v, f)
    if f in ("money", "number") and isinstance(v, int | float | Decimal | str):
        try:
            d = Decimal(str(v))
        except ArithmeticError:
            return str(v)
        if d == d.to_integral_value():
            return f"{int(d):,}".replace(",", ".")
        return f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").rstrip("0").rstrip(",")
    if f == "percent":
        try:
            return f"{Decimal(str(v)):.1f}".replace(".", ",").removesuffix(",0") + "%"
        except ArithmeticError:
            return str(v)
    if f in ("date", "datetime") and isinstance(v, str) and len(v) >= 10:
        try:
            d = date.fromisoformat(v[:10])
            return d.strftime("%d/%m/%Y") + (f" {v[11:16]}" if f == "datetime" and len(v) >= 16 else "")
        except ValueError:
            return v
    return str(v)


def source_line(data: dict[str, Any]) -> str:
    s = data.get("source") or {}
    at = s.get("at") or ""
    try:
        at = datetime.fromisoformat(at).astimezone(zone()).strftime("%H:%M %d/%m/%Y")
    except ValueError:
        pass
    return f"Nguồn: {s.get('title') or s.get('query', '')} (theo quyền xem của người soạn) — số liệu lúc {at}"


def footnote(blocks: list[dict[str, Any]]) -> str:
    """Chú thích MỘT dòng cho trang / tệp gửi khách thay cho các dòng "Nguồn: …" (xuất bản không mang chữ nội bộ — nguồn chi tiết vẫn ở màn soạn):
    lúc chụp số liệu MỚI NHẤT trong bản. Không có số liệu ⇒ rỗng."""
    stamps = []
    for b in blocks:
        if b["type"] != "data":
            continue
        try:
            stamps.append(datetime.fromisoformat((b.get("data") or {}).get("source", {}).get("at") or ""))
        except ValueError:
            continue
    if not stamps:
        return ""
    at = max(stamps).astimezone(zone()).strftime("%H:%M %d/%m/%Y")
    return f"Thông tin cập nhật lúc {at} — có thể thay đổi; vui lòng liên hệ chuyên viên để xác nhận."


def _rgb(hex6: str) -> tuple[int, int, int]:
    return int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)


# ── Word ──

def _docx_setup(brand: dict[str, Any], margin_cm: float) -> Any:
    """Tài liệu Word trắng: A4, lề, phông + logo (đầu trang) + chân trang theo bộ nhận diện."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Cm(margin_cm)
    normal = doc.styles["Normal"]
    normal.font.name = brand["font"]   # type: ignore[attr-defined]
    normal.font.size = Pt(11)   # type: ignore[attr-defined]
    rpr = normal.element.get_or_add_rPr()   # type: ignore[attr-defined]
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(a), brand["font"])
    if brand.get("logo"):
        try:
            sec.header.paragraphs[0].add_run().add_picture(io.BytesIO(brand["logo"]), height=Cm(1.2))
        except Exception:   # ảnh hỏng ⇒ bỏ logo, tệp vẫn xuất
            pass
    if brand.get("footer"):
        fp = sec.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = fp.add_run(brand["footer"])
        r.font.size = Pt(8)
        r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    return doc


def docx(title: str, kind: str, blocks: list[dict[str, Any]], brand: dict[str, Any], images: dict[str, bytes], lay: dict[str, Any] | None = None) -> bytes:
    """``lay`` có trang mẫu ⇒ bố cục (bìa, cột, ô nổi bật, lưới ảnh, chân — ``layout.sections``); rỗng ⇒ bố cục cũ, xuất đúng như trước."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    from barista.modules.compose import layout

    if (tpl := layout.template_of(lay)) is not None:
        return _docx_layout(title, blocks, brand, images, tpl)
    color = RGBColor(*_rgb(brand["primary_color"]))
    doc = _docx_setup(brand, 1.8 if kind == "brochure" else 2.2)
    h = doc.add_paragraph()
    run = h.add_run(title)
    run.bold = True
    run.font.size = Pt(26 if kind == "brochure" else 18)
    run.font.color.rgb = color
    if kind == "brochure":
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for b in blocks:
        _docx_block(doc, b, color, images)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _shade(cell: Any, hex6: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex6)
    cell._tc.get_or_add_tcPr().append(shd)


def _set_font(run: Any, name: str) -> None:
    """Đặt tên phông cho một đoạn chữ Word (cả bốn vùng chữ). KHÔNG nhúng phông vào tệp: máy có phông thì hiện đúng, máy chưa cài thì Word tự thay phông gần nhất."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run.font.name = name
    rpr = run._r.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(a), name)


def _cell_text(cell: Any, text: str, size: float, rgb: Any, *, bold: bool = False, font: str = "") -> None:
    from docx.shared import Pt

    p = cell.paragraphs[0] if not cell.paragraphs[0].text and len(cell.paragraphs) == 1 and not cell.paragraphs[0].runs else cell.add_paragraph()
    r = p.add_run(text)
    r.bold, r.font.size = bold, Pt(size)
    r.font.color.rgb = rgb
    if font:
        _set_font(r, font)


def _docx_layout(title: str, blocks: list[dict[str, Any]], brand: dict[str, Any], images: dict[str, bytes], tpl: dict[str, Any]) -> bytes:
    """Word theo bố cục: mỗi phần là một bảng không viền (bìa = dải màu, cột = bảng n cột, ô nổi bật = hàng ô tô nhạt, lưới ảnh, chân = dải màu)."""
    from docx.shared import Cm, Pt, RGBColor

    from barista.modules.compose import layout

    primary = brand["primary_color"]
    color = RGBColor(*_rgb(primary))
    on = RGBColor(*_rgb(layout.on_color(primary)))
    margin = float(tpl["margin_cm"])
    width = 21.0 - 2 * margin
    fonts = tpl.get("fonts") or {}   # trang mẫu có bộ phông riêng (editorial): cùng TÊN phông với trang web, không nhúng
    doc = _docx_setup({**brand, "font": fonts["text"]} if fonts.get("text") else brand, margin)
    display = str(fonts.get("display", ""))
    by = {b["id"]: b for b in blocks}
    secs = layout.sections(blocks, tpl, title)
    style, customer = str(tpl.get("style", "")), bool(tpl.get("customer"))   # trang mẫu gửi khách: số kiểu Việt + một dòng chú thích thay cho "Nguồn: …" từng khối
    if not secs or secs[0]["kind"] != "cover":
        h = doc.add_paragraph()
        r = h.add_run(title)
        r.bold, r.font.size = True, Pt(22)
        r.font.color.rgb = color

    def table(rows: int, cols: int, w: float) -> Any:
        t = doc.add_table(rows=rows, cols=cols)
        t.autofit = False
        for row in t.rows:
            for c in row.cells:
                c.width = Cm(w / cols)
        return t

    for s in secs:
        k = s["kind"]
        if k == "cover":
            if s["style"] == "hero" and s["hero"] and (img := images.get(s["hero"])) is not None:
                try:
                    doc.add_paragraph().add_run().add_picture(io.BytesIO(img), width=Cm(width))
                except Exception:   # ảnh hỏng ⇒ chỉ còn dải màu, tệp vẫn xuất
                    log.warning("compose: không chèn được ảnh bìa", exc_info=True)
            t = table(1, 1, width)
            cell = t.cell(0, 0)
            _shade(cell, primary)
            _cell_text(cell, title, 28, on, bold=True, font=display)
            for i in s["text"]:
                _cell_text(cell, by[i]["text"], 13, on)
            doc.add_paragraph()
        elif k == "flow":
            for i in s["ids"]:
                _docx_block(doc, by[i], color, images, width_cm=min(width, 16), style=style, source=not customer, display=display)
        elif k == "columns":
            t = table(1, s["cols"], width)
            for j, col in enumerate(s["columns"]):
                cell = t.cell(0, j)
                for i in col:
                    _docx_block(cell, by[i], color, images, width_cm=width / s["cols"] - 0.6, style=style, source=not customer, display=display)
        elif k == "tiles":
            items, n = s["items"], s["cols"]
            t = table((len(items) + n - 1) // n, n, width)
            for idx, it in enumerate(items):
                cell = t.cell(idx // n, idx % n)
                _shade(cell, layout.tint(primary, 0.88))
                _cell_text(cell, it["value"], 20, color, bold=True, font=display)
                _cell_text(cell, it["label"], 9, RGBColor(0x55, 0x55, 0x5B))
            for i in s["ids"] if not customer else []:
                sp = doc.add_paragraph()
                sr = sp.add_run(source_line(by[i]["data"]))
                sr.font.size, sr.font.color.rgb = Pt(8), RGBColor(0x66, 0x66, 0x66)
        elif k == "grid":
            n = s["cols"]
            t = table((len(s["ids"]) + n - 1) // n, n, width)
            for idx, i in enumerate(s["ids"]):
                _docx_block(t.cell(idx // n, idx % n), by[i], color, images, width_cm=width / n - 0.4)
        elif k == "footer":
            t = table(1, 1, width)
            cell = t.cell(0, 0)
            _shade(cell, primary)
            for i in s["ids"]:
                for line in layout.footer_lines(by[i]):
                    _cell_text(cell, line, 10.5, on)
    if customer and (note := footnote(blocks)):
        np_ = doc.add_paragraph()
        nr = np_.add_run(note)
        nr.font.size, nr.font.color.rgb = Pt(8), RGBColor(0x66, 0x66, 0x66)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def _docx_block(doc: Any, b: dict[str, Any], color: Any, images: dict[str, bytes], *, width_cm: float = 12, style: str = "", source: bool = True, display: str = "") -> None:
    """Một khối ⇒ Word. ``doc`` là tài liệu HOẶC một ô bảng (cùng có add_paragraph / add_table) — bố cục nhiều cột đặt khối vào ô."""
    from docx.enum.text import WD_COLOR_INDEX
    from docx.shared import Cm, Pt, RGBColor

    t = b["type"]
    if t == "heading":
        p = doc.add_paragraph()
        r = p.add_run(b["text"])
        if display:
            _set_font(r, display)
        r.bold = True
        r.font.size = Pt(15 if b.get("level", 1) == 1 else 12.5)
        r.font.color.rgb = color
        p.paragraph_format.space_before = Pt(10)
    elif t == "paragraph":
        p = doc.add_paragraph()
        for part in _split_placeholders(b["text"]):
            r = p.add_run(part)
            if part.startswith("[cần số liệu"):
                r.font.highlight_color = WD_COLOR_INDEX.YELLOW   # chỗ còn thiếu số
        p.paragraph_format.space_after = Pt(6)
    elif t == "quote":
        q = b.get("quote") or {}
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(1)
        p.add_run(f"“{q.get('text', b.get('text', ''))}”")
        c = doc.add_paragraph()
        c.paragraph_format.left_indent = Cm(1)
        cr = c.add_run(f"— {q.get('cite', '')}")
        cr.font.size = Pt(9)
        cr.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    elif t == "image":
        im = b.get("image") or {}
        if (data := images.get(b["id"])) is not None:
            try:
                doc.add_paragraph().add_run().add_picture(io.BytesIO(data), width=Cm(width_cm))
            except Exception:   # ảnh hỏng ⇒ chừa dòng chữ báo thay ảnh, tệp vẫn xuất; log để biết ảnh nào hỏng
                log.warning("compose: không chèn được ảnh vào tài liệu", exc_info=True)
                doc.add_paragraph(f"[ảnh không đọc được: {im.get('filename', '')}]")
        else:
            doc.add_paragraph(f"[ảnh không mở được bằng quyền của bạn: {im.get('filename', '')}]")
        if im.get("caption"):
            cp = doc.add_paragraph()
            cr = cp.add_run(im["caption"])
            cr.font.size = Pt(9)
    elif t == "data":
        _docx_data(doc, b.get("data") or {}, color, style, source)


def _split_placeholders(text: str) -> list[str]:
    import re
    return [p for p in re.split(r"(\[cần số liệu:[^\]]*\])", text) if p]


def _docx_data(doc: Any, data: dict[str, Any], color: Any, style: str = "", source: bool = True) -> None:
    from docx.shared import Pt, RGBColor

    if data.get("title"):
        p = doc.add_paragraph()
        r = p.add_run(data["title"])
        r.bold = True
        r.font.size = Pt(11)
    tiles = vn.tiles(data.get("kpis") or [], fmt, style)
    cols, rows = vn.table(data, fmt, style)
    if tiles:
        tbl = doc.add_table(rows=2, cols=len(tiles))
        tbl.style = "Table Grid"
        for j, k in enumerate(tiles):
            tbl.cell(0, j).text = k["label"]
            cell = tbl.cell(1, j)
            cell.text = ""
            rr = cell.paragraphs[0].add_run(k["value"])
            rr.bold = True
            rr.font.size = Pt(14)
            rr.font.color.rgb = color
    if cols and rows:
        tbl = doc.add_table(rows=1 + len(rows), cols=len(cols))
        tbl.style = "Table Grid"
        for j, c in enumerate(cols):
            cell = tbl.cell(0, j)
            cell.text = ""
            hr = cell.paragraphs[0].add_run(c["label"])
            hr.bold = True
        for i, r in enumerate(rows, 1):
            for j, c in enumerate(cols):
                tbl.cell(i, j).text = r[c["key"]]
    for tx in data.get("texts") or []:
        if tx.get("title"):
            hp = doc.add_paragraph()
            hr = hp.add_run(tx["title"])
            hr.bold = True
        doc.add_paragraph(tx.get("text", ""))
    note = (source_line(data) + (f" · {data['note']}" if data.get("note") else "")) if source else str(data.get("note") or "")
    if note:
        p = doc.add_paragraph()
        sr = p.add_run(note)
        sr.font.size = Pt(8)
        sr.font.color.rgb = RGBColor(0x66, 0x66, 0x66)


# ── PowerPoint ──

def pptx(title: str, blocks: list[dict[str, Any]], brand: dict[str, Any], images: dict[str, bytes]) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Emu, Inches, Pt

    color = RGBColor(*_rgb(brand["primary_color"]))
    gray = RGBColor(0x66, 0x66, 0x66)
    prs = Presentation()
    W, H = Inches(13.333), Inches(7.5)
    prs.slide_width, prs.slide_height = W, H
    blank = prs.slide_layouts[6]
    font = brand["font"]

    def text(slide: Any, x: Any, y: Any, w: Any, h: Any, s: str, size: int, *, bold: bool = False, rgb: Any = None) -> Any:
        box = slide.shapes.add_textbox(x, y, w, h)
        tf = box.text_frame
        tf.word_wrap = True
        r = tf.paragraphs[0].add_run()
        r.text = s
        r.font.size, r.font.bold, r.font.name = Pt(size), bold, font
        if rgb is not None:
            r.font.color.rgb = rgb
        return tf

    def frame(slide: Any) -> None:
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, Inches(0.18))
        bar.fill.solid()
        bar.fill.fore_color.rgb = color
        bar.line.fill.background()
        if brand.get("footer"):
            text(slide, Inches(0.5), H - Inches(0.5), W - Inches(1), Inches(0.35), brand["footer"], 10, rgb=gray)
        if brand.get("logo"):
            try:
                slide.shapes.add_picture(io.BytesIO(brand["logo"]), W - Inches(1.6), Inches(0.35), height=Inches(0.6))
            except Exception:   # logo hỏng / định dạng lạ ⇒ bỏ logo của trang này, tệp vẫn xuất; log để biết ảnh nào hỏng
                log.warning("compose: không chèn được logo vào slide", exc_info=True)

    cover = prs.slides.add_slide(blank)
    frame(cover)
    text(cover, Inches(0.8), Inches(2.6), W - Inches(1.6), Inches(1.5), title, 40, bold=True, rgb=color)

    # gom khối thành slide: mỗi tiêu đề mức 1 mở slide mới; khối số liệu luôn một slide riêng (bảng cần chỗ)
    slides: list[tuple[str, list[dict[str, Any]]]] = []
    for b in blocks:
        if b["type"] == "heading" and b.get("level", 1) == 1:
            slides.append((b["text"], []))
        elif b["type"] == "data":
            head = slides[-1][0] if slides else title
            slides.append((head, [b]))
            slides.append((head, []))
        else:
            if not slides:
                slides.append((title, []))
            slides[-1][1].append(b)
    for head, items in slides:
        if not items:
            continue
        s = prs.slides.add_slide(blank)
        frame(s)
        text(s, Inches(0.6), Inches(0.45), W - Inches(2.6), Inches(0.9), head, 28, bold=True, rgb=color)
        top = Inches(1.5)
        for b in items:
            if b["type"] == "data":
                _pptx_data(s, b.get("data") or {}, top, W, H, color, gray, font)
                break
            if b["type"] == "image" and (data := images.get(b["id"])) is not None:
                try:
                    s.shapes.add_picture(io.BytesIO(data), Inches(0.8), top, height=Inches(3.5))
                    top = Emu(top + Inches(3.7))
                except Exception:   # ảnh hỏng ⇒ bỏ ảnh đó, slide vẫn xuất; log để biết ảnh nào hỏng
                    log.warning("compose: không chèn được ảnh vào slide", exc_info=True)
                continue
            if b["type"] == "quote":
                q = b.get("quote") or {}
                tf = text(s, Inches(0.8), top, W - Inches(1.6), Inches(1.2), f"“{q.get('text', '')}”", 18)
                r = tf.add_paragraph().add_run()
                r.text, r.font.size, r.font.color.rgb = f"— {q.get('cite', '')}", Pt(11), gray
                top = Emu(top + Inches(1.4))
                continue
            s_txt = b.get("text", "")
            size = 20 if b["type"] == "heading" else 18
            bullet = "" if b["type"] == "heading" else "• "
            lines = max(1, len(s_txt) // 90 + 1)
            text(s, Inches(0.8), top, W - Inches(1.6), Inches(0.45 * lines), bullet + s_txt, size, bold=b["type"] == "heading")
            top = Emu(top + Inches(0.5 * lines + 0.1))
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def _pptx_data(slide: Any, data: dict[str, Any], top: Any, W: Any, H: Any, color: Any, gray: Any, font: str) -> None:
    from pptx.util import Emu, Inches, Pt

    kpis = data.get("kpis") or []
    cols = data.get("columns") or []
    rows = (data.get("rows") or [])[:10]
    y = top
    if kpis:
        n = min(len(kpis), 4)
        w = (W - Inches(1.6)) // n
        for j, k in enumerate(kpis[:n]):
            box = slide.shapes.add_textbox(Inches(0.8) + w * j, y, w, Inches(1.3))
            tf = box.text_frame
            r = tf.paragraphs[0].add_run()
            r.text, r.font.size, r.font.bold, r.font.name = fmt(k.get("value"), k.get("format", "number")), Pt(30), True, font
            r.font.color.rgb = color
            r2 = tf.add_paragraph().add_run()
            r2.text, r2.font.size, r2.font.name = str(k.get("label", "")), Pt(13), font
        y = Emu(y + Inches(1.5))
    if cols and rows:
        shape = slide.shapes.add_table(1 + len(rows), len(cols), Inches(0.8), y, W - Inches(1.6), Inches(0.4) * (1 + len(rows)))
        tbl = shape.table
        for j, c in enumerate(cols):
            tbl.cell(0, j).text = c["label"]
        for i, r in enumerate(rows, 1):
            for j, c in enumerate(cols):
                tbl.cell(i, j).text = fmt(r.get(c["key"]), c.get("format", "text"))
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.text_frame.paragraphs:
                    for run in p.runs:
                        run.font.size, run.font.name = Pt(12), font
    box = slide.shapes.add_textbox(Inches(0.8), H - Inches(1.0), W - Inches(1.6), Inches(0.4))
    r = box.text_frame.paragraphs[0].add_run()
    r.text = source_line(data) + (" · chỉ hiện 10 dòng đầu" if len(data.get("rows") or []) > 10 else "")
    r.font.size, r.font.color.rgb = Pt(10), gray
