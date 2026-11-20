-- module: mkt — lý do: N11 — chính sách RLS của gói dùng biến phiên dạng InitPlan `(SELECT core.x())`, tính một lần mỗi câu thay vì mỗi dòng
-- (base/054_rls_initplan.sql). Chỉ đổi cách viết, không đổi nghĩa ⇒ không cần bảo trì. Chạy lại không sao (chữ đã đúng thì bỏ qua).
SET LOCAL lock_timeout = '10s';
CALL core.initplan_policies(ARRAY['mkt']);
