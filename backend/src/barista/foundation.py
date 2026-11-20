"""Điểm vào của gói NỀN (``base``, luôn bật — 0045): nạp các module của gói. Gói khai ở ``kernel/core_ops.py`` cùng module ``core``; thứ tự
nạp các gói khác: ``kernel/loader.py`` (0130)."""

from barista.modules import admin, apps, asks, connect, connectors, flow, health, kb, memory, papers, work  # noqa: F401
