"""Tệp của hồ sơ (ADR 0249, api 1.72). Khai đối tượng nhận tệp bằng ``m.record_files(loại, labels=(…))`` (đối tượng của chính module, đã khai
``m.subject``); thẻ của module đặt khối ``await record_files.block(ctx, loại, id)`` cạnh khối trao đổi / lịch sử (None = loại không nhận tệp / người xem
không thấy hồ sơ). Gắn: lệnh ``core.attach_file`` (tải lên qua ``POST /v1/record_files``, hoặc theo tham chiếu: ``files.grant(ctx, file_id, "core")`` rồi
``ctx.call("core.attach_file", subject_kind=…, subject_id=…, file_id=…, grant=…)``); gỡ ``core.remove_file``; trợ lý hỏi ``core.files_of``.
Hợp đồng: ``kernel/record_files.py``."""

from barista.kernel.record_files import block

__all__ = ["block"]
