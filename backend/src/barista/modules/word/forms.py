"""KHUNG THỂ THỨC cho văn bản soạn mới (0251 lát 2 — màn "Soạn"): CODE dựng bố cục, AI chỉ viết chữ vào chỗ của nó.

Văn bản hành chính theo Nghị định 30/2020/NĐ-CP (Phụ lục I): khổ A4, lề trên / dưới 20 mm, trái 30 mm, phải 15 mm; Times New Roman; phần đầu là bảng
KHÔNG VIỀN hai cột (tên cơ quan + số, ký hiệu + trích yếu bên trái · quốc hiệu, tiêu ngữ + địa danh, ngày tháng bên phải); tên loại + trích yếu giữa trang
(văn bản có tên loại); nội dung căn đều hai lề, thụt đầu dòng 1,27 cm, cỡ 14; phần cuối là bảng không viền (nơi nhận bên trái · quyền hạn, chức vụ, họ
tên người ký bên phải). Thư gửi khách không phải văn bản hành chính: đầu thư công ty, kính gửi, nội dung, khối ký.

Chỗ chưa biết để "……" — người (hoặc trợ lý soạn thảo) điền; đoạn ``BODY`` là chỗ viết nội dung (trợ lý sửa đoạn đó + thêm đoạn ngay sau — đoạn thêm
chép định dạng của nó nên giữ đúng thể thức). Tên công ty (pháp nhân mặc định) và ngày lập do code điền, không để model đoán.
"""

import io
from dataclasses import dataclass
from datetime import date
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Mm, Pt

from barista.api import fold

GAP = "……"
BODY = "[Nội dung]"


@dataclass(frozen=True)
class Form:
    key: str
    label: str
    title: str = ""      # tên loại in giữa trang ("TỜ TRÌNH"…); trống = công văn / thư / trang trắng
    code: str = ""       # chữ viết tắt tên loại trong số, ký hiệu (TTr, TB…)
    hints: tuple[str, ...] = ()


FORMS: dict[str, Form] = {f.key: f for f in (
    Form("cong_van", "Công văn (Nghị định 30)", hints=("cong van", "v/v")),   # công văn: ký hiệu không có tên loại — viết tắt cơ quan + đơn vị soạn
    Form("to_trinh", "Tờ trình", "TỜ TRÌNH", "TTr", ("to trinh",)),
    Form("thong_bao", "Thông báo", "THÔNG BÁO", "TB", ("thong bao",)),
    Form("ke_hoach", "Kế hoạch", "KẾ HOẠCH", "KH", ("ke hoach",)),
    Form("bao_cao", "Báo cáo", "BÁO CÁO", "BC", ("bao cao",)),
    Form("giay_moi", "Giấy mời", "GIẤY MỜI", "GM", ("giay moi",)),
    Form("bien_ban", "Biên bản", "BIÊN BẢN", "BB", ("bien ban",)),
    Form("thu", "Thư (cảm ơn, mời, chúc mừng…)", hints=("thu cam on", "thu moi", "thu chuc", "thu gui", "thu ngo", "thu xin")),
    Form("trang", "Trang trắng"),
)}
ORDER = ("bien_ban", "to_trinh", "thong_bao", "ke_hoach", "bao_cao", "giay_moi", "cong_van", "thu")   # tên loại cụ thể trước "công văn" chung


def infer(brief: str) -> str:
    """Loại khung theo lời mô tả (code chọn, không model): tên loại có trong câu ⇒ khung đó; không rõ ⇒ trang trắng."""
    s = f" {fold(brief).lower()} "
    for k in ORDER:
        if any(h in s for h in FORMS[k].hints):
            return k
    return "trang"


def build(key: str, company: dict[str, Any], day: date) -> bytes:
    f = FORMS.get(key) or FORMS["trang"]
    d = Document()
    s = d.sections[0]
    s.page_width, s.page_height = Mm(210), Mm(297)
    s.top_margin, s.bottom_margin, s.left_margin, s.right_margin = Mm(20), Mm(20), Mm(30), Mm(15)
    normal: Any = d.styles["Normal"]
    normal.font.name, normal.font.size = "Times New Roman", Pt(14)
    fonts = normal.element.get_or_add_rPr().get_or_add_rFonts()
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), "Times New Roman")
    normal.paragraph_format.line_spacing = 1.15
    normal.paragraph_format.space_after = Pt(6)
    name = str(company.get("name") or "").strip()
    if f.key == "trang":
        d.add_paragraph("")
    elif f.key == "thu":
        _letter(d, name, str(company.get("address") or "").strip(), day)
    else:
        _official(d, f, name, day)
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


# ── phần dựng ──

def _para(container: Any, text: str = "", *, size: float = 14, bold: bool = False, italic: bool = False, align: str = "left",
          indent: bool = False, after: float = 0, underline_line: bool = False) -> Any:
    p = container.add_paragraph()
    p.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER, "right": WD_ALIGN_PARAGRAPH.RIGHT,
                   "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}[align]
    pf = p.paragraph_format
    pf.space_after, pf.space_before, pf.line_spacing = Pt(after), Pt(0), 1.0 if not indent else 1.15
    if indent:
        pf.first_line_indent = Cm(1.27)
    if text:
        r = p.add_run(text)
        r.font.size, r.bold, r.italic = Pt(size), bold, italic
    if underline_line:   # đường kẻ ngang nét liền dưới tiêu ngữ / tên cơ quan (NĐ 30): viền dưới của đoạn, thu hai bên cho gần bằng dòng chữ
        pf.left_indent, pf.right_indent = Cm(1.6), Cm(1.6)
        ppr = p._p.get_or_add_pPr()
        bdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "000000")):
            bottom.set(qn(k), v)
        bdr.append(bottom)
        ppr.append(bdr)
    return p


def _grid(d: Any, widths: tuple[float, float]) -> Any:
    """Bảng KHÔNG VIỀN hai cột (cm) — khung đầu / cuối văn bản."""
    t = d.add_table(rows=1, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, w in enumerate(widths):
        t.columns[i].width = Cm(w)   # cột của lưới bảng (trình soạn + Word đọc độ rộng cột ở đây) …
        for c in t.columns[i].cells:
            c.width = Cm(w)          # … và của từng ô (Word ưu tiên ô khi có)
    tbl_pr = t._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tbl_pr.append(borders)
    for c in t.rows[0].cells:   # ô mới có sẵn một đoạn trống — bỏ để đoạn đầu là chữ của khung
        c._tc.remove(c.paragraphs[0]._p)
    return t


def _date_line(day: date) -> str:
    """Địa danh, ngày tháng (NĐ 30): ngày dưới 10 và tháng 1, 2 có số 0 phía trước."""
    month = f"{day.month:02d}" if day.month <= 2 else str(day.month)
    return f"{GAP}, ngày {day.day:02d} tháng {month} năm {day.year}"


def _official(d: Any, f: Form, org: str, day: date) -> None:
    head = _grid(d, (6.5, 9.5))
    left, right = head.rows[0].cells
    _para(left, org.upper() if org else "TÊN CƠ QUAN, TỔ CHỨC", size=13, bold=True, align="center")
    _para(left, "", size=6, align="center", underline_line=True)
    _para(left, f"Số: {GAP}/{f.code}-{GAP}" if f.code else f"Số: {GAP}/{GAP}-{GAP}", size=13, align="center", after=2)
    if f.key == "cong_van":
        _para(left, f"V/v {GAP}", size=12, align="center")
    _para(right, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", size=12, bold=True, align="center")
    _para(right, "Độc lập - Tự do - Hạnh phúc", size=13, bold=True, align="center")
    _para(right, "", size=6, align="center", underline_line=True)
    _para(right, _date_line(day), size=13, italic=True, align="center", after=6)
    _para(d, "", size=8)
    if f.title:
        _para(d, f.title, size=14, bold=True, align="center")
        _para(d, f"Về việc {GAP}" if f.key in ("to_trinh", "ke_hoach", "bao_cao", "thong_bao") else GAP, size=14, bold=True, align="center", after=12)
    if f.key in ("cong_van", "to_trinh"):
        _para(d, f"Kính gửi: {GAP}", size=14, align="center", after=12)
    _para(d, BODY, size=14, align="justify", indent=True, after=6)
    _para(d, "", size=8)
    foot = _grid(d, (7.0, 9.0))
    left, right = foot.rows[0].cells
    if f.key == "bien_ban":
        _para(left, "THƯ KÝ", size=13, bold=True, align="center")
        _para(left, "", size=13)
        _para(left, "", size=13)
        _para(left, GAP, size=13, bold=True, align="center")
        _para(right, "CHỦ TRÌ", size=13, bold=True, align="center")
    else:
        _para(left, "Nơi nhận:", size=12, bold=True, italic=True)
        _para(left, "- Như trên;" if f.key in ("cong_van", "to_trinh") else f"- {GAP};", size=11)
        _para(left, f"- Lưu: VT, {GAP}.", size=11)
        _para(right, f"TM. {org.upper()}" if org else "QUYỀN HẠN, CHỨC VỤ CỦA NGƯỜI KÝ", size=13, bold=True, align="center")
        _para(right, "CHỨC VỤ", size=13, bold=True, align="center")
    _para(right, "", size=13)
    _para(right, "", size=13)
    _para(right, GAP, size=13, bold=True, align="center")


def _letter(d: Any, org: str, address: str, day: date) -> None:
    head = _grid(d, (8.0, 8.0))
    left, right = head.rows[0].cells
    _para(left, org.upper() if org else "TÊN CÔNG TY", size=12, bold=True)
    _para(left, address or f"Địa chỉ: {GAP}", size=11)
    _para(right, _date_line(day), size=13, italic=True, align="right")
    _para(d, "", size=8)
    _para(d, f"Kính gửi: {GAP}", size=14, bold=True, after=6)
    _para(d, BODY, size=14, align="justify", indent=True, after=6)
    _para(d, "Trân trọng.", size=14, align="justify", indent=True, after=12)
    foot = _grid(d, (7.0, 9.0))
    right = foot.rows[0].cells[1]
    _para(foot.rows[0].cells[0], "", size=13)
    _para(right, f"TM. {org.upper()}" if org else "TM. CÔNG TY", size=13, bold=True, align="center")
    _para(right, "CHỨC VỤ", size=13, bold=True, align="center")
    _para(right, "", size=13)
    _para(right, "", size=13)
    _para(right, GAP, size=13, bold=True, align="center")
