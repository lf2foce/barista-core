-- module: core — lý do: từ vựng theo công ty (0124, bảng core.vocabulary); công ty đang bật Bán hàng BĐS giữ chữ "Dự án" như trước khi lõi nói chữ trung tính (0110); KPI và Marketing thành năng lực chung (0124 §3).
-- Một file cho cả đợt N1 (chưa phát hành): phần từ vựng ở đầu, phần gói KPI / Marketing nối ở cuối.

-- Từ vựng: khoá khái niệm (code khai bằng Module.term) → nhãn công ty gọi. Dữ liệu của công ty, quản trị sửa trong app (admin.set_term).
CREATE TABLE core.vocabulary (
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    term       TEXT NOT NULL CHECK (term ~ '^[a-z][a-z0-9_]*$'),
    label      TEXT NOT NULL CHECK (btrim(label) <> '' AND length(label) <= 40 AND position('[[' IN label) = 0),
    updated_by UUID REFERENCES core.users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, term)
);
CALL core.secure_tenant('core.vocabulary');
GRANT SELECT, INSERT, UPDATE, DELETE ON core.vocabulary TO barista_app;
COMMENT ON TABLE core.vocabulary IS 'Từ vựng công ty (0124): chỉ đổi chữ hiện ra, không đổi khoá / cột / tên lệnh';

-- Mặc định của gói ghi vào nhật ký cài như mọi mục mặc định khác (công ty xoá / sửa rồi thì bật lại gói không khôi phục)
ALTER TABLE core.pack_installs DROP CONSTRAINT pack_installs_kind_check;
ALTER TABLE core.pack_installs ADD CONSTRAINT pack_installs_kind_check
    CHECK (kind = ANY (ARRAY['role', 'grant', 'team', 'chain', 'rule', 'flow', 'vocab']));

-- Công ty đang bật Bán hàng BĐS: thứ khách quan tâm là "Dự án" (nhãn mặc định của gói) — để người dùng không thấy chữ trung tính "Sản phẩm" sau khi lõi tổng quát.
INSERT INTO core.vocabulary (tenant_id, term, label)
SELECT tenant_id, 'offering', 'Dự án' FROM core.tenant_packs WHERE pack = 'sales' ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs (tenant_id, kind, key, pack, version)
SELECT tenant_id, 'vocab', 'offering', 'sales', NULL FROM core.tenant_packs WHERE pack = 'sales' ON CONFLICT DO NOTHING;

-- KPI thành gói năng lực riêng `kpi` (0124): trước nằm trong Bán hàng BĐS. Công ty đang bật Bán hàng nhận gói KPI (đang bật + quyền dùng) để không ai mất màn;
-- quyền mặc định của gói KPI cài cho công ty chưa có (giữ nguyên dòng công ty đã sửa / đã xoá — nhật ký cài ghi ai đã từng được cài). Marketing đã chỉ cần Khách
-- hàng từ 0110; đổi loại gói là của code (năng lực chung), không có dòng dữ liệu nào đổi.
INSERT INTO core.tenant_packs (tenant_id, pack) SELECT tenant_id, 'kpi' FROM core.tenant_packs WHERE pack = 'sales' ON CONFLICT DO NOTHING;
INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note)
SELECT tenant_id, 'kpi', 'migration 037', 'KPI tách khỏi Bán hàng BĐS' FROM core.tenant_entitlements WHERE pack = 'sales' ON CONFLICT DO NOTHING;
UPDATE core.pack_installs SET pack = 'kpi' WHERE kind = 'grant' AND key LIKE '%|kpi';
-- Màn KPI chuyển từ phân hệ "Kinh doanh" sang phân hệ "KPI & xếp hạng": ghim của từng người (địa chỉ = phân hệ + khoá màn) trỏ theo, không thành ghim ẩn.
UPDATE core.user_pins SET workspace = 'kpi' WHERE workspace = 'sales' AND key IN ('kpi_mine', 'kpi_board', 'kpi_ranking');
INSERT INTO core.grants (tenant_id, role, resource, scope, actions)
SELECT p.tenant_id, v.role, 'kpi', v.scope, v.actions
FROM core.tenant_packs p
CROSS JOIN (VALUES ('admin', 'all', ARRAY['read', 'write', 'manage']), ('director', 'all', ARRAY['read', 'write', 'manage']),
                   ('team_lead', 'team', ARRAY['read', 'write']), ('sale', 'own', ARRAY['read']),
                   ('sales_admin', 'all', ARRAY['read', 'write'])) AS v(role, scope, actions)
JOIN core.roles r ON r.tenant_id = p.tenant_id AND r.key = v.role
WHERE p.pack = 'kpi'
  AND NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = p.tenant_id AND i.kind = 'grant' AND i.key = v.role || '|kpi')
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs (tenant_id, kind, key, pack, version)
SELECT p.tenant_id, 'grant', v.role || '|kpi', 'kpi', NULL
FROM core.tenant_packs p CROSS JOIN (VALUES ('admin'), ('director'), ('team_lead'), ('sale'), ('sales_admin')) AS v(role)
JOIN core.roles r ON r.tenant_id = p.tenant_id AND r.key = v.role
WHERE p.pack = 'kpi' ON CONFLICT DO NOTHING;
