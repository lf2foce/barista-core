"""Tên tài liệu đoán TỪ CHÍNH FILE (metadata / đầu mục đầu tiên / tên file) — thuần hàm; model đoán được thì nơi gọi ưu tiên model."""

import io
import re

from barista.docread.types import IMAGE, SHEET, base_name, ext_of

_BAD_TITLE = re.compile(r"^(untitled|microsoft (word|powerpoint|excel)|document\d*|slide\d*|bản sao|new document)\b|\.(docx?|pdf|pptx?|xlsx?)$", re.I)


def sane_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip(" -_.")
    return t[:150] if 4 <= len(t) <= 150 and not _BAD_TITLE.search(t) else ""


def title_from_filename(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", base_name(filename))
    stem = re.sub(r"[_]+|(?<=\S)-(?=\S)", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()[:200]
    return (stem[:1].upper() + stem[1:]) if stem else "Tài liệu"   # tên file thường viết thường hết — viết hoa chữ đầu, giữ nguyên phần còn lại


def hint_title(filename: str, data: bytes, text: str) -> str:
    """Tên từ metadata (PDF /Title, Word title) hoặc đầu mục đầu tiên của chữ; không có ⇒ tên file đã làm sạch."""
    ext = ext_of(filename)
    try:
        if ext == ".pdf":
            from pypdf import PdfReader

            meta = PdfReader(io.BytesIO(data)).metadata
            if meta and (t := sane_title(str(meta.title or ""))):
                return t
        elif ext == ".docx":
            from docx import Document

            if t := sane_title(Document(io.BytesIO(data)).core_properties.title or ""):
                return t
    except Exception:   # metadata hỏng không được chặn việc tải lên — quay về đầu mục / tên file
        pass
    if ext not in SHEET | IMAGE:
        for line in text.splitlines()[:60]:
            if m := re.match(r"^#\s+(.+)$", line.strip()):
                if t := sane_title(m.group(1)):
                    return t
    return title_from_filename(filename)
