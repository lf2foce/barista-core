"""Sổ tệp của lõi (0122) — gói CHỈ lưu / đọc / nhả tệp qua đây; bảng của gói giữ ``file_id``, không giữ thân tệp."""

from barista.kernel.files import digest, put, read, release

__all__ = ["digest", "put", "read", "release"]
