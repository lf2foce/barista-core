-- module: schedule, core — lý do: 0157 — gói Lịch & nguồn lực thành gói LUÔN BẬT (Pack(always_on=True), như gói nền): không còn dòng core.tenant_packs, mọi công ty
-- đều có lịch / người / phòng. Phát hành chỉ chạy migration (không seed --upgrade) nên công ty đang chạy phải nhận phần mặc định của gói ở đây: quyền mặc định
-- theo vai (Pack.grants của gói) cho công ty CHƯA từng cài + nhật ký cài. Chỉ thêm: quyền công ty đã có / đã gỡ (có dòng nhật ký) giữ nguyên; vai chưa
-- có ở công ty thì chưa cài (lần có vai, install_defaults / seed --upgrade bổ sung). Dòng tenant_packs 'schedule' đang có giữ nguyên (vô hại: bản cũ chạy song
-- song vẫn thấy gói bật); bản mới đọc "luôn bật" từ khai báo gói. Không bảo trì: chỉ thêm dòng.
WITH want(role, scope, actions) AS (VALUES
    ('admin',    'all', ARRAY['read', 'write', 'manage']),
    ('office',   'all', ARRAY['read', 'write', 'manage']),
    ('director', 'all', ARRAY['read']),
    ('staff',    'own', ARRAY['read']))
INSERT INTO core.grants(tenant_id, role, resource, scope, actions)
SELECT r.tenant_id, r.key, 'schedule', w.scope, w.actions
FROM core.roles r JOIN want w ON w.role = r.key
WHERE NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = r.tenant_id AND i.kind = 'grant' AND i.key = r.key || '|schedule')
ON CONFLICT DO NOTHING;

INSERT INTO core.pack_installs(tenant_id, kind, key, pack, version)
SELECT r.tenant_id, 'grant', r.key || '|schedule', 'schedule', NULL
FROM core.roles r
WHERE r.key IN ('admin', 'office', 'director', 'staff')
ON CONFLICT DO NOTHING;
