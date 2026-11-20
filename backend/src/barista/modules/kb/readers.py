"""Đọc CHỮ từ các loại file người ta thả vào Kho tài liệu (0225) — thuần hàm: không DB, không gọi model (model đọc ảnh / PDF scan ở ``intake``).

* Đọc CHỮ (bảng tính, PowerPoint, Word, PDF, ảnh) nằm ở năng lực ``barista.docread`` (0232) — ở đây chỉ còn danh mục loại file được nhận, ZIP và tên đoán.
* ZIP: bung ra nhiều tài liệu — bỏ rác (``__MACOSX``, ``.DS_Store``, ``~$…``, file ẩn), không bung zip lồng, chặn zip bom (số tệp + tổng cỡ).
* ``hint_title``: tên tài liệu đoán TỪ CHÍNH FILE (metadata / đầu mục đầu tiên) — dùng khi không có model; model đoán được thì ưu tiên.
"""

import io
import zipfile

from barista.docread import types
from barista.docread.titles import hint_title, title_from_filename  # noqa: F401 — đoán tên nằm ở năng lực đọc tài liệu (0232); intake gọi qua ``readers.``
from barista.docread.types import base_name, ext_of
from barista.kernel.errors import Invalid

DOC = types.PDF | types.WORD
TEXT = types.TEXT
SHEET = types.SHEET
SLIDES = types.SLIDES
IMAGE = types.IMAGE
ARCHIVE = {".zip"}
SUPPORTED = DOC | TEXT | SHEET | SLIDES | IMAGE | ARCHIVE
ACCEPT_TEXT = "PDF, Word, Excel / CSV, PowerPoint, ảnh, chữ (.txt .md), hoặc ZIP chứa các loại đó"

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain", ".md": "text/markdown",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12", ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
    ".zip": "application/zip",
}

FILE_MAX = 30 * 1024 * 1024
ZIP_MAX_FILES = 40
ZIP_MAX_TOTAL = 120 * 1024 * 1024
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def content_type(filename: str) -> str:
    return CONTENT_TYPES.get(ext_of(filename), "application/octet-stream")


def is_junk(path: str) -> bool:
    parts = [x for x in path.replace("\\", "/").split("/") if x]
    name = parts[-1].lower() if parts else ""
    return (not parts or "__macosx" in (x.lower() for x in parts) or name in JUNK_NAMES or name.startswith(("~$", "."))
            or any(x.startswith(".") for x in parts[:-1]))


# ── ZIP ──

def _zip_name(i: zipfile.ZipInfo) -> str:
    """Tên trong zip: không đặt cờ UTF-8 thì zipfile giải bằng cp437 ⇒ tiếng Việt vỡ; thử đọc lại như UTF-8."""
    if i.flag_bits & 0x800:
        return i.filename
    try:
        return i.filename.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return i.filename


def expand_zip(data: bytes) -> tuple[list[tuple[str, bytes]], list[dict[str, str]]]:
    """Bung zip ⇒ ([(tên file, nội dung)], [{filename, reason}] đã bỏ qua). Rác và loại chưa hỗ trợ bị bỏ kèm lý do (trừ rác hệ điều hành — im lặng)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise Invalid("File ZIP hỏng hoặc không phải ZIP") from e
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if sum(i.file_size for i in infos) > ZIP_MAX_TOTAL:
        raise Invalid(f"ZIP giải nén ra quá {ZIP_MAX_TOTAL // (1024 * 1024)} MB — tách thành nhiều ZIP nhỏ hơn")
    entries: list[tuple[str, bytes]] = []
    skipped: list[dict[str, str]] = []
    for i in infos:
        name = _zip_name(i)
        if is_junk(name):
            continue
        short = base_name(name)
        ext = ext_of(short)
        if i.flag_bits & 0x1:
            skipped.append({"filename": short, "reason": "file trong ZIP có mật khẩu"})
        elif ext in ARCHIVE:
            skipped.append({"filename": short, "reason": "ZIP lồng trong ZIP — bung ra rồi tải riêng"})
        elif ext not in SUPPORTED:
            skipped.append({"filename": short, "reason": f"loại file {ext or '(không đuôi)'} chưa hỗ trợ"})
        elif i.file_size > FILE_MAX:
            skipped.append({"filename": short, "reason": "file quá 30 MB"})
        elif i.file_size == 0:
            skipped.append({"filename": short, "reason": "file rỗng"})
        elif len(entries) >= ZIP_MAX_FILES:
            skipped.append({"filename": short, "reason": f"ZIP có quá {ZIP_MAX_FILES} file — tải phần còn lại ở lượt sau"})
        else:
            entries.append((short, zf.read(i)))
    return entries, skipped
