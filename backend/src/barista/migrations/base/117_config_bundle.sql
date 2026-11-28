-- module: admin, core — lý do: ADR 0263 mục C — GÓI CẤU HÌNH CÔNG TY (xuất / so / nhập); phần core = luật duyệt mặc định của lệnh nhập cho công ty đã có. Tệp cấu hình tải lên được GIỮ lại (đã đọc + kiểm, dạng JSON) để
-- người xem bảng so khác trên màn rồi mới bấm nhập, và để lệnh nhập sau DUYỆT (chuỗi đổi quyền → chủ tài khoản) chạy lại trên ĐÚNG nội dung người duyệt
-- đã thấy: phiếu duyệt chỉ giữ đầu vào của lệnh (mã tệp), không giữ thân tệp tải lên. Nội dung là cấu hình (vai, quyền, chuỗi duyệt…) — không dữ liệu
-- nghiệp vụ, không tài khoản người dùng (người ghi bằng email để ánh xạ). CHỈ THÊM (bảng mới trong schema admin có sẵn) ⇒ không cần cửa sổ bảo trì.
SET LOCAL lock_timeout = '10s';

CREATE TABLE admin.config_bundles (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    filename        text NOT NULL CHECK (btrim(filename) <> '' AND length(filename) <= 300),
    sha256          text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    config_version  text NOT NULL CHECK (config_version ~ '^[0-9a-f]{64}$'),
    source_company  text NOT NULL DEFAULT '' CHECK (length(source_company) <= 200),
    bundle          jsonb NOT NULL CHECK (jsonb_typeof(bundle) = 'object'),   -- {khoá tài nguyên: [mục]} đã kiểm khớp config_version
    uploaded_by     uuid NOT NULL REFERENCES core.users(id),
    uploaded_at     timestamptz NOT NULL DEFAULT now(),
    applied_by      uuid REFERENCES core.users(id),
    applied_at      timestamptz,
    applied         jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(applied) = 'array')   -- lần nhập gần nhất: mục đã ghi / lỗi kèm lý do
);
CREATE INDEX idx_admin_config_bundles ON admin.config_bundles (tenant_id, uploaded_at DESC);
CALL core.secure_tenant('admin.config_bundles');
GRANT SELECT, INSERT, UPDATE ON admin.config_bundles TO barista_app;

-- Luật duyệt cho lệnh mới ``admin.import_config`` ở MỌI công ty đang có chuỗi "access_change" (deploy chỉ chạy migration; công ty mới nhận luật qua
-- admin.install_defaults — core_ops): nhập cấu hình đổi quyền / vai / chuỗi duyệt ⇒ MỘT phiếu chủ tài khoản cho cả tệp (lệnh ghi lồng cùng chuỗi chạy sau
-- duyệt). Công ty đã cố ý bỏ luật (có nhật ký cài) giữ nguyên. Chỉ thêm dòng.
INSERT INTO core.approval_rules (tenant_id, command, chain)
SELECT c.tenant_id, 'admin.import_config', 'access_change' FROM core.approval_chains c
WHERE c.key = 'access_change'
  AND NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = c.tenant_id AND i.kind = 'rule' AND i.key = 'admin.import_config')
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs (tenant_id, kind, key, pack, version)
SELECT c.tenant_id, 'rule', 'admin.import_config', 'base', NULL FROM core.approval_chains c
WHERE c.key = 'access_change' ON CONFLICT DO NOTHING;
