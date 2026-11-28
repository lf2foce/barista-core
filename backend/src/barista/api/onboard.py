"""Chuyển dữ liệu từ hệ cũ (ADR 0263 mục B, api 1.86): bộ chuyển khai ``Converter`` (``Entity`` theo thứ tự + ``Metric`` đối chiếu), chạy bằng ``load`` — lệnh qua
executor ``historical=True``, sổ mã gốc + dấu vân tay, đối chiếu có ngưỡng. Hợp đồng đầy đủ: ``kernel/onboard.py``; cách viết một bộ chuyển: runbook 23."""

from barista.kernel.onboard import Converter, Entity, Ids, Metric, Problem, Reader, Report, compare, fingerprint, load

__all__ = ["Converter", "Entity", "Ids", "Metric", "Problem", "Reader", "Report", "compare", "fingerprint", "load"]
