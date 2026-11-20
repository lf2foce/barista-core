"""Bộ đọc ``pypdf``: PDF CÓ lớp chữ — miễn phí. Thêm hai việc mà ``extract_text`` trần không làm:

1. **Bảng** (bảng giá, lịch thanh toán): ``extract_text`` dẹt các cột thành một dòng chữ liền. Ở đây lấy VỊ TRÍ từng mẩu chữ (``visitor_text`` của pypdf), gom thành dòng theo
   toạ độ y, tìm khối ≥ 3 dòng liền nhau có nhiều ô, chia cột theo "hình chiếu" các ô lên trục x (khe trống chung của mọi dòng = ranh giới cột — không cần đường kẻ, không
   phụ thuộc căn trái / phải). Kết quả: bảng markdown có DÒNG TIÊU ĐỀ (kb cắt đoạn thì nhắc lại tiêu đề — ``kb.chunks``). Thuần Python, chỉ dùng pypdf. Bảng đo được là lệch
   (nhiều ô trống / số ô không khớp) ⇒ cờ ``table_ragged`` để chuỗi chuyển trang đó cho model nếu hồ sơ cho phép (``tables = auto``).
2. **Trang chụp / scan**: trang gần như không có chữ mà có ảnh ⇒ cờ ``scanned`` (model đọc, theo công tắc + trần của hồ sơ). Bản scan cả tài liệu (< 15 từ / trang) cũng vậy.

Số trang: ``## Trang N`` do ``Doc.text()`` thêm — mốc trích nguồn của kho tài liệu.
"""

import asyncio
import io
import re
from dataclasses import dataclass
from statistics import mean
from typing import Any

from barista.docread.readers import Request, notice
from barista.docread.types import RAGGED_TABLE, Doc, Page, Table

SCAN_WORDS = 5            # trang có ít hơn ngần ấy từ mà có ảnh ⇒ trang chụp
SCAN_DOC_WORDS = 15       # tài liệu trung bình < ngần ấy từ / trang ⇒ cả tài liệu là bản scan (nới cho trang ít chữ)
CHAR_W = 0.5              # bề rộng ước tính một ký tự = CHAR_W × cỡ chữ (Arial / Be Vietnam ~0.5)
JOIN_GAP = 0.6            # hai mẩu cách nhau < JOIN_GAP × cỡ chữ ⇒ cùng một ô
COL_GAP = 0.6             # khe trống ≥ COL_GAP × cỡ chữ trong MỌI dòng của khối ⇒ ranh giới cột
ROW_TOL = 0.45            # hai mẩu lệch y < ROW_TOL × cỡ chữ ⇒ cùng dòng
RAGGED_BELOW = 0.7        # độ lấp đầy cột trung bình dưới mức này ⇒ bảng lệch


@dataclass
class Frag:
    text: str
    x: float
    y: float
    size: float

    @property
    def end(self) -> float:
        return self.x + len(self.text) * CHAR_W * self.size


def _visitor(out: list[Frag]) -> Any:
    def visit(text: str, cm: list[float], tm: list[float], font_dict: Any, font_size: float) -> None:
        t = text.replace("\n", " ")
        if not t.strip():
            return
        a, _b, _c, d, e, f = tm
        A, B, C, D, E, F = cm
        size = abs(font_size * d * D) or abs(font_size * a * A) or 10.0
        out.append(Frag(t.strip(), e * A + f * C + E, e * B + f * D + F, size))
    return visit


def rows_of(frags: list[Frag]) -> list[list[Frag]]:
    """Dòng chữ từ trên xuống; trong dòng: mẩu gần nhau gộp thành một ô."""
    rows: list[list[Frag]] = []
    for fr in sorted(frags, key=lambda f: (-f.y, f.x)):
        if rows and abs(rows[-1][0].y - fr.y) <= ROW_TOL * max(rows[-1][0].size, fr.size):
            rows[-1].append(fr)
        else:
            rows.append([fr])
    out: list[list[Frag]] = []
    for r in rows:
        cells: list[Frag] = []
        for fr in sorted(r, key=lambda f: f.x):
            if cells and fr.x - cells[-1].end < JOIN_GAP * fr.size:
                last = cells[-1]
                cells[-1] = Frag(f"{last.text} {fr.text}", last.x, last.y, last.size)
            else:
                cells.append(fr)
        out.append(cells)
    return out


def _columns(block: list[list[Frag]]) -> list[tuple[float, float]]:
    """Cột = hợp các khoảng [x, x + rộng] của mọi ô trong khối; khe trống ≥ COL_GAP × cỡ chữ tách cột."""
    ivs = sorted((c.x, c.end, c.size) for r in block for c in r)
    cols: list[list[float]] = []
    for x0, x1, size in ivs:
        if cols and x0 - cols[-1][1] < COL_GAP * size:
            cols[-1][1] = max(cols[-1][1], x1)
        else:
            cols.append([x0, x1])
    return [(a, b) for a, b in cols]


def _col_of(cell: Frag, cols: list[tuple[float, float]]) -> int:
    mid = (cell.x + cell.end) / 2
    best = min(range(len(cols)), key=lambda i: 0 if cols[i][0] <= mid <= cols[i][1] else min(abs(mid - cols[i][0]), abs(mid - cols[i][1])))
    return best


def _numeric(s: str) -> bool:
    return bool(re.search(r"\d", s))


def find_tables(rows: list[list[Frag]]) -> list[tuple[int, int, Table, float]]:
    """[(dòng đầu, dòng cuối + 1, bảng, độ lấp đầy 0–1)] — khối ≥ 3 dòng liền nhau, mỗi dòng ≥ 2 ô, sau chia cột được ≥ 3 cột (hoặc 2 cột với ≥ 5 dòng và cột phải là số)."""
    out: list[tuple[int, int, Table, float]] = []
    i = 0
    while i < len(rows):
        if len(rows[i]) < 2:
            i += 1
            continue
        j = i
        while j < len(rows) and len(rows[j]) >= 2:
            j += 1
        block = rows[i:j]
        if len(block) >= 3:
            cols = _columns(block)
            n = len(cols)
            ok = n >= 3 or (n == 2 and len(block) >= 5 and sum(_numeric(r[-1].text) for r in block[1:]) >= 0.8 * (len(block) - 1))
            if ok:
                grid: list[list[str]] = []
                for r in block:
                    cells = [""] * n
                    for c in r:
                        k = _col_of(c, cols)
                        cells[k] = (cells[k] + " " + c.text).strip()
                    grid.append(cells)
                fill = mean(sum(1 for c in g if c) / n for g in grid)
                out.append((i, j, Table(grid[0], grid[1:]), fill))
        i = j
    return out


def page_text_and_tables(frags: list[Frag], plain: str) -> tuple[str, list[Table], float | None]:
    """(chữ trang, bảng, độ lấp đầy thấp nhất). Không có bảng ⇒ chữ gốc của pypdf, không đổi gì."""
    rows = rows_of(frags)
    found = find_tables(rows)
    if not found:
        return plain, [], None
    lines: list[str] = []
    pos = 0
    for a, b, table, _ in found:
        lines += [" ".join(c.text for c in r) for r in rows[pos:a]]
        lines.append(table.markdown())
        pos = b
    lines += [" ".join(c.text for c in r) for r in rows[pos:]]
    return "\n".join(lines), [t for _, _, t, _ in found], min(f for _, _, _, f in found)


def has_image(page: Any) -> bool:
    try:
        xo = page["/Resources"]["/XObject"].get_object()
    except Exception:   # trang không có tài nguyên ảnh
        return False
    return any(xo[k].get_object().get("/Subtype") == "/Image" for k in xo)


class PdfTextReader:
    name = "pypdf"
    paid = False

    def handles(self, kind: str) -> bool:
        return kind == "pdf"

    async def read(self, req: Request, doc: Doc) -> None:
        await asyncio.to_thread(self.read_sync, req, doc)   # đọc file nặng CPU ở luồng riêng — không chặn vòng lặp sự kiện

    def read_sync(self, req: Request, doc: Doc) -> None:
        if doc.pages:
            return
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(req.data))
        if reader.is_encrypted and not reader.decrypt(""):
            from barista.kernel.errors import Invalid

            raise Invalid("PDF có mật khẩu — mở khoá rồi tải lại")
        doc.paged = True
        tables_on = req.policy.tables != "off"
        wanted = set(req.pages) if req.pages else None
        for n, page in enumerate(reader.pages, 1):
            if wanted is not None and n not in wanted:
                continue
            frags: list[Frag] = []
            plain = (page.extract_text(visitor_text=_visitor(frags)) if tables_on else page.extract_text()) or ""
            text, tables, fill = (page_text_and_tables(frags, plain) if tables_on else (plain, [], None))
            pg = Page(n, text, tables, method="pdftable" if tables else self.name)
            for t in tables:
                t.page = n
            if tables:
                pg.flags.append("table")
                pg.confidence = round(fill or 0.0, 2)
                if fill is not None and fill < RAGGED_BELOW:
                    pg.flags.append("table_ragged")
            words = len(plain.split())
            if words < SCAN_WORDS and has_image(page):
                pg.flags.append("scanned")
            elif words == 0:
                pg.flags.append("empty")
            doc.pages.append(pg)
        # bản scan cả tài liệu: trung bình < 15 từ / trang ⇒ mọi trang ít chữ đều cần model (như ``scanned()`` cũ)
        if doc.pages and sum(len(p.text.split()) for p in doc.pages) < SCAN_DOC_WORDS * len(doc.pages):
            for p in doc.pages:
                if len(p.text.split()) < SCAN_DOC_WORDS and "scanned" not in p.flags:
                    p.flags = [f for f in p.flags if f != "empty"] + ["scanned"]
        ragged = [p.n for p in doc.pages if "table_ragged" in p.flags]
        if ragged and "model" not in req.policy.chain:
            notice(doc, RAGGED_TABLE, f"Bảng ở trang {', '.join(map(str, ragged))} trích bằng code, có thể lệch cột — đối chiếu file gốc.", ragged)
