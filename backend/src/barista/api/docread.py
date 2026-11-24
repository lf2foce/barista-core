"""Đọc tài liệu / OCR cho gói (1.52.0 — ADR 0232): ``read`` (PDF / Word / Excel / PowerPoint / ảnh → ``Doc``: chữ markdown, bảng, ảnh, phương pháp từng trang, số đo, thông báo),
``read_images`` (chép chữ / mô tả một loạt ảnh, mỗi ảnh trả tiền một lần), ``extract_images`` (lấy ảnh trong PDF / Word / PowerPoint — miễn phí), ``read_free`` (chỉ bộ miễn phí, đồng bộ).
Model + trần + công tắc là hồ sơ của công ty (nhà vận hành đặt) — gói chỉ nêu MỤC ĐÍCH (``purpose``); AI tắt / hết hạn mức ⇒ ``Doc.notices``, không ném lỗi.

1.85.0 (ADR 0262): GÓI BỘ ĐỌC khai engine đọc riêng (OCR trên máy, dịch vụ tài liệu) — lớp con ``ExternalReader`` (``name`` / ``paid`` / ``kinds`` / ``read(req, doc)``
điền ``doc.pages`` + cộng ``doc.usage``) rồi ``register(...)`` lúc nạp gói; công ty chọn bộ đó bằng chuỗi của hồ sơ đọc (``purpose``). Tên trùng bộ đã có ⇒ từ chối.
``paid = True`` ⇒ chịu trần trang / token như ``model``; gửi dữ liệu ra ngoài ⇒ chỉ khi công ty đã đồng ý (0193)."""

from barista.docread import (
    AudioRef,
    Doc,
    ExternalReader,
    Hints,
    ImageRef,
    Notice,
    Page,
    Reader,
    Request,
    Table,
    Unit,
    Usage,
    extract_images,
    read,
    read_free,
    read_images,
    register,
)

__all__ = ["AudioRef", "Doc", "ExternalReader", "Hints", "ImageRef", "Notice", "Page", "Reader", "Request", "Table", "Unit", "Usage", "extract_images", "read",
           "read_free", "read_images", "register"]
