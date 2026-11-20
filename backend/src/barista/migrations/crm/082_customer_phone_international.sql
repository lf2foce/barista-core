-- module: crm — 0197: ràng buộc định dạng SĐT của khách phải nhận đúng những gì ``parse_phone`` sinh ra — số Việt Nam dạng nội địa (như cũ) HOẶC số quốc tế
-- E.164 ``+<mã nước>…``. Thiếu migration này thì hàm chuẩn hoá nhận số nước ngoài nhưng ghi khách vẫn vỡ (đo 12/11/2026: 230 lead nạp lại bị chặn ở đây).
-- Nới ràng buộc, mọi dòng cũ vẫn thoả ⇒ bản cũ chạy song song không vỡ ⇒ không cần dừng dịch vụ.
SET LOCAL lock_timeout = '10s';

ALTER TABLE crm.customers DROP CONSTRAINT customers_phone_check;
ALTER TABLE crm.customers ADD CONSTRAINT customers_phone_check CHECK (phone ~ '^(0[0-9]{9,10}|\+[1-9][0-9]{7,14})$');
