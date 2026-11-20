"""Bộ đọc VĂN PHÒNG của năng lực đọc tài liệu (ADR 0232): Word (có BẢNG, đúng thứ tự trong văn bản), bảng tính, PowerPoint, chữ thuần — thuần hàm, không DB, không model, không phí.

* Bảng tính (.xlsx / .xlsm / .xls / .csv / .tsv): mỗi sheet là một mục ``# Tên sheet``; mỗi dòng ``ô | ô | ô``; dòng tiêu đề được nhắc lại
  định kỳ để đoạn cắt nào cũng mang tên cột. .xls cũ đổi sang .xlsx bằng LibreOffice (chưa cài ⇒ báo rõ, không đoán).
* PowerPoint (.pptx): mỗi slide là một "trang", tiêu đề slide thành đầu mục.
* Word (.docx): đoạn kiểu Heading thành ``# …``; bảng thành bảng markdown có dòng tiêu đề.
"""

import csv
import datetime as dt
import io
import re
import unicodedata
from typing import Any

from barista.docread.types import ext_of
from barista.kernel import convert
from barista.kernel.errors import Invalid

BODY_MAX = 450_000          # kb.add_document nhận tối đa 500.000 ký tự — chừa chỗ cho đầu mục
HEADER_EVERY = 12           # nhắc lại dòng tiêu đề bảng sau mỗi ngần ấy dòng (đoạn cắt ~900 ký tự luôn mang tên cột)


# ── bảng tính ──

def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "có" if v else "không"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else f"{v:.10g}"
    if isinstance(v, dt.datetime):
        return f"{v:%Y-%m-%d}" if (v.hour, v.minute, v.second) == (0, 0, 0) else f"{v:%Y-%m-%d %H:%M}"   # ISO: không nhập nhằng ngày / tháng giữa các nước
    if isinstance(v, dt.date):
        return f"{v:%Y-%m-%d}"
    return re.sub(r"\s+", " ", str(v)).strip()


def _table(rows: Any, budget: list[int]) -> list[str]:
    """Các dòng chữ của một sheet; ``budget[0]`` = số ký tự còn được dùng (chung cho cả file) — hết thì dừng, ghi rõ đã cắt."""
    out: list[str] = []
    header = ""
    n = 0
    for row in rows:
        cells = [_cell(c) for c in row]
        while cells and not cells[-1]:
            cells.pop()
        if not any(cells):
            continue
        line = " | ".join(cells)
        if not header:
            header = line
        elif n % HEADER_EVERY == 0:
            out.append(header)
            budget[0] -= len(header) + 1
        n += 1
        out.append(line)
        budget[0] -= len(line) + 1
        if budget[0] <= 0:
            out.append("(…bảng còn nữa — đã cắt bớt vì quá dài; mở file gốc để xem đủ)")
            break
    return out


def _xls_to_xlsx(data: bytes) -> bytes:
    """.xls cũ (nhị phân) → .xlsx bằng LibreOffice. Chưa cài ⇒ báo người dùng lưu lại thành .xlsx (không tự đoán định dạng)."""
    import subprocess
    import tempfile
    from pathlib import Path

    exe = convert.binary()
    if exe is None:
        raise Invalid("File Excel cũ (.xls) cần LibreOffice để đọc mà máy chủ chưa cài — mở file, “Lưu thành” .xlsx rồi tải lại")
    with tempfile.TemporaryDirectory(prefix="barista-xls-") as d:
        src = Path(d) / "in.xls"
        src.write_bytes(data)
        try:
            subprocess.run([exe, f"-env:UserInstallation=file://{d}/profile", "--headless", "--norestore", "--convert-to", "xlsx",
                            "--outdir", d, str(src)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=convert.TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as e:
            raise Invalid("Đổi file .xls quá lâu — mở file, “Lưu thành” .xlsx rồi tải lại") from e
        out = Path(d) / "in.xlsx"
        if not out.exists() or out.stat().st_size == 0:
            raise Invalid("Không đọc được file .xls này — mở file, “Lưu thành” .xlsx rồi tải lại")
        return out.read_bytes()


def sheets_text(filename: str, data: bytes) -> str:
    ext = ext_of(filename)
    budget = [BODY_MAX]
    if ext in (".csv", ".tsv"):
        try:
            raw = data.decode("utf-8-sig")
        except UnicodeDecodeError:   # Excel cũ xuất CSV theo bảng mã Windows (cp1258 = tiếng Việt)
            raw = unicodedata.normalize("NFC", data.decode("cp1258", errors="replace"))   # cp1258 viết dấu rời ⇒ gộp lại dạng dựng sẵn
        try:
            dialect: Any = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel_tab if ext == ".tsv" else csv.excel
        lines = _table(csv.reader(io.StringIO(raw), dialect), budget)
        body = "\n".join(lines)
    else:
        from openpyxl import load_workbook

        if ext == ".xls":
            data = _xls_to_xlsx(data)
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts: list[str] = []
        try:
            for ws in wb.worksheets:
                if ws.sheet_state != "visible":
                    continue
                lines = _table(ws.iter_rows(values_only=True), budget)
                if lines:
                    parts.append(f"# {ws.title}\n" + "\n".join(lines))
                if budget[0] <= 0:
                    break
        finally:
            wb.close()
        body = "\n\n".join(parts)
    if not body.strip():
        raise Invalid("Bảng tính không có dòng nào có chữ / số")
    return body


# ── PowerPoint ──

def _shape_lines(shapes: Any) -> list[str]:
    out: list[str] = []
    for sh in shapes:
        if getattr(sh, "shape_type", None) == 6:   # nhóm hình: đọc vào trong
            out += _shape_lines(sh.shapes)
        elif getattr(sh, "has_table", False) and sh.has_table:
            for row in sh.table.rows:
                cells = [re.sub(r"\s+", " ", c.text).strip() for c in row.cells]
                if any(cells):
                    out.append(" | ".join(cells))
        elif getattr(sh, "has_text_frame", False) and sh.has_text_frame:
            for para in sh.text_frame.paragraphs:
                t = "".join(r.text for r in para.runs).strip()
                if t:
                    out.append(t)
    return out


def slide_blocks(data: bytes) -> list[str]:
    """Chữ từng slide (slide = một "trang"): đầu mục = tiêu đề slide, rồi các dòng chữ / bảng, rồi ghi chú người trình bày. Tổng cắt ở ``BODY_MAX``."""
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    parts: list[str] = []
    for slide in prs.slides:
        title = ""
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title = re.sub(r"\s+", " ", slide.shapes.title.text_frame.text).strip()
        lines = []
        if title:
            lines.append(f"# {title}")
        for t in _shape_lines(slide.shapes):
            if t != title:
                lines.append(t)
        if slide.has_notes_slide and (notes := slide.notes_slide.notes_text_frame):
            if n := notes.text.strip():
                lines.append("Ghi chú của người trình bày: " + n)
        parts.append("\n".join(lines))
    budget = BODY_MAX
    for i, p in enumerate(parts):
        budget -= len(p) + 1
        if budget <= 0:
            return parts[: i + 1]
    return parts


def slides_text(data: bytes) -> str:
    parts = slide_blocks(data)
    body = "\n\n".join(f"## Trang {i}\n{p}" for i, p in enumerate(parts, 1))[:BODY_MAX]
    if not re.sub(r"^#+ .*$|^## Trang \d+$", "", body, flags=re.M).strip():
        raise Invalid("Bài trình bày không có chữ (chỉ có ảnh?) — xuất sang PDF rồi tải lên để trợ lý đọc từng trang")
    return body


# ── Word ──

def word_page(data: bytes) -> tuple[str, list[Any]]:
    """(chữ markdown, các bảng) của file .docx theo ĐÚNG thứ tự trong văn bản — bảng không bị rơi mất như khi chỉ đọc đoạn."""
    from docx import Document
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph

    from barista.docread.types import Table

    doc = Document(io.BytesIO(data))
    lines: list[str] = []
    tables: list[Table] = []
    for el in doc.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(el, doc)
            style = (para.style.name if para.style is not None else "") or ""
            lines.append(("# " if style.lower().startswith("heading") else "") + para.text)
        elif tag == "tbl":
            rows = [[re.sub(r"\s+", " ", c.text).strip() for c in r.cells] for r in DocxTable(el, doc).rows]
            rows = [r for r in rows if any(r)]
            if rows:
                t = Table(rows[0], rows[1:])
                tables.append(t)
                lines.append(t.markdown())
    return "\n".join(lines), tables
