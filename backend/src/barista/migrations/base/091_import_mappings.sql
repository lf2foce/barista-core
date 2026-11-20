-- module: core
-- 0226 — ÁNH XẠ CỘT NHẬP EXCEL NHỚ THEO CÔNG TY. Màn nhập tự khớp cột của file vào trường của lệnh; người xác nhận một lần, lần sau file CÙNG BỐ CỤC
-- (cùng tập tiêu đề cột đã chuẩn hoá) nhập không phải khớp lại. Khoá (lệnh, chữ ký tiêu đề) cho từng công ty; ``columns`` = tiêu đề → trường (null = bỏ cột);
-- ``values`` = chữ trong ô → giá trị lựa chọn đã chọn tay (mỗi chữ một lần, lần sau tự áp). Không giữ dữ liệu dòng — chỉ tiêu đề cột + chữ của ô LỰA CHỌN.
-- CHỈ THÊM: bảng mới ⇒ bản cũ chạy song song không biết tới nó (không bảo trì).

CREATE TABLE core.import_mappings (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    command text NOT NULL CHECK (btrim(command) <> ''),
    signature text NOT NULL CHECK (signature ~ '^[0-9a-f]{40}$'),         -- sha1 của tập tiêu đề cột đã chuẩn hoá (không phụ thuộc thứ tự cột)
    name text NOT NULL DEFAULT '' CHECK (char_length(name) <= 300),        -- tên file lần đầu dùng bố cục này (nhãn cho người đọc)
    columns jsonb NOT NULL DEFAULT '{}'::jsonb,                            -- {tiêu đề gốc: khoá trường | null}
    values jsonb NOT NULL DEFAULT '{}'::jsonb,                             -- {khoá trường: {chữ trong ô đã chuẩn hoá: giá trị chọn}}
    uses integer NOT NULL DEFAULT 1 CHECK (uses >= 0),
    created_by uuid REFERENCES core.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, command, signature)
);
CALL core.secure_tenant('core.import_mappings');
CREATE INDEX idx_import_mappings_cmd ON core.import_mappings (tenant_id, command, last_used_at DESC);
GRANT SELECT, INSERT, UPDATE, DELETE ON core.import_mappings TO barista_app;
COMMENT ON TABLE core.import_mappings IS 'Ánh xạ cột nhập Excel đã xác nhận (0226): (lệnh, chữ ký tiêu đề) → trường; quản trị sửa / xoá trong cài đặt';
