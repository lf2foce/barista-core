-- module: leadflow
-- 0185 — LEAD ĐỖ. Trạng thái chia mang ``leadflow.lead_state.imported_at`` (nạp từ hệ khác — 0168, hoặc ghi trong một lần nạp lịch sử) là tham chiếu: vòng quét không chấm
-- hạn, không nhắc, không báo trưởng nhóm, không thu hồi, không chia lại (đảo câu "vòng quét xử lý như mọi lead" của crm/075). Barista tự chia lead đó (giao từ hàng chờ,
-- chuyển tay, khoá tài khoản người giữ) ⇒ xoá dấu, lead sống theo luật, hạn tính từ lúc đó.
-- CHỈ THÊM: ghi chú cột + chỉ mục một phần nhỏ (chỉ lead SỐNG đang mở) ⇒ bản cũ chạy song song không vướng, không bảo trì.

SET LOCAL lock_timeout = '10s';

COMMENT ON COLUMN leadflow.lead_state.imported_at IS 'Lead ĐỖ: trạng thái chia nạp từ hệ khác / ghi trong lần nạp lịch sử (0168, 0185) — tham chiếu, vòng quét bỏ qua. NULL = Barista tự chia (lead sống)';
-- vòng quét chọn lead SỐNG theo checked_at — công ty nạp hàng nghìn lead đỗ không phải quét qua chúng mỗi phút
CREATE INDEX idx_lead_state_live ON leadflow.lead_state (tenant_id, checked_at NULLS FIRST, assigned_at)
    WHERE imported_at IS NULL AND status IN ('offered', 'waiting', 'contacted', 'queued');
