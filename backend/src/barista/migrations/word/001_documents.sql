-- module: word
-- 001 (0227): VĂN BẢN WORD sửa ngay trong app. Mỗi văn bản là một file .docx ở kho tệp (0122: thân ở kho đối tượng, đây chỉ tham chiếu); mỗi lần
-- lưu là MỘT PHIÊN BẢN MỚI (chỉ thêm — bản cũ còn nguyên, khôi phục = thêm phiên bản chép từ bản cũ). Bản đầu: văn bản của RIÊNG người tạo (như
-- bản soạn của Soạn với AI / app tự ghép — 0098); chia sẻ là bước sau. CHỈ THÊM: schema mới, không chạm bảng cũ ⇒ không cần cửa sổ bảo trì.
CREATE SCHEMA word;
GRANT USAGE ON SCHEMA word TO barista_app;

CREATE TABLE word.documents (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    owner_id    uuid NOT NULL DEFAULT core.me() REFERENCES core.users(id) ON DELETE CASCADE,
    title       text NOT NULL CHECK (btrim(title) <> '' AND length(title) <= 200),
    origin      text NOT NULL DEFAULT '' CHECK (length(origin) <= 200),      -- từ đâu tới: tên file tải lên / nguồn gói khác đưa sang
    version     int NOT NULL DEFAULT 1 CHECK (version >= 1),                 -- phiên bản hiện hành
    archived_at timestamptz,                                                 -- đã cất (không xoá: phiên bản và tệp còn nguyên)
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_word_documents_owner ON word.documents (tenant_id, owner_id, updated_at DESC);
ALTER TABLE word.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE word.documents FORCE ROW LEVEL SECURITY;
CREATE POLICY p_own ON word.documents USING (tenant_id = (SELECT core.tenant()) AND owner_id = (SELECT core.me()))
    WITH CHECK (tenant_id = (SELECT core.tenant()) AND owner_id = (SELECT core.me()));
GRANT SELECT, INSERT, UPDATE ON word.documents TO barista_app;

CREATE TABLE word.versions (
    tenant_id   uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES word.documents(id) ON DELETE CASCADE,
    version     int NOT NULL CHECK (version >= 1),
    file_id     uuid NOT NULL UNIQUE REFERENCES core.files(id),
    size_bytes  bigint NOT NULL CHECK (size_bytes > 0),
    note        text NOT NULL DEFAULT '' CHECK (length(note) <= 300),        -- việc đã làm ("Tải lên", "Sửa 3 đoạn", "Khôi phục bản 2")
    created_by  uuid NOT NULL DEFAULT core.me() REFERENCES core.users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, version)
);
CALL core.secure_child('word.versions', 'word.documents', 'document_id');
GRANT SELECT, INSERT ON word.versions TO barista_app;
