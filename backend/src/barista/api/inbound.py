"""Webhook VÀO từ nhà cung cấp ngoài (``Module.inbound`` — 0153, 0172 §2): một cách kiểm chữ ký / khoá / giờ ký cho mọi cửa."""

from barista.kernel.inbound import REASONS, verify_inbound

__all__ = ["REASONS", "verify_inbound"]
