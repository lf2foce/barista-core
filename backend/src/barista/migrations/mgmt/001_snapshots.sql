-- module: mgmt
-- 001 (0213): BẢN CHỐT THÁNG của Báo cáo quản trị — chốt tháng thì chụp ĐẦU RA của từng trang chính thức trong menu (đúng trang app vẽ, bằng quyền
-- người chốt) để số đã họp không tự đổi khi dữ liệu trễ được nhập. Chỉ THÊM: chốt lại = lần chốt mới (version + 1), bản cũ còn nguyên.
-- Chụp cả dashboard tự tạo ĐÃ ĐƯA VÀO MENU cho cả công ty (0176); không chụp app riêng chưa chia (định nghĩa riêng, không phải số chính thức).
CREATE SCHEMA mgmt;
GRANT USAGE ON SCHEMA mgmt TO barista_app;

CREATE TABLE mgmt.snapshots (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    period     DATE NOT NULL CHECK (extract(day FROM period) = 1),          -- tháng được chốt (ngày đầu tháng)
    version    INT NOT NULL CHECK (version >= 1),                           -- lần chốt thứ mấy của tháng đó
    screen     TEXT NOT NULL CHECK (screen ~ '^[a-z0-9_-]{1,80}$'),       -- khoá màn trong menu Báo cáo quản trị
    title      TEXT NOT NULL CHECK (length(title) <= 200),
    grp        TEXT NOT NULL DEFAULT '' CHECK (length(grp) <= 120),         -- nhóm trên menu lúc chụp
    page       JSONB NOT NULL,                                              -- đầu ra truy vấn của màn lúc chụp (trang app vẽ)
    note       TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    closed_by  UUID NOT NULL REFERENCES core.users(id),
    closed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, period, version, screen)
);
CREATE INDEX idx_mgmt_snapshots ON mgmt.snapshots (tenant_id, period DESC, version DESC);
CALL core.secure_table('mgmt.snapshots', 'mgmt');                          -- số cả công ty ⇒ chỉ ai có quyền Báo cáo quản trị phạm vi cả công ty
GRANT SELECT, INSERT ON mgmt.snapshots TO barista_app;
