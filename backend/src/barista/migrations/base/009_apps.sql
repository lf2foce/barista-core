-- module: apps
-- 009 (0098): APP TỰ GHÉP — người dùng nói, trợ lý ghép một màn từ truy vấn + lệnh ĐÃ CÓ; app là DỮ LIỆU (định nghĩa khối), không phải code:
-- mở app = chạy lại từng nguồn bằng quyền NGƯỜI XEM qua executor. Bản đầu: app CỦA RIÊNG người tạo (chia sẻ để sau) — chỉ chính người đó thấy.
-- Mỗi lần lưu thêm một phiên bản (chỉ thêm) ⇒ quay lại được bản trước. Chỉ THÊM.
CREATE SCHEMA apps;
GRANT USAGE ON SCHEMA apps TO barista_app;

CREATE TABLE apps.items (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    owner_id   UUID NOT NULL DEFAULT core.me() REFERENCES core.users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL CHECK (btrim(title) <> '' AND length(title) <= 80),
    icon       TEXT NOT NULL DEFAULT 'square.grid.2x2',
    tint       TEXT NOT NULL DEFAULT '',
    spec       JSONB NOT NULL,                        -- AppSpec (modules/apps.py) — đã kiểm khi lưu
    version    INT NOT NULL DEFAULT 1 CHECK (version > 0),
    via        TEXT NOT NULL DEFAULT 'human' CHECK (via IN ('human', 'agent')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_apps_owner ON apps.items (tenant_id, owner_id);
ALTER TABLE apps.items ENABLE ROW LEVEL SECURITY;
ALTER TABLE apps.items FORCE ROW LEVEL SECURITY;
CREATE POLICY p_own ON apps.items USING (tenant_id = core.tenant() AND owner_id = core.me())
    WITH CHECK (tenant_id = core.tenant() AND owner_id = core.me());
GRANT SELECT, INSERT, UPDATE, DELETE ON apps.items TO barista_app;

CREATE TABLE apps.versions (
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    app_id     UUID NOT NULL REFERENCES apps.items(id) ON DELETE CASCADE,
    version    INT NOT NULL,
    title      TEXT NOT NULL,
    spec       JSONB NOT NULL,
    via        TEXT NOT NULL CHECK (via IN ('human', 'agent')),
    trace      TEXT NOT NULL DEFAULT '',              -- lượt trò chuyện đã sinh ra phiên bản (0086)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (app_id, version)
);
CALL core.secure_child('apps.versions', 'apps.items', 'app_id');
GRANT SELECT, INSERT, DELETE ON apps.versions TO barista_app;
