-- module: flow
-- 002 (0090): TỰ ĐỘNG HOÁ "Nếu… thì…" do quản trị đặt — luật là DỮ LIỆU: khi một CÒ module khai (sự kiện, ``Module.trigger``) xảy ra và điều
-- kiện trên trường khai báo đúng ⇒ báo người / đội, hoặc tạo việc. Chạy bằng QUYỀN NGƯỜI ĐẶT LUẬT (nạp lại mỗi lần — thôi việc / mất quyền ⇒
-- luật không chạy được), có trần số lần mỗi ngày, mỗi sự kiện chạy một lần, mỗi lần có nhật ký.

CREATE TABLE flow.automations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 120),
    trigger     TEXT NOT NULL CHECK (trigger ~ '^[a-z_]+\.[a-z_]+$'),
    conditions  JSONB NOT NULL DEFAULT '[]',          -- [{field, op: eq|ne|ge|le|contains, value}] — trường phải nằm trong cò khai
    action      TEXT NOT NULL CHECK (action IN ('notify', 'task')),
    team_id     UUID REFERENCES core.teams(id),       -- báo / giao cho đội (cả đội, hoặc chỉ trưởng đội)
    leads_only  BOOLEAN NOT NULL DEFAULT false,
    user_id     UUID REFERENCES core.users(id),       -- hoặc một người cụ thể
    message     TEXT NOT NULL CHECK (btrim(message) <> '' AND length(message) <= 500),   -- mẫu chữ, {trường} lấy từ sự kiện
    due_days    INT CHECK (due_days BETWEEN 0 AND 60),   -- việc: hạn sau N ngày
    daily_cap   INT NOT NULL DEFAULT 20 CHECK (daily_cap BETWEEN 1 AND 500),
    active      BOOLEAN NOT NULL DEFAULT true,
    owner_id    UUID NOT NULL REFERENCES core.users(id),   -- người đặt luật: luật chạy bằng quyền người này
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((team_id IS NULL) <> (user_id IS NULL))
);
CALL core.secure_table('flow.automations', 'flow.automations', 'owner_id', NULL);
GRANT SELECT, INSERT, UPDATE ON flow.automations TO barista_app;

-- Mỗi lần luật được kích: chạy / bỏ qua (điều kiện sai, chạm trần, người đặt luật không còn) / lỗi — chống chạy hai lần cùng một sự kiện.
CREATE TABLE flow.automation_runs (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    automation_id UUID NOT NULL REFERENCES flow.automations(id) ON DELETE CASCADE,
    event_id      BIGINT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('done', 'skipped', 'failed')),
    note          TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (automation_id, event_id)
);
CREATE INDEX idx_automation_runs_day ON flow.automation_runs (automation_id, created_at DESC);
CALL core.secure_child('flow.automation_runs', 'flow.automations', 'automation_id');
GRANT SELECT, INSERT ON flow.automation_runs TO barista_app;
GRANT USAGE ON SEQUENCE flow.automation_runs_id_seq TO barista_app;
