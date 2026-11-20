-- module: core
-- 070 (runbook 15): LOẠI LỖI của lượt model hỏng — ``agent/reliability.classify`` (rate_limit · unavailable · timeout · config · quota · safety ·
-- bad_request); NULL = lượt không hỏng vì nhà cung cấp (xong, hoặc hỏng vì lý do khác). Chỉ THÊM cột có thể trống: bản cũ đang chạy lúc chuyển không
-- ghi cột này vẫn chạy bình thường (không cần dừng dịch vụ). CHECK thêm ở dạng NOT VALID (chỉ chặn dòng MỚI, không quét bảng dưới khoá nặng);
-- 071 kiểm nốt dòng cũ ở bước riêng.
ALTER TABLE core.ai_usage ADD COLUMN IF NOT EXISTS error_class text;
ALTER TABLE core.ai_usage DROP CONSTRAINT IF EXISTS ai_usage_error_class_check;   -- IF EXISTS: chạy lại an toàn
ALTER TABLE core.ai_usage ADD CONSTRAINT ai_usage_error_class_check
    CHECK (error_class IN ('rate_limit', 'unavailable', 'timeout', 'config', 'quota', 'safety', 'bad_request')) NOT VALID;
