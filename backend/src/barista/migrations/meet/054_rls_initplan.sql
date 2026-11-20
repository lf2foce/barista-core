-- module: meet — lý do: N11 — chính sách RLS của gói dùng biến phiên dạng InitPlan `(SELECT core.x())`, tính một lần mỗi câu thay vì mỗi dòng
-- (base/054_rls_initplan.sql). Chỉ đổi cách viết, không đổi nghĩa ⇒ không cần bảo trì. Chạy lại không sao (chữ đã đúng thì bỏ qua).
SET LOCAL lock_timeout = '10s';
CALL core.initplan_policies(ARRAY['meet']);
-- meet.link_call() không tham số, chỉ phụ thuộc biến phiên ⇒ cũng một lần mỗi câu (DEFINER: trước đây gọi lại ở từng dòng cuộc gọi).
ALTER POLICY p_link ON meet.calls USING (tenant_id = (SELECT core.tenant()) AND (SELECT core.link_ref()) IS NOT NULL AND id = (SELECT meet.link_call()));
ALTER POLICY p_no_link ON meet.calls USING ((SELECT core.link_ref()) IS NULL OR id = (SELECT meet.link_call()));
