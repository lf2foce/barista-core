-- module: papers — lý do: 0118 lát 1 — mẫu văn bản có phiên bản + bản soạn điền từ hồ sơ (nguồn từng ô) + tệp đã ban hành ở kho tệp (0122)
-- CHỈ THÊM: bảng mới, cột mới có mặc định, nới CHECK khuôn trình bày (thêm 'contract'). Bản cũ chạy song song lúc chuyển không đọc / ghi các
-- bảng này, dòng nó thêm vào papers.documents nhận mặc định (không mẫu, rev 0) ⇒ không cần cửa sổ bảo trì (không vào deployment.json).

-- Mẫu: một LOẠI văn bản công ty dùng (kind của giấy = 'mau_' || key ⇒ dãy số riêng theo loại / năm, không trùng loại giấy của gói). Đọc: mọi người trong công ty (chọn mẫu
-- khi soạn); ghi: lệnh khai tài nguyên papers.templates (executor).
CREATE TABLE papers.templates (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    key text NOT NULL,
    name text NOT NULL,
    subject_kind text NOT NULL,
    abbr text DEFAULT ''::text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_by uuid REFERENCES core.users(id),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT templates_key_check CHECK (key ~ '^[a-z][a-z_]{1,39}$' AND key <> 'letter'),
    CONSTRAINT templates_name_check CHECK (btrim(name) <> '' AND length(name) <= 120),
    CONSTRAINT templates_abbr_check CHECK (abbr ~ '^[A-ZĐ0-9]{0,12}$'),
    UNIQUE (tenant_id, key)
);
CALL core.secure_tenant('papers.templates');

-- Phiên bản mẫu: thân Markdown có ô {{khoá}} (đọc / diff được). Đã phát hành ⇒ KHÔNG sửa / xoá (trigger) — bản soạn trỏ đúng phiên bản đã dùng.
CREATE TABLE papers.template_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    template_id uuid NOT NULL REFERENCES papers.templates(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version >= 1),
    body text NOT NULL CHECK (length(body) BETWEEN 1 AND 60000),
    signers text[] DEFAULT '{}'::text[] NOT NULL CHECK (cardinality(signers) <= 3),
    status text DEFAULT 'draft'::text NOT NULL CHECK (status IN ('draft', 'published')),
    created_by uuid REFERENCES core.users(id),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    published_by uuid REFERENCES core.users(id),
    published_at timestamp with time zone,
    CONSTRAINT template_versions_published_check CHECK (status = 'draft' OR (published_by IS NOT NULL AND published_at IS NOT NULL)),
    UNIQUE (template_id, version)
);
CALL core.secure_child('papers.template_versions', 'papers.templates', 'template_id');
CREATE UNIQUE INDEX uq_template_versions_one_draft ON papers.template_versions (template_id) WHERE status = 'draft';

-- Chỉ chặn SỬA: app không có quyền DELETE bảng này; xoá theo dây chuyền khi xoá công ty (kịch bản eval / dọn) vẫn chạy.
CREATE FUNCTION papers.template_version_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'published' THEN
        RAISE EXCEPTION 'Phiên bản mẫu đã phát hành không sửa được — tạo phiên bản mới' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_template_version_frozen BEFORE UPDATE ON papers.template_versions
    FOR EACH ROW EXECUTE FUNCTION papers.template_version_frozen();

-- Giấy soạn từ mẫu: thêm cột vào danh sách giấy tờ chung (một danh sách, một dãy số). rev = bản hiện hành (0 = giấy không theo mẫu).
ALTER TABLE papers.documents
    ADD COLUMN template_version_id uuid REFERENCES papers.template_versions(id),
    ADD COLUMN subject_kind text DEFAULT ''::text NOT NULL,
    ADD COLUMN subject_id uuid,
    ADD COLUMN rev integer DEFAULT 0 NOT NULL CHECK (rev >= 0);
ALTER TABLE papers.documents DROP CONSTRAINT documents_layout_check,
    ADD CONSTRAINT documents_layout_check CHECK (layout = ANY (ARRAY['official'::text, 'handout'::text, 'contract'::text]));
CREATE INDEX idx_documents_template ON papers.documents (tenant_id, template_version_id) WHERE template_version_id IS NOT NULL;

-- Mỗi bản (rev) của bản soạn — CHỈ THÊM: cách ra bản (từ mẫu / cập nhật số / người nhập), dấu số liệu hồ sơ, chữ đã điền, ô thiếu, thay đổi so
-- với bản trước.
CREATE TABLE papers.revisions (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES papers.documents(id) ON DELETE CASCADE,
    rev integer NOT NULL CHECK (rev >= 1),
    via text NOT NULL CHECK (via IN ('template', 'refresh', 'person')),
    mark text NOT NULL,
    body text NOT NULL,
    missing text[] DEFAULT '{}'::text[] NOT NULL,
    changes jsonb DEFAULT '[]'::jsonb NOT NULL CHECK (jsonb_typeof(changes) = 'array'),
    created_by uuid REFERENCES core.users(id),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    UNIQUE (document_id, rev)
);
CALL core.secure_child('papers.revisions', 'papers.documents', 'document_id');

-- Ô của từng bản — CHỈ THÊM: giá trị + chữ in + NGUỒN (record = đọc từ hồ sơ bằng quyền người soạn, kèm hồ sơ nào; person = người nhập, ai,
-- lúc nào, vì sao). Ô thiếu: value NULL.
CREATE TABLE papers.fields (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES papers.documents(id) ON DELETE CASCADE,
    rev integer NOT NULL CHECK (rev >= 1),
    key text NOT NULL,
    label text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('text', 'number', 'amount', 'date', 'rows')),
    value jsonb,
    display text DEFAULT ''::text NOT NULL,
    origin text NOT NULL CHECK (origin IN ('record', 'person')),
    source jsonb DEFAULT '{}'::jsonb NOT NULL,
    reason text DEFAULT ''::text NOT NULL CHECK (length(reason) <= 500),
    entered_by uuid REFERENCES core.users(id),
    entered_at timestamp with time zone,
    CONSTRAINT fields_person_check CHECK (origin = 'record' OR (entered_by IS NOT NULL AND entered_at IS NOT NULL AND value IS NOT NULL)),
    UNIQUE (document_id, rev, key)
);
CALL core.secure_child('papers.fields', 'papers.documents', 'document_id');

-- Tệp của giấy (0122): thân ở kho đối tượng, đây chỉ tham chiếu sổ tệp. Bản ban hành lưu lúc cấp số ⇒ tải lại luôn đúng tệp đã phát hành.
CREATE TABLE papers.files (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES papers.documents(id) ON DELETE CASCADE,
    rev integer NOT NULL CHECK (rev >= 0),
    file_id uuid NOT NULL REFERENCES core.files(id),
    role text NOT NULL CHECK (role IN ('issued')),
    filename text NOT NULL,
    created_by uuid REFERENCES core.users(id),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    UNIQUE (file_id),
    UNIQUE (document_id, role)
);
CALL core.secure_child('papers.files', 'papers.documents', 'document_id');

GRANT SELECT, INSERT ON papers.templates, papers.template_versions, papers.revisions, papers.fields, papers.files TO barista_app;
GRANT UPDATE (name, abbr, active) ON papers.templates TO barista_app;
GRANT UPDATE (body, signers, status, published_by, published_at) ON papers.template_versions TO barista_app;
