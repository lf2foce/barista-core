"""Đổi file văn phòng sang PDF để XEM (0102) — gói gọi qua đây: ``to_pdf`` (LibreOffice chạy ngầm, có nhớ); máy chủ chưa cài ⇒ ``Unavailable``."""

from barista.kernel.convert import Unavailable, to_pdf

__all__ = ["Unavailable", "to_pdf"]
