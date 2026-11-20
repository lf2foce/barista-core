"""Lịch sử thay đổi theo trường (ADR 0246, api 1.69). Khai trường bằng ``m.tracks(loại, trường, nhãn, fmt=…, needs=…)`` (đối tượng của chính module, đã
khai ``m.subject``); lệnh đổi trường gọi ``await ctx.track(loại, id, trường, cũ, mới)`` trong handler (cùng giao dịch). Thẻ của module đặt khối
``await history.block(ctx, loại, id)`` cạnh khối trao đổi (None = chưa có gì người xem thấy). Trợ lý hỏi qua ``core.history``. Hợp đồng: ``kernel/history.py``."""

from barista.kernel.history import block

__all__ = ["block"]
