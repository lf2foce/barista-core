-- module: core
-- 071: kiểm nốt ràng buộc của 070 trên dòng đã có (cột mới toàn NULL ⇒ qua ngay). Bước riêng: VALIDATE chỉ giữ khoá nhẹ (không chặn ghi), không
-- kéo dài khoá nặng của ALTER ở 070 — cùng giao dịch thì khoá nặng giữ tới hết.
ALTER TABLE core.ai_usage VALIDATE CONSTRAINT ai_usage_error_class_check;
