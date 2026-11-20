-- module: crm — lý do: đo quy mô thật 11/11 (ADR 0172 §1): chỉ số KPI (crm.metric_calls / metric_connected / metric_leads) lọc kỳ bằng khoảng trên cột
-- mốc (metrics.run(at=…)) thay cho biểu thức ngày bọc cột ⇒ cần chỉ mục theo mốc. Trước: mọi hoạt động / lead của công ty qua RLS rồi mới lọc kỳ
-- (90.000 hoạt động: 130–800 ms mỗi màn KPI đội). Chỉ thêm chỉ mục ⇒ không cần cửa sổ bảo trì.
SET LOCAL lock_timeout = '10s';
CREATE INDEX idx_activities_at ON crm.activities (tenant_id, at);
CREATE INDEX idx_leads_created_at ON crm.leads (tenant_id, created_at);
