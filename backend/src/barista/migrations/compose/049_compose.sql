-- module: compose — lý do: thử nghiệm "Soạn với AI" (docs/ideas/compose-ai-thu-nghiem.md): bản soạn tự do (văn bản / tờ giới thiệu / slide)
-- ghép từ KHỐI; số chỉ nằm trong khối số liệu (kết quả truy vấn chạy bằng quyền người soạn). Bản soạn của RIÊNG người tạo (như app tự ghép —
-- 0098); mỗi lần đổi thêm một phiên bản (chỉ thêm). CHỈ THÊM: schema mới, không chạm bảng cũ ⇒ không cần cửa sổ bảo trì.
CREATE SCHEMA compose;
GRANT USAGE ON SCHEMA compose TO barista_app;

CREATE TABLE compose.sessions (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    owner_id   uuid NOT NULL DEFAULT core.me() REFERENCES core.users(id) ON DELETE CASCADE,
    kind       text NOT NULL CHECK (kind IN ('document', 'brochure', 'slides')),
    brief      text NOT NULL CHECK (btrim(brief) <> '' AND length(brief) <= 2000),
    title      text NOT NULL CHECK (btrim(title) <> '' AND length(title) <= 200),
    blocks     jsonb NOT NULL,                     -- bản hiện hành (danh sách khối — modules/compose.py)
    version    int NOT NULL DEFAULT 0 CHECK (version >= 0),       -- 0 = lượt soạn đầu chưa xong
    status     text NOT NULL DEFAULT 'ready' CHECK (status IN ('drafting', 'ready', 'failed')),   -- lượt model chạy NGOÀI giao dịch (sau commit)
    error      text NOT NULL DEFAULT '',                         -- lượt soạn gần nhất lỗi / hết giờ
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_compose_sessions_owner ON compose.sessions (tenant_id, owner_id, updated_at DESC);
ALTER TABLE compose.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE compose.sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY p_own ON compose.sessions USING (tenant_id = core.tenant() AND owner_id = core.me())
    WITH CHECK (tenant_id = core.tenant() AND owner_id = core.me());
GRANT SELECT, INSERT, UPDATE, DELETE ON compose.sessions TO barista_app;

CREATE TABLE compose.versions (
    tenant_id   uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    session_id  uuid NOT NULL REFERENCES compose.sessions(id) ON DELETE CASCADE,
    version     int NOT NULL,
    title       text NOT NULL,
    blocks      jsonb NOT NULL,
    via         text NOT NULL CHECK (via IN ('ai', 'human', 'agent')),
    note        text NOT NULL DEFAULT '',          -- việc đã làm ("Soạn mới", "Viết lại khối 3: …")
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, version)
);
CALL core.secure_child('compose.versions', 'compose.sessions', 'session_id');
GRANT SELECT, INSERT, DELETE ON compose.versions TO barista_app;

-- Tệp đã xuất (0122: thân ở kho tệp, đây chỉ tham chiếu)
CREATE TABLE compose.exports (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    session_id  uuid NOT NULL REFERENCES compose.sessions(id) ON DELETE CASCADE,
    version     int NOT NULL,
    format      text NOT NULL CHECK (format IN ('docx', 'pdf', 'pptx')),
    filename    text NOT NULL,
    file_id     uuid NOT NULL UNIQUE REFERENCES core.files(id),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CALL core.secure_child('compose.exports', 'compose.sessions', 'session_id');
GRANT SELECT, INSERT, DELETE ON compose.exports TO barista_app;

-- Bộ nhận diện công ty (logo, màu chính, phông, chân trang) — một dòng mỗi công ty; trống ⇒ mặc định trong code
CREATE TABLE compose.brand (
    tenant_id     uuid PRIMARY KEY DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    primary_color text NOT NULL DEFAULT '1F4E79' CHECK (primary_color ~ '^[0-9A-F]{6}$'),
    font          text NOT NULL DEFAULT 'Arial' CHECK (font IN ('Arial', 'Calibri', 'Times New Roman', 'Roboto', 'Be Vietnam Pro')),
    footer        text NOT NULL DEFAULT '' CHECK (length(footer) <= 200),
    logo_file_id  uuid REFERENCES core.files(id),
    logo_filename text NOT NULL DEFAULT '',
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CALL core.secure_tenant('compose.brand');
GRANT SELECT, INSERT, UPDATE ON compose.brand TO barista_app;
