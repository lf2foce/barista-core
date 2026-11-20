-- module: flow
-- 092 (ADR 0222 §15/11, barista-docs): NHẬP KẾ HOẠCH TỪ EXCEL — mỗi việc nhớ MÃ của dòng đó trong file kế hoạch (STT / WBS: "1.2.3") để lần nhập lại một file đã
-- sửa CẬP NHẬT đúng việc (đổi ngày, đổi tên) thay vì tạo bản trùng. Trống = việc tạo tay / bởi trợ lý / file không có cột mã (khi đó khớp bằng tên việc).
-- CHỈ THÊM cột tuỳ chọn ⇒ bản cũ chạy song song không đọc / ghi gì ở đây, không cần dừng dịch vụ.

SET LOCAL lock_timeout = '10s';

ALTER TABLE flow.tasks ADD COLUMN plan_ref text;
ALTER TABLE flow.tasks ADD CONSTRAINT tasks_plan_ref_len CHECK (plan_ref IS NULL OR char_length(plan_ref) <= 60);
CREATE INDEX tasks_plan_ref_idx ON flow.tasks (plan_id, plan_ref) WHERE plan_ref IS NOT NULL;
