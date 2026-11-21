"""Dựng bộ tài liệu GIẢ để đo bộ đọc (ADR 0232) — không dữ liệu khách thật. Chạy lại khi cần đổi: ``python -m barista.docread.fixtures.make`` (cần Google Chrome để in PDF / chụp ảnh;
kết quả được commit nên máy khác không cần chạy lại).

Mỗi tài liệu có ĐÁP ÁN trong ``truth.json``: từng trang là danh sách KHỐI theo thứ tự đọc (câu chữ, hoặc ``{"table": {header, rows}}``), ``facts`` (chuỗi bắt buộc phải có — số hoá đơn, tổng tiền…), ``markers`` (từ chỉ có ở trang đó — kiểm
gán đúng trang), ``caption_any`` (từ khoá một bản mô tả đúng phải chạm tới). Công ty / dự án / người trong tài liệu đều hư cấu.
"""

import base64
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FONTS = HERE / "fonts"

PRICE_HEAD = ["STT", "Mã căn", "Diện tích (m²)", "Giá bán (VND)", "Ghi chú"]
PRICE_ROWS = [
    ["1", "A-0501", "62,4", "2.450.000.000", "Hướng Đông"],
    ["2", "A-0502", "58,7", "2.280.000.000", "Hướng Tây"],
    ["3", "A-0610", "75,2", "3.120.000.000", "Góc, view sông"],
    ["4", "B-0305", "49,5", "1.890.000.000", "Hướng Nam"],
    ["5", "B-0306", "49,5", "1.915.000.000", "Hướng Bắc"],
    ["6", "B-1208", "83,9", "3.560.000.000", "Penthouse mini"],
    ["7", "C-0902", "66,1", "2.640.000.000", "Hướng Đông Nam"],
    ["8", "C-0903", "66,1", "2.655.000.000", "Hướng Tây Bắc"],
]
PAY_HEAD = ["Đợt", "Thời điểm thanh toán", "Tỷ lệ", "Số tiền (VND)"]
PAY_ROWS = [
    ["Đợt 1", "Khi ký thỏa thuận đặt cọc", "5%", "122.500.000"],
    ["Đợt 2", "30 ngày sau khi ký hợp đồng", "15%", "367.500.000"],
    ["Đợt 3", "Khi hoàn thành phần móng", "20%", "490.000.000"],
    ["Đợt 4", "Khi cất nóc", "25%", "612.500.000"],
    ["Đợt 5", "Khi bàn giao căn hộ", "30%", "735.000.000"],
    ["Đợt 6", "Khi nhận giấy chứng nhận", "5%", "122.500.000"],
]
P1_TITLE = "BẢNG GIÁ CĂN HỘ DỰ ÁN VƯỜN LAM SƠN"
P1_TEXT = "Áp dụng cho khách hàng mua trực tiếp từ chủ đầu tư, giá đã bao gồm thuế giá trị gia tăng và chưa gồm kinh phí bảo trì 2%."
P2_TITLE = "LỊCH THANH TOÁN MẪU CHO CĂN A-0501"
P2_TEXT = "Tổng giá trị căn hộ là 2.450.000.000 đồng. Khách hàng thanh toán theo sáu đợt như bảng dưới đây, mỗi đợt chuyển khoản vào tài khoản của chủ đầu tư."
P2_NOTE = "Lưu ý: chậm thanh toán quá 15 ngày, chủ đầu tư được tính lãi 0,03% mỗi ngày trên số tiền chậm."
SCAN_TITLE = "ĐIỀU KHOẢN ĐẶT CỌC"
SCAN_LINES = [
    "Điều 1. Bên đặt cọc nộp cho Bên nhận cọc số tiền 122.500.000 đồng khi ký thỏa thuận.",
    "Điều 2. Trong vòng 30 ngày kể từ ngày nhận cọc, hai bên ký hợp đồng mua bán chính thức.",
    "Điều 3. Bên đặt cọc từ chối ký hợp đồng thì mất khoản tiền đặt cọc.",
    "Điều 4. Bên nhận cọc từ chối ký hợp đồng thì hoàn trả khoản cọc và chịu phạt một lần số tiền đã nhận.",
]
RECEIPT = {"shop": "CỬA HÀNG TIỆN LỢI MINH AN", "addr": "Số 12 đường Hoa Mai, phường Tân Phú", "no": "HD-004217", "date": "12/11/2026",
           "items": [("Nước suối 500ml", 6, 5000), ("Mì ly hải sản", 4, 12500), ("Bánh gạo vị phô mai", 3, 18000), ("Sữa tươi không đường", 5, 9500), ("Khăn giấy", 2, 22000)]}
DOCX_TITLE = "Hợp đồng đặt cọc căn hộ"
DOCX_PARAS = [("h1", "Điều 1. Các bên"), ("p", "Bên nhận cọc là Công ty Phát triển Nhà Vườn Lam Sơn, Bên đặt cọc là khách hàng ghi tại bảng thông tin dưới đây."),
              ("table", None), ("h1", "Điều 2. Giá trị và tiến độ"), ("p", "Giá bán căn hộ A-0501 là 2.450.000.000 đồng, thanh toán theo sáu đợt."),
              ("h2", "2.1 Phạt vi phạm"), ("p", "Bên vi phạm chịu phạt 8% giá trị hợp đồng.")]
DOCX_TABLE_HEAD = ["Hạng mục", "Nội dung"]
DOCX_TABLE = [["Căn hộ", "A-0501, tầng 5, tòa A"], ["Diện tích", "62,4 m²"], ["Tiền đặt cọc", "122.500.000 đồng"], ["Ngày ký", "15/11/2026"]]
SHEETS = {"Bảng giá": [["Mã căn", "Diện tích", "Giá bán"], ["A-0501", 62.4, 2450000000], ["A-0502", 58.7, 2280000000], ["B-0305", 49.5, 1890000000]],
          "Chính sách": [["Chương trình", "Ưu đãi"], ["Thanh toán sớm", "Chiết khấu 2%"], ["Mua từ căn thứ hai", "Chiết khấu 1%"]]}


def _font_css() -> str:
    out = ""
    for name, weight in (("BeVietnamPro-Regular-vi", 400), ("BeVietnamPro-SemiBold-vi", 700)):
        b64 = base64.b64encode((FONTS / f"{name}.woff2").read_bytes()).decode()
        out += f"@font-face{{font-family:'BVN';font-weight:{weight};src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
    return out


def _table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{c}</th>" for c in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>"


CSS = ("body{font-family:'BVN',sans-serif;font-size:11pt;margin:0;padding:0}h1{font-size:15pt;margin:0 0 12pt}p{margin:0 0 14pt;line-height:1.5}"
       "table{border-collapse:collapse;width:100%;margin-bottom:14pt}th,td{border:1px solid #444;padding:6pt 8pt;text-align:left}th{font-weight:700}td:nth-child(4),th:nth-child(4){text-align:right}")


def _chrome(args: list[str]) -> None:
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars", *args], check=True, capture_output=True, timeout=120)


def _html_to_pdf(html: str, out: Path) -> None:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "x.html"
        f.write_text(f"<!doctype html><meta charset=utf-8><style>{_font_css()}{CSS}@page{{size:A4;margin:22mm 18mm}}</style>{html}", encoding="utf-8")
        _chrome(["--no-pdf-header-footer", f"--print-to-pdf={out}", f.as_uri()])


def _html_to_png(html: str, out: Path, w: int, h: int, css: str = "") -> None:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "x.html"
        f.write_text(f"<!doctype html><meta charset=utf-8><style>{_font_css()}{CSS}{css}</style>{html}", encoding="utf-8")
        _chrome([f"--screenshot={out}", f"--window-size={w},{h}", f.as_uri()])


def _scanned(png: Path, out: Path) -> None:
    from PIL import Image, ImageFilter

    rnd = random.Random(7)
    im = Image.open(png).convert("L").rotate(0.7, expand=True, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
    px: Any = im.load()
    for _ in range(im.width * im.height // 40):
        x, y = rnd.randrange(im.width), rnd.randrange(im.height)
        px[x, y] = max(0, min(255, px[x, y] + rnd.randint(-40, 40)))
    im.save(str(out), "PDF", resolution=110)


def _photo(out: Path) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    rnd = random.Random(3)
    w, h = 1200, 800
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):   # bầu trời
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(120 + 100 * t), int(170 + 60 * t), int(235 - 20 * t)))
    d.rectangle([0, 470, w, h], fill=(150, 170, 120))   # bãi cỏ
    for bx, bw, bh in ((90, 260, 330), (380, 300, 400), (720, 280, 300)):   # ba toà nhà
        top = 470 - bh
        d.rectangle([bx, top, bx + bw, 470], fill=(226, 222, 214))
        for fy in range(top + 24, 440, 46):
            for fx in range(bx + 18, bx + bw - 30, 40):
                d.rectangle([fx, fy, fx + 22, fy + 26], fill=(70, 110, 150))
    d.polygon([(130, 800), (1070, 800), (980, 560), (220, 560)], fill=(215, 205, 180))   # sân quanh hồ
    d.polygon([(190, 770), (1010, 770), (940, 595), (260, 595)], fill=(36, 150, 205))   # hồ bơi
    for i in range(1, 6):   # vạch phân làn
        x0, x1 = 260 + i * 115, 190 + i * 137
        d.line([(x0, 595), (x1, 770)], fill=(235, 245, 250), width=3)
    for tx, ty in ((60, 520), (1110, 540), (1040, 470)):   # cây
        d.rectangle([tx - 6, ty, tx + 6, ty + 90], fill=(110, 80, 50))
        d.ellipse([tx - 55, ty - 70, tx + 55, ty + 30], fill=(40, 120, 60))
    im = im.filter(ImageFilter.GaussianBlur(1.1))
    px: Any = im.load()
    for _ in range(w * h // 25):
        x, y = rnd.randrange(w), rnd.randrange(h)
        r, g, b = px[x, y]
        n = rnd.randint(-14, 14)
        px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
    im.save(out, "JPEG", quality=82)


def _docx(out: Path) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading(DOCX_TITLE, 0)
    for kind, text in DOCX_PARAS:
        if kind == "table":
            t = doc.add_table(rows=1, cols=2)
            for i, c in enumerate(DOCX_TABLE_HEAD):
                t.rows[0].cells[i].text = c
            for r in DOCX_TABLE:
                cells = t.add_row().cells
                for i, c in enumerate(r):
                    cells[i].text = c
        elif kind in ("h1", "h2"):
            doc.add_heading(text, int(kind[1]))
        else:
            doc.add_paragraph(text)
    doc.save(str(out))


def _xlsx(out: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.worksheets[0])
    for name, rows in SHEETS.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(str(out))


def _receipt_numbers() -> tuple[list[tuple[str, int, int, int]], int, int]:
    lines = [(n, q, p, q * p) for n, q, p in RECEIPT["items"]]
    total = sum(x[3] for x in lines)
    return lines, total, round(total * 0.08)


def money(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def main() -> None:
    from pypdf import PdfReader, PdfWriter

    price_pdf = HERE / "bang-gia.pdf"
    page1 = f"<h1>{P1_TITLE}</h1><p>{P1_TEXT}</p>{_table(PRICE_HEAD, PRICE_ROWS)}"
    page2 = f"<div style='page-break-before:always'><h1>{P2_TITLE}</h1><p>{P2_TEXT}</p>{_table(PAY_HEAD, PAY_ROWS)}<p>{P2_NOTE}</p></div>"
    _html_to_pdf(page1 + page2, price_pdf)
    assert len(PdfReader(str(price_pdf)).pages) == 2

    scan_png = HERE / "_scan.png"
    body = f"<div style='padding:70px 80px;width:900px'><h1 style='font-size:22pt'>{SCAN_TITLE}</h1>" + "".join(f"<p style='font-size:15pt;line-height:1.7'>{x}</p>" for x in SCAN_LINES) + "</div>"
    _html_to_png(body, scan_png, 1060, 760)
    _scanned(scan_png, HERE / "phieu-scan.pdf")
    scan_png.unlink()

    w = PdfWriter()   # một trang chữ + một trang scan: thử chuyển đúng trang
    w.add_page(PdfReader(str(price_pdf)).pages[0])
    w.add_page(PdfReader(str(HERE / "phieu-scan.pdf")).pages[0])
    with (HERE / "hon-hop.pdf").open("wb") as f:
        w.write(f)

    _docx(HERE / "hop-dong.docx")
    _xlsx(HERE / "bang-tinh.xlsx")

    lines, total, vat = _receipt_numbers()
    rows = "".join(f"<tr><td>{n}</td><td>{q}</td><td>{money(p)}</td><td>{money(a)}</td></tr>" for n, q, p, a in lines)
    html = (f"<div style='width:600px;padding:28px 30px;background:#fff'><h1 style='text-align:center;font-size:17pt'>{RECEIPT['shop']}</h1>"
            f"<p style='text-align:center;margin-bottom:6pt'>{RECEIPT['addr']}</p><p style='text-align:center;font-weight:700;font-size:13pt'>HÓA ĐƠN BÁN LẺ</p>"
            f"<p>Số: {RECEIPT['no']} &nbsp; Ngày: {RECEIPT['date']}</p>"
            f"<table style='font-size:10.5pt'><thead><tr><th>Mặt hàng</th><th>SL</th><th>Đơn giá</th><th>Thành tiền</th></tr></thead><tbody>{rows}</tbody></table>"
            f"<p style='text-align:right'>Thuế GTGT 8%: {money(vat)}</p><p style='text-align:right;font-weight:700;font-size:13pt'>Tổng cộng: {money(total)} đ</p></div>")
    _html_to_png(html, HERE / "hoa-don.png", 660, 640, "body{background:#fff}")
    _photo(HERE / "phoi-canh.jpg")

    def tbl(head: list[str], rows: list[list[str]]) -> dict[str, Any]:
        return {"table": {"header": head, "rows": rows}}

    p1 = [P1_TITLE, P1_TEXT, tbl(PRICE_HEAD, PRICE_ROWS)]
    p2 = [P2_TITLE, P2_TEXT, tbl(PAY_HEAD, PAY_ROWS), P2_NOTE]
    scan = [SCAN_TITLE, *SCAN_LINES]
    docx_blocks: list[Any] = [DOCX_TITLE]
    for kind, text in DOCX_PARAS:
        docx_blocks.append(tbl(DOCX_TABLE_HEAD, DOCX_TABLE) if kind == "table" else text)
    sheet_blocks: list[Any] = []
    for name, rows in SHEETS.items():
        sheet_blocks += [name, tbl([str(c) for c in rows[0]], [[(str(c)) for c in r] for r in rows[1:]])]
    receipt = [RECEIPT["shop"], RECEIPT["addr"], "HÓA ĐƠN BÁN LẺ", f"Số: {RECEIPT['no']} Ngày: {RECEIPT['date']}",
               tbl(["Mặt hàng", "SL", "Đơn giá", "Thành tiền"], [[n, str(q), money(p), money(a)] for n, q, p, a in lines]),
               f"Thuế GTGT 8%: {money(vat)}", f"Tổng cộng: {money(total)} đ"]
    truth = {
        "bang-gia.pdf": {"kind": "pdf", "pages": [p1, p2], "facts": ["2.450.000.000", "A-0610", "3.560.000.000", "367.500.000", "30 ngày sau khi ký hợp đồng"],
                         "markers": [["BẢNG GIÁ"], ["LỊCH THANH TOÁN"]]},
        "phieu-scan.pdf": {"kind": "pdf", "pages": [scan], "facts": ["122.500.000", "30 ngày", "Điều 3"], "markers": [["ĐẶT CỌC"]]},
        "hon-hop.pdf": {"kind": "pdf", "pages": [p1, scan], "facts": ["A-0610", "122.500.000"], "markers": [["BẢNG GIÁ"], ["ĐẶT CỌC"]]},
        "hop-dong.docx": {"kind": "word", "pages": [docx_blocks], "facts": ["122.500.000 đồng", "8% giá trị hợp đồng"], "markers": []},
        "bang-tinh.xlsx": {"kind": "sheet", "pages": [sheet_blocks], "facts": ["A-0501", "Chiết khấu 2%"], "markers": []},
        "hoa-don.png": {"kind": "image", "pages": [receipt], "facts": [RECEIPT["no"], RECEIPT["date"], money(total), money(vat)], "markers": []},
        "phoi-canh.jpg": {"kind": "image", "pages": [[]], "facts": [], "markers": [], "caption_any": ["hồ bơi", "bể bơi", "toà nhà", "tòa nhà", "chung cư", "căn hộ"]},
    }
    (HERE / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    print("đã dựng", ", ".join(sorted(truth)))


if __name__ == "__main__":
    sys.exit(main())
