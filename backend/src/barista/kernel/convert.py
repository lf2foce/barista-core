"""Đổi tài liệu văn phòng sang PDF để XEM trong app (quyết định 0102) — MỘT trình xem (PDF) cho mọi loại file: PDF giữ nguyên; Word / Excel /
PowerPoint / OpenDocument đổi bằng LibreOffice chạy ngầm (``soffice --headless``). Chỉ để xem — bản gốc vẫn là thứ được lưu / tải về.

* Máy chưa cài LibreOffice (dev) ⇒ ``Unavailable``: app quay về "tải về" như cũ, không lỗi.
* Chạy tối đa 2 lượt đổi cùng lúc (LibreOffice nặng); mỗi lượt một hồ sơ người dùng riêng trong thư mục tạm (không khoá nhau); quá 90 giây ⇒ huỷ.
* Nhớ kết quả trong tiến trình theo băm nội dung (24 file gần nhất) — mở lại cùng file không đổi lại.
* File của người dùng là dữ liệu không tin được: chỉ đổi định dạng, không chạy macro (LibreOffice chế độ không giao diện không chạy macro).
"""

import asyncio
import hashlib
import shutil
import tempfile
from collections import OrderedDict
from pathlib import Path

from barista.config import settings

OFFICE = frozenset({".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".pptx", ".ppt", ".odp"})
CACHE_MAX = 24
TIMEOUT_S = 90
_cache: OrderedDict[str, bytes] = OrderedDict()
_slots = asyncio.Semaphore(2)


class Unavailable(Exception):
    """Không xem trước được (máy chủ chưa cài LibreOffice / loại file không hỗ trợ / đổi lỗi) — app cho tải file gốc thay vì xem."""


def binary() -> str | None:
    return settings.soffice_path or shutil.which("soffice") or shutil.which("libreoffice")


def viewable(filename: str) -> bool:
    """Loại file xem được trong app (PDF, hoặc đổi được sang PDF)."""
    ext = Path(filename).suffix.lower()
    return ext == ".pdf" or ext in OFFICE


def _convert(exe: str, ext: str, data: bytes) -> bytes:
    import subprocess

    with tempfile.TemporaryDirectory(prefix="barista-pdf-") as d:
        src = Path(d) / f"in{ext}"
        src.write_bytes(data)
        try:
            subprocess.run([exe, f"-env:UserInstallation=file://{d}/profile", "--headless", "--norestore", "--convert-to", "pdf",
                            "--outdir", d, str(src)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as e:
            raise Unavailable("Đổi sang PDF quá lâu — tải file về để xem") from e
        out = Path(d) / "in.pdf"
        if not out.exists() or out.stat().st_size == 0:
            raise Unavailable("Không đổi được file này sang PDF — tải file về để xem")
        return out.read_bytes()


async def to_pdf(filename: str, data: bytes) -> bytes:
    """PDF để xem: PDF ⇒ chính nó; file văn phòng ⇒ đổi (có nhớ); loại khác / chưa cài LibreOffice ⇒ ``Unavailable``."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return data
    if ext not in OFFICE:
        raise Unavailable(f"Chưa xem trước được loại file {ext or '(không đuôi)'} — tải file về để xem")
    key = hashlib.sha256(data).hexdigest() + ext
    if (hit := _cache.get(key)) is not None:
        _cache.move_to_end(key)
        return hit
    exe = binary()
    if exe is None:
        raise Unavailable("Máy chủ chưa cài LibreOffice nên chưa xem trước được file Word / Excel — tải file về để xem")
    async with _slots:
        pdf = await asyncio.to_thread(_convert, exe, ext, data)
    _cache[key] = pdf
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return pdf
