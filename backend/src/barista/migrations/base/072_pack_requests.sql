-- module: core
-- 0163: quản trị công ty xin MỞ một gói bán được mà bản cài chưa có (thẻ "Chưa có trên bản cài" ở Store). Chỉ ghi yêu cầu — không giá, không bật
-- gì: nhà cung cấp đọc bảng này (kênh vận hành) rồi mới cài / cấp quyền (barista.entitle). Mỗi (công ty, gói) một dòng — xin lại không thêm dòng.
-- Bảng mới, chỉ thêm ⇒ bản cũ chạy song song không ảnh hưởng.
CREATE TABLE core.pack_requests (
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    pack text NOT NULL CHECK (pack ~ '^[a-z][a-z0-9_]{0,40}$'),
    requested_by uuid REFERENCES core.users(id),
    requested_at timestamptz NOT NULL DEFAULT now(),
    note text NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    PRIMARY KEY (tenant_id, pack)
);
CALL core.secure_tenant('core.pack_requests');
GRANT SELECT, INSERT ON core.pack_requests TO barista_app;
