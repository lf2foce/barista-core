"""ĐỌC TÀI LIỆU / OCR — MỘT năng lực lõi, bộ đọc cắm được, cấu hình theo bản cài và theo công ty, đo được (ADR 0232).

    from barista.docread import read
    doc = await read(principal, "bang-gia.pdf", data, purpose="kb")
    doc.text()      # markdown, "## Trang N" mở đầu mỗi trang (mốc trích nguồn); bảng là bảng markdown có dòng tiêu đề
    doc.tables      # [Table(header, rows, page)]
    doc.units()     # đơn vị nhúng ổn định: chữ trang · bảng · ảnh (kèm tham chiếu bytes) · âm thanh — cho embedder đa phương thức (0232)
    doc.methods     # {trang: office | pypdf | pdftable | model | <ngoài>}
    doc.usage       # token, chi phí USD, giây, số trang gửi model
    doc.notices     # vì sao một trang KHÔNG được đọc bằng model (công tắc tắt / trần / hết hạn mức / AI tắt / lỗi)

Cấu hình: ``policy.py`` (hồ sơ theo mục đích). Bộ đọc: ``readers/``. Ảnh: ``images.py``. Đo: ``python -m barista.docread.bench``.
"""

from barista.docread import types
from barista.docread.core import Hints, read, read_free
from barista.docread.images import extract as extract_images
from barista.docread.images import read_images
from barista.docread.policy import PURPOSES, Policy
from barista.docread.readers import ExternalReader, Reader, Request, register
from barista.docread.types import AudioRef, Doc, ImageRef, Notice, Page, Table, Unit, Usage, kind_of

__all__ = ["PURPOSES", "AudioRef", "Doc", "ExternalReader", "Hints", "ImageRef", "Notice", "Page", "Policy", "Reader", "Request", "Table", "Unit", "Usage", "extract_images",
           "kind_of", "read", "read_free", "read_images", "register", "types"]
