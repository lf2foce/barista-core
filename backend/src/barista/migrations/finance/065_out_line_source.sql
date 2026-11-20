-- module: acct
-- ADR 0151, sửa sau review 10/11: dòng tiền ra biết NGUỒN — chỉ dòng từ file sao kê làm bằng chứng "đã thấy trên sao kê"; dòng gõ tay / ảnh không đóng
-- được cảnh báo lệnh chi chưa thấy. Cột có mặc định ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
ALTER TABLE acct.bank_out_lines ADD COLUMN source TEXT NOT NULL DEFAULT 'excel' CHECK (source IN ('excel', 'image', 'manual'));
