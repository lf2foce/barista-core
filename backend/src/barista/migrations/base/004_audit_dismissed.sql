-- module: core
-- 004 (0091): nhật ký thao tác ghi cả lần NGƯỜI BỎ một đề xuất của trợ lý (cùng vết với lượt / thẻ đã soạn — 0086) — tín hiệu cho trí nhớ doanh
-- nghiệp: đề xuất → xác nhận nguyên / sửa (so đầu vào hai dòng cùng vết) / bỏ. Chỉ nới ràng buộc trạng thái — bản cũ còn chạy không bị ảnh hưởng.
ALTER TABLE core.audit_log DROP CONSTRAINT audit_log_status_check;
ALTER TABLE core.audit_log ADD CONSTRAINT audit_log_status_check
    CHECK (status = ANY (ARRAY['ok', 'pending_approval', 'proposed', 'dismissed', 'denied', 'invalid', 'not_found', 'conflict', 'error']));
