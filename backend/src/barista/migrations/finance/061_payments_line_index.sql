-- module: acct — lý do: hiệu năng (perf doc §16). Kiểm "dòng sao kê lệch phân bổ" (Sức khoẻ dữ liệu, Tổng quan điều hành) cộng thanh toán / phiếu thu hồi
-- THEO DÒNG SAO KÊ, nhưng hai bảng không có chỉ mục theo line_id ⇒ mỗi dòng sao kê quét lại cả bảng (đo 10/11: 6.694 dòng × 694 thanh toán = 214ms, kể cả
-- bằng owner). Thêm chỉ mục; không đổi dữ liệu, không đổi nghĩa. Bảng nhỏ ⇒ khoá ghi rất ngắn, không cần bảo trì.
SET LOCAL lock_timeout = '10s';
CREATE INDEX IF NOT EXISTS idx_payments_line ON acct.payments (line_id);
CREATE INDEX IF NOT EXISTS idx_recovery_receipts_line ON acct.recovery_receipts (line_id);
