"""Sổ tệp của lõi (0122, ADR 0249) — gói CHỈ lưu / đọc / nhả tệp qua đây; bảng của gói giữ ``file_id``, không giữ thân tệp.

1.72 (ADR 0249): một tệp NHIỀU CHỖ DÙNG — module đang giữ tệp ``grant(ctx, file_id, "<module đích>")`` (sau khi kiểm người xem thấy chỗ dùng của mình),
module đích ``adopt(ctx, file_id, grant)`` (giữ CÙNG tệp, thêm dòng của mình) hoặc ``copy`` (bản độc lập) trong cùng giao dịch. Loại tệp nhận được
theo nội dung: ``sniff`` (+ ``ACCEPTED`` chữ hiện ra), tên an toàn theo loại: ``clean_name``, nhãn cho người đọc: ``kind_label`` (``MEDIA`` = ảnh), ``size_text``.
``signed_url`` (1.73): link ký ≤ 60 giây để HIỂN THỊ ảnh / PDF (``inline=True``) trên thẻ — cấp sau khi kiểm quyền; loại chạy được (svg, html) không bao giờ có link."""

from barista.kernel.files import (
    ACCEPTED,
    MEDIA,
    adopt,
    clean_name,
    copy,
    digest,
    grant,
    kind_label,
    put,
    read,
    release,
    signed_url,
    size_text,
    sniff,
)

__all__ = ["ACCEPTED", "MEDIA", "adopt", "clean_name", "copy", "digest", "grant", "kind_label", "put", "read", "release", "signed_url", "size_text", "sniff"]
