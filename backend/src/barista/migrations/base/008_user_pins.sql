-- module: core
-- 008 (0098): GHIM THEO NGƯỜI — mỗi người tự ghim màn lên đầu sidebar (màn của module hoặc app tự ghép). Trước đó chỉ có ghim theo VAI
-- (Pack.pins / Role.home — vẫn là mặc định). Chỉ của chính người đó (như cuộc trò chuyện). Ghim chỉ là ĐỊA CHỈ (phân hệ + khoá màn): mở màn vẫn
-- lọc quyền như thường — ghim một màn rồi mất quyền ⇒ ghim tự ẩn. Chỉ THÊM.

CREATE TABLE core.user_pins (
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL DEFAULT core.me() REFERENCES core.users(id) ON DELETE CASCADE,
    workspace  TEXT NOT NULL CHECK (workspace ~ '^[a-z_]+$'),
    key        TEXT NOT NULL CHECK (key ~ '^[a-z0-9_-]{1,80}$'),
    position   INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id, workspace, key)
);
ALTER TABLE core.user_pins ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.user_pins FORCE ROW LEVEL SECURITY;
CREATE POLICY p_own ON core.user_pins USING (tenant_id = core.tenant() AND user_id = core.me())
    WITH CHECK (tenant_id = core.tenant() AND user_id = core.me());
GRANT SELECT, INSERT, UPDATE, DELETE ON core.user_pins TO barista_app;
