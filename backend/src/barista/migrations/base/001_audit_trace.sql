-- module: core
-- 001: MÃ VẾT của nhật ký thao tác — nối lệnh trợ lý chạy / soạn với nơi sinh ra nó (lượt trò chuyện, đề xuất buổi sáng, việc theo dõi) và với
-- lần người bấm xác nhận đề xuất đó: "lệnh này do lượt nào, ai quyết". '' = thao tác người làm trực tiếp (không qua trợ lý). Thêm cột có mặc định
-- ⇒ bản cũ còn chạy lúc chuyển không bị ảnh hưởng.
ALTER TABLE core.audit_log ADD COLUMN trace TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_audit_trace ON core.audit_log (tenant_id, trace) WHERE trace <> '';
