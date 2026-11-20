-- module: schedule, core — lý do: 0240 — quyền mặc định mới của gói Lịch (xem lịch của mình cho kế toán / marketing) cài cho công ty có trước như 055 / 070 (chỉ thêm dòng core.grants + nhật ký cài, theo dõi bằng pack_installs)
-- 0240 (18/11/2026): màn Lịch kiểu app lịch — kế toán và marketing xem / tạo được lịch CỦA MÌNH (trước đây không mở được phân hệ Lịch). Quyền mặc định
-- mới của gói cho công ty có trước — như 055 / 070: chỉ vai đang có ở công ty, chỉ khi chưa từng cài (có dòng nhật ký ⇒ giữ quyết định của công ty).
WITH want(role, resource, scope, actions) AS (VALUES
    ('accountant', 'schedule', 'own', ARRAY['read']),
    ('marketer',   'schedule', 'own', ARRAY['read']))
INSERT INTO core.grants(tenant_id, role, resource, scope, actions)
SELECT r.tenant_id, r.key, w.resource, w.scope, w.actions
FROM core.roles r JOIN want w ON w.role = r.key
WHERE NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = r.tenant_id AND i.kind = 'grant' AND i.key = r.key || '|' || w.resource)
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs(tenant_id, kind, key, pack, version)
SELECT r.tenant_id, 'grant', r.key || '|' || w.resource, 'schedule', NULL
FROM core.roles r JOIN (VALUES ('accountant', 'schedule'), ('marketer', 'schedule')) AS w(role, resource) ON w.role = r.key
ON CONFLICT DO NOTHING;
