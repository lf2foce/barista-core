"""Đọc tài liệu / OCR cho gói (1.52.0 — ADR 0232): ``read`` (PDF / Word / Excel / PowerPoint / ảnh → ``Doc``: chữ markdown, bảng, ảnh, phương pháp từng trang, số đo, thông báo),
``read_images`` (chép chữ / mô tả một loạt ảnh, mỗi ảnh trả tiền một lần), ``extract_images`` (lấy ảnh trong PDF / Word / PowerPoint — miễn phí), ``read_free`` (chỉ bộ miễn phí, đồng bộ).
Model + trần + công tắc là hồ sơ của công ty (nhà vận hành đặt) — gói chỉ nêu MỤC ĐÍCH (``purpose``); AI tắt / hết hạn mức ⇒ ``Doc.notices``, không ném lỗi."""

from barista.docread import (
    AudioRef,
    Doc,
    Hints,
    ImageRef,
    Notice,
    Page,
    Table,
    Unit,
    Usage,
    extract_images,
    read,
    read_free,
    read_images,
)

__all__ = ["AudioRef", "Doc", "Hints", "ImageRef", "Notice", "Page", "Table", "Unit", "Usage", "extract_images", "read", "read_free", "read_images"]
