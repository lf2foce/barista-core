-- module: acct
-- 0211: NGÀY ĐẾN HẠN của nghĩa vụ phải trả — nguồn nêu lúc ghi (vd phiếu lương: ngày chi lương) để Dòng tiền dự phóng xếp khoản chi vào đúng tháng
-- thay vì dồn hết vào tháng này. Trống = như trước (dồn vào tháng này). Cột cho phép trống, không mặc định, không chỉ mục ⇒ bản cũ không đọc / không
-- ghi, chạy song song đúng ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
ALTER TABLE acct.payables ADD COLUMN due_on DATE;
COMMENT ON COLUMN acct.payables.due_on IS 'Ngày đến hạn chi (nguồn nêu lúc ghi nghĩa vụ); trống = chưa hẹn — dòng tiền dự phóng xếp vào tháng hiện tại';
