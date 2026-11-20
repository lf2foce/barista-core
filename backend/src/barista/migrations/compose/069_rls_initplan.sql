-- module: compose — lý do: N11 (như base/054_rls_initplan.sql) — chính sách RLS của gói dùng biến phiên dạng InitPlan ``(SELECT core.x())``, tính một lần mỗi câu lệnh.
-- Bản gốc của gói (049, dời từ gói nền) tạo chính sách với ``core.tenant()`` / ``core.me()`` trần; ở bản cài cũ base/054 đã viết lại chỗ này (hàm idempotent),
-- ở bản cài mới schema ``compose`` chưa có lúc base/054 chạy ⇒ viết lại ở đây. CHỈ ĐỔI CÁCH VIẾT, không đổi nghĩa chính sách.
CALL core.initplan_policies(ARRAY['compose']);
