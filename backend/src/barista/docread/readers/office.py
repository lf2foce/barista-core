"""Bộ đọc ``office``: Word / bảng tính / PowerPoint / chữ thuần — miễn phí, không model."""

import asyncio
import re

from barista.docread import office
from barista.docread.readers import Request
from barista.docread.types import Doc, Page, Table, parse_md_tables


class OfficeReader:
    name = "office"
    paid = False

    def handles(self, kind: str) -> bool:
        return kind in ("word", "text", "sheet", "slides")

    async def read(self, req: Request, doc: Doc) -> None:
        await asyncio.to_thread(self.read_sync, req, doc)   # đọc file nặng CPU ở luồng riêng — không chặn vòng lặp sự kiện

    def read_sync(self, req: Request, doc: Doc) -> None:
        if doc.pages:
            return
        if doc.kind == "word":
            text, tables = office.word_page(req.data)
            doc.pages = [Page(1, text, tables, method=self.name)]
        elif doc.kind == "text":
            text = req.data.decode("utf-8", errors="replace")
            doc.pages = [Page(1, text, parse_md_tables(text), method=self.name)]
        elif doc.kind == "sheet":
            text = office.sheets_text(req.filename, req.data)
            doc.pages = [Page(1, text, _sheet_tables(text), method=self.name)]
        elif doc.kind == "slides":
            blocks = office.slide_blocks(req.data)
            body = "\n".join(blocks)
            if not re.sub(r"^#+ .*$", "", body, flags=re.M).strip():
                from barista.kernel.errors import Invalid

                raise Invalid("Bài trình bày không có chữ (chỉ có ảnh?) — xuất sang PDF rồi tải lên để trợ lý đọc từng trang")
            doc.pages = [Page(i, b, method=self.name) for i, b in enumerate(blocks, 1)]
            doc.paged = True
        for p in doc.pages:
            if not p.text.strip():
                p.flags.append("empty")


def _sheet_tables(text: str) -> list[Table]:
    """Mỗi sheet (``# Tên`` rồi các dòng ``ô | ô``) là một bảng: dòng đầu là tiêu đề. Dòng tiêu đề nhắc lại định kỳ được bỏ khỏi ``rows``."""
    out: list[Table] = []
    titled = text.startswith("# ")
    for i, block in enumerate(re.split(r"(?m)^# ", text)):
        if i == 0 and titled:
            continue
        lines = [x for x in (block.splitlines()[1:] if titled or i > 0 else block.splitlines()) if x.strip()]
        if not lines or " | " not in lines[0]:
            continue
        head = [c.strip() for c in lines[0].split(" | ")]
        rows = [[c.strip() for c in ln.split(" | ")] for ln in lines[1:] if ln != lines[0] and not ln.startswith("(…")]
        out.append(Table(head, rows))
    return out
