-- module: core
-- 0060 lát 1 — TRƯỜNG TUỲ BIẾN THEO CÔNG TY. Định nghĩa trường là DỮ LIỆU của công ty: đối tượng (module khai ``Module.extensible``), khoá ổn định, nhãn,
-- kiểu (chữ · số · ngày · chọn một), lựa chọn, còn dùng hay ngừng. Giá trị KHÔNG nằm ở đây: nằm trong cột ``extra jsonb`` của chính bảng đối tượng
-- (module sở hữu lưu — bất biến 3). Server kiểm kiểu ở executor (mọi cửa như nhau). Không có trường tiền / quyền / trạng thái (0060: phải là hợp đồng của module).
-- CHỈ THÊM: bảng mới ⇒ bản cũ chạy song song không biết tới nó (không bảo trì).

CREATE TABLE core.field_defs (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    subject text NOT NULL CHECK (subject ~ '^[a-z][a-z0-9_]{0,40}$'),       -- đối tượng module khai (vd "lead")
    key text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{0,30}$'),               -- khoá ỔN ĐỊNH (không đổi sau khi tạo) — khoá trong extra jsonb
    label text NOT NULL CHECK (btrim(label) <> '' AND char_length(label) <= 80),
    kind text NOT NULL CHECK (kind IN ('text', 'number', 'date', 'choice')),
    options jsonb NOT NULL DEFAULT '[]'::jsonb,                             -- kiểu "choice": danh sách chữ lựa chọn
    help text NOT NULL DEFAULT '' CHECK (char_length(help) <= 300),
    position integer NOT NULL DEFAULT 0,
    active boolean NOT NULL DEFAULT true,                                   -- ngừng dùng: ẩn khỏi form, giá trị đã lưu vẫn còn
    updated_by uuid REFERENCES core.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, subject, key)
);
CALL core.secure_tenant('core.field_defs');
GRANT SELECT, INSERT, UPDATE ON core.field_defs TO barista_app;
COMMENT ON TABLE core.field_defs IS 'Định nghĩa trường tuỳ biến theo công ty (0060): giá trị ở cột extra của bảng đối tượng; quản trị sửa trong app';
