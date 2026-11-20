-- module: acct
-- 0183 — CHUYỂN PHÁP NHÂN của phiếu chi phí đã ghi: không bao giờ UPDATE entity_id của tiền đã ghi (sổ cái chỉ thêm). Chuyển = ĐẢO bút toán ở pháp nhân cũ
-- (phiếu cũ ghi huỷ, lý do "chuyển pháp nhân") + GHI LẠI ở pháp nhân mới (phiếu mới). Phiếu mới mang ``transferred_from_id`` trỏ phiếu cũ để truy vết cả hai
-- chiều (phiếu cũ tra ngược: phiếu nào có transferred_from_id = nó). CHỈ THÊM: cột cho phép trống, chỉ mục duy nhất một phần (mỗi phiếu cũ chỉ có MỘT phiếu thay
-- thế còn hiệu lực) ⇒ bản cũ chạy song song vẫn ghi được, không cần cửa sổ bảo trì. Rào "phiếu chỉ thêm" (070) vẫn nguyên: cột mới điền lúc tạo, không sửa sau.

SET LOCAL lock_timeout = '10s';

ALTER TABLE acct.bills ADD COLUMN transferred_from_id UUID,
                       ADD CONSTRAINT bills_transferred_from_fk FOREIGN KEY (tenant_id, transferred_from_id) REFERENCES acct.bills (tenant_id, id),
                       ADD CONSTRAINT bills_transferred_not_self CHECK (transferred_from_id IS NULL OR transferred_from_id <> id);
CREATE UNIQUE INDEX uq_bills_transferred_from ON acct.bills (tenant_id, transferred_from_id) WHERE transferred_from_id IS NOT NULL AND cancelled_on IS NULL;
COMMENT ON COLUMN acct.bills.transferred_from_id IS 'Phiếu cũ mà phiếu này thay thế khi chuyển pháp nhân (đảo ở pháp nhân cũ, ghi lại ở pháp nhân mới) — 0183';
