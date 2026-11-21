-- module: core — lý do: ADR 0253 — BẢN CHẠY THỬ CHUẨN của việc chờ người duyệt (hồ sơ · cũ → mới · vì sao · đảo được không), lưu cùng đề xuất và phiếu duyệt.
--   Thẻ đề xuất (core.proposals) và phiếu duyệt (core.approvals) vẽ CÙNG MỘT hợp đồng; lõi dựng nó lúc chạy thử (kernel/preview.py) và giữ ở đây để mọi nơi
--   mở lại (Việc, thông báo, danh sách "Chờ tôi duyệt") đọc đúng một thứ. Cột trống được, không mặc định ⇒ dòng cũ chưa có bản mô tả (thẻ vẽ tên lệnh như trước).
-- Chỉ THÊM cột: bản cũ chạy song song không đọc / ghi cột này ⇒ không bảo trì.
ALTER TABLE core.proposals ADD COLUMN preview jsonb;
ALTER TABLE core.approvals ADD COLUMN preview jsonb;
