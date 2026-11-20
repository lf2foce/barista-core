"""Kiểu dữ liệu của năng lực ĐỌC TÀI LIỆU (ADR 0232) — thuần dữ liệu, không DB, không model.

``Doc`` = kết quả một lần đọc: các trang (chữ markdown + bảng có tiêu đề + ảnh), phương pháp đã dùng cho TỪNG trang, cờ / độ tin cậy,
các thông báo (``Notice`` — không bao giờ im lặng khi một trang không được đọc bằng model vì công tắc / trần / hết hạn mức), và số đo
(token, chi phí, giây). ``Doc.text()`` giữ đúng dạng cũ của kho tài liệu: PDF / PowerPoint mở đầu mỗi trang bằng ``## Trang N`` (mốc trích nguồn).
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any

# loại file theo đuôi
PDF = {".pdf"}
WORD = {".docx"}
TEXT = {".txt", ".md"}
SHEET = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv"}
SLIDES = {".pptx"}
IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif"}

# mã thông báo (Notice.code)
AI_OFF = "ai_off"                 # công ty tắt trợ lý AI
NO_MODEL = "no_model"             # bản cài chưa cấu hình model đọc
MODEL_ERROR = "model_error"       # model / nhà cung cấp lỗi
SWITCHED_OFF = "switched_off"     # công tắc của công ty / mục đích tắt việc này
PAGE_CAP = "page_cap"             # quá trần số trang model đọc / tài liệu
OVER_BUDGET = "over_budget"       # hết hạn mức token đọc tài liệu của tháng
RAGGED_TABLE = "ragged_table"     # bảng trích bằng code chưa chắc đúng (không có model để sửa)


def ext_of(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).suffix.lower()


def base_name(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).name


def kind_of(filename: str) -> str:
    """pdf / word / text / sheet / slides / image / "" (chưa hỗ trợ)."""
    e = ext_of(filename)
    for k, s in (("pdf", PDF), ("word", WORD), ("text", TEXT), ("sheet", SHEET), ("slides", SLIDES), ("image", IMAGE)):
        if e in s:
            return k
    return ""


@dataclass
class Table:
    header: list[str]
    rows: list[list[str]]
    page: int = 1

    def markdown(self) -> str:
        w = max([len(self.header), *(len(r) for r in self.rows)] or [1])
        pad = lambda cells: [*(_cell(c) for c in cells), *[""] * (w - len(cells))]  # noqa: E731
        lines = ["| " + " | ".join(pad(self.header)) + " |", "|" + "|".join([" --- "] * w) + "|"]
        lines += ["| " + " | ".join(pad(r)) + " |" for r in self.rows]
        return "\n".join(lines)

    def cells(self) -> list[list[str]]:
        return [self.header, *self.rows]


def _cell(c: Any) -> str:
    return re.sub(r"\s+", " ", str(c if c is not None else "")).replace("|", "/").strip()


_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


def parse_md_tables(text: str, page: int = 1) -> list[Table]:
    """Các bảng markdown (``| a | b |`` + dòng ngăn ``|---|---|``) trong ``text``."""
    out: list[Table] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines) - 1:
        if lines[i].strip().startswith("|") and _SEP.match(lines[i + 1].strip()):
            cut = lambda s: [c.strip() for c in s.strip().strip("|").split("|")]  # noqa: E731
            header, rows, j = cut(lines[i]), [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append(cut(lines[j]))
                j += 1
            out.append(Table(header, rows, page))
            i = j
        else:
            i += 1
    return out


@dataclass
class ImageRef:
    """Ảnh nằm TRONG tài liệu (hoặc cả file ảnh). ``data`` = JPEG đã chuẩn hoá; ``text`` / ``caption`` điền khi model đọc (tốn tiền, theo công tắc)."""
    page: int
    idx: int
    width: int
    height: int
    sha256: str
    data: bytes = b""
    text: str = ""
    caption: str = ""
    file_id: str | None = None    # mã trong sổ tệp (``files.put``) — nơi gọi điền sau khi lưu bản JPEG; embedder đa phương thức đọc LẠI bytes từ đây (không chỉ mô tả)


@dataclass
class AudioRef:
    """Chỗ dành cho ÂM THANH (chưa có bộ đọc nào sinh ra): bản chép lời + tham chiếu tệp gốc — embedder đa phương thức nhúng thẳng âm thanh hoặc nhúng bản chép."""
    sha256: str
    seconds: float = 0.0
    transcript: str = ""
    file_id: str | None = None
    media_type: str = "audio/mpeg"


@dataclass(frozen=True)
class Unit:
    """ĐƠN VỊ NHÚNG ổn định của một ``Doc`` (ADR 0232 "Hướng tiếp: embedding đa phương thức"): embedder chọn từng đơn vị nhúng bằng chữ hay bằng phương tiện gốc.

    ``key`` ổn định theo (băm file, loại, trang, thứ tự) — đọc lại cùng file ra cùng khoá ⇒ biết đơn vị nào đã nhúng. ``kind``: ``text`` (chữ của một trang, KHÔNG gồm bảng — cắt nhỏ
    tiếp là việc của nơi dùng, vd ``kb.chunks``) · ``table`` (tiêu đề + dòng; ``text`` = bảng markdown) · ``image`` (``text`` = chữ chép + mô tả nếu có; ``ref`` trỏ bytes ảnh đã lưu) · ``audio``.
    ``ref``: {sha256, file_id, media_type, width, height, seconds} — ``file_id`` trống nếu nơi gọi chưa lưu tệp."""
    key: str
    kind: str
    page: int | None
    text: str
    header: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    ref: dict[str, Any] | None = None
    alt: str = ""


@dataclass
class Page:
    n: int
    text: str = ""                                  # markdown; bảng đã nằm trong chữ (và lặp ở ``tables`` dạng cấu trúc)
    tables: list[Table] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    method: str = ""                                # tên bộ đọc cuối cùng cho trang này: office / pypdf / pdftable / model / <ngoài>
    confidence: float | None = None                 # 0–1 nếu bộ đọc tự đo được
    flags: list[str] = field(default_factory=list)  # scanned · table · table_ragged · empty


@dataclass
class Notice:
    code: str
    message: str
    pages: list[int] = field(default_factory=list)


@dataclass
class Usage:
    """Số đo của MỘT lần đọc (cộng dồn mọi lần gọi model trong đó)."""
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal | None = Decimal(0)           # None = có lượt chưa tra được giá (đừng báo thiếu mà tưởng đủ)
    seconds: float = 0.0
    model_pages: int = 0                            # số trang / ảnh đã gửi model (tính tiền theo đây)
    models: list[str] = field(default_factory=list)

    def add(self, other: Usage) -> None:
        self.requests += other.requests
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd = None if self.cost_usd is None or other.cost_usd is None else self.cost_usd + other.cost_usd
        self.seconds += other.seconds
        self.model_pages += other.model_pages
        self.models += [m for m in other.models if m not in self.models]

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Doc:
    filename: str
    kind: str
    pages: list[Page] = field(default_factory=list)
    paged: bool = False                             # có trang thật (PDF / slide) ⇒ ``text()`` mở đầu mỗi trang bằng "## Trang N"
    title_hint: str = ""
    notices: list[Notice] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cached: bool = False                            # lấy từ bộ nhớ theo băm file — không tốn lượt model
    policy: str = ""                                # tóm tắt cấu hình đã áp (để truy vết / benchmark)
    sha256: str = ""                                # băm file gốc — gốc của khoá đơn vị nhúng
    audio: list[AudioRef] = field(default_factory=list)

    def text(self) -> str:
        if self.paged:
            return "\n".join(f"## Trang {p.n}\n{p.text}" for p in self.pages)
        return "\n".join(p.text for p in self.pages)

    @property
    def tables(self) -> list[Table]:
        return [t for p in self.pages for t in p.tables]

    @property
    def images(self) -> list[ImageRef]:
        return [i for p in self.pages for i in p.images]

    @property
    def methods(self) -> dict[int, str]:
        return {p.n: p.method for p in self.pages}

    def units(self) -> list[Unit]:
        """Đơn vị nhúng ổn định theo thứ tự đọc: chữ từng trang (không gồm bảng) · từng bảng (tiêu đề + dòng) · từng ảnh (kèm tham chiếu bytes) · âm thanh. Không đoán: thiếu chữ ⇒ không có đơn vị chữ."""
        base = (self.sha256 or "doc")[:16]
        out: list[Unit] = []
        for p in self.pages:
            body = "\n".join(ln for ln in p.text.splitlines() if not (p.tables and ln.lstrip().startswith("|"))).strip()
            pg = p.n if self.paged else None
            if body:
                out.append(Unit(f"{base}/text/{p.n}/0", "text", pg, body))
            for i, t in enumerate(p.tables):
                out.append(Unit(f"{base}/table/{p.n}/{i}", "table", pg, t.markdown(), tuple(t.header), tuple(tuple(r) for r in t.rows)))
            for i, im in enumerate(p.images):
                label = "\n".join(x for x in (im.caption, im.text) if x)
                out.append(Unit(f"{base}/image/{p.n}/{i}", "image", pg, label, ref={"sha256": im.sha256, "file_id": im.file_id, "media_type": "image/jpeg",
                                                                                     "width": im.width, "height": im.height}, alt=im.caption))
        for i, a in enumerate(self.audio):
            out.append(Unit(f"{base}/audio/0/{i}", "audio", None, a.transcript, ref={"sha256": a.sha256, "file_id": a.file_id, "media_type": a.media_type, "seconds": a.seconds}))
        return out

    def unread(self) -> list[int]:
        """Trang cần model đọc mà chưa được (công tắc tắt / trần / hết hạn mức / lỗi) — nơi gọi quyết định báo người hay dùng tạm chữ có sẵn."""
        return [p.n for p in self.pages if "scanned" in p.flags and p.method != "model"]

    def notice(self, code: str) -> Notice | None:
        return next((n for n in self.notices if n.code == code), None)
