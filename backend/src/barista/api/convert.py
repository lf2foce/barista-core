"""Đổi tài liệu sang PDF — gói gọi qua đây: ``to_pdf`` (file văn phòng ⇒ PDF để XEM, LibreOffice chạy ngầm, có nhớ — 0102); ``html_to_pdf`` / ``html_to_png`` (trang HTML
MỘT TỆP ⇒ PDF / ảnh bằng trình duyệt không giao diện, không mạng — ấn phẩm, 0252, api 1.76). Máy chủ chưa cài công cụ ⇒ ``Unavailable`` (lời rõ ràng, không lùi đường khác)."""

from barista.kernel.convert import Unavailable, html_to_pdf, html_to_png, to_pdf

__all__ = ["Unavailable", "html_to_pdf", "html_to_png", "to_pdf"]
