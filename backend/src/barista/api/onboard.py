"""Chuyển dữ liệu từ hệ cũ (ADR 0263 mục B, api 1.86): bộ chuyển khai ``Converter`` (``Entity`` theo thứ tự + ``Metric`` đối chiếu), chạy bằng ``load`` — lệnh qua
executor ``historical=True``, sổ mã gốc + dấu vân tay, đối chiếu có ngưỡng; mã đích ``id_key`` / ``target=`` / ``keyless``, người chạy theo thực thể (``actor`` +
``load(actors=)``), người thứ hai duyệt phiếu của lượt (``load(approver_email=)``), giao sự kiện giữa các lệnh (``settle``). Hợp đồng đầy đủ: ``kernel/onboard.py``; cách viết một bộ chuyển: runbook 23."""

from barista.kernel.onboard import (
    Converter,
    ConverterError,
    Entity,
    Ids,
    Metric,
    Problem,
    Reader,
    Report,
    compare,
    fingerprint,
    load,
)

__all__ = ["Converter", "ConverterError", "Entity", "Ids", "Metric", "Problem", "Reader", "Report", "compare", "fingerprint", "load"]
