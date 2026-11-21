-- module: admin — lý do: ADR 0249 lát 2 — NHẬN DIỆN THƯƠNG HIỆU là dữ liệu của CÔNG TY (cài đặt công ty), không của riêng phân hệ ấn phẩm: ấn phẩm, giấy tờ,
-- Word đọc cùng một bộ (màu chính, phông, chân trang, logo). Một dòng mỗi công ty; trống ⇒ mặc định trong code (``kernel/company_settings.BRAND_DEFAULT``).
-- Bảng nằm ở schema của module Quản trị (``admin``) chứ không ở ``core``: logo là một tệp ở kho (0122) và sổ tệp chỉ coi tệp "đang dùng" khi một bảng MODULE
-- trỏ vào nó (khoá ngoại ngoài schema ``core``) — module Quản trị giữ tệp logo, module khác đọc qua thao tác nội bộ ``admin.brand``.
-- Dữ liệu cũ (bảng ``compose.brand``) chép sang ở ``compose/073`` (module sở hữu bảng cũ tự dời). CHỈ THÊM: schema + bảng mới ⇒ không cần cửa sổ bảo trì.
CREATE SCHEMA admin;
GRANT USAGE ON SCHEMA admin TO barista_app;

CREATE TABLE admin.brand (
    tenant_id     uuid PRIMARY KEY DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    primary_color text NOT NULL DEFAULT '1F4E79' CHECK (primary_color ~ '^[0-9A-F]{6}$'),
    font          text NOT NULL DEFAULT 'Arial' CHECK (font IN ('Arial', 'Calibri', 'Times New Roman', 'Roboto', 'Be Vietnam Pro')),
    footer        text NOT NULL DEFAULT '' CHECK (length(footer) <= 200),
    logo_file_id  uuid REFERENCES core.files(id),
    logo_filename text NOT NULL DEFAULT '' CHECK (length(logo_filename) <= 120),
    updated_by    uuid REFERENCES core.users(id),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CALL core.secure_tenant('admin.brand');
GRANT SELECT, INSERT, UPDATE ON admin.brand TO barista_app;
COMMENT ON COLUMN admin.brand.logo_file_id IS 'Tệp logo ở kho tệp (0122) — module admin sở hữu; đọc qua admin.brand';
