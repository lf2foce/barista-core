-- module: core — lý do: 0140 (0130 lát 5) — APP TỪ XA: một app = một client OAuth của MỘT công ty (client credentials), quản trị cài, chủ tài khoản
-- duyệt (0029). Quyền của app là DỮ LIỆU riêng (core.remote_app_grants — "vai" của app), lúc chạy giao với quyền hiện tại của người bảo lãnh
-- (không bao giờ hơn người cài). Sự kiện báo ra ngoài = webhook từ outbox, ký HMAC, chỉ mang mã.
-- Giá trị bí mật lưu BĂM (như core.sessions / core.mcp_tokens); khoá ký webhook KHÔNG lưu — dẫn xuất từ khoá của bản cài (BARISTA_APPS_SIGNING_SECRET).
-- CHỈ THÊM: bảng mới + nới CHECK ``via`` của nhật ký (NOT VALID — dòng cũ hiển nhiên thoả, không quét bảng). Bản cũ chạy song song lúc chuyển
-- không đọc / ghi các bảng này ⇒ không cần cửa sổ bảo trì (không vào deployment.json).

CREATE TABLE core.remote_apps (
    id               uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id        uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    client_id        text NOT NULL UNIQUE CHECK (client_id ~ '^bca_[A-Za-z0-9_-]{12,64}$'),
    name             text NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 80),
    -- danh tính của app trong DB: một dòng core.users KHÔNG có thành viên (không đăng nhập được, không hiện trong danh sách người, không nhận
    -- lead / phiếu duyệt) — chỉ để các cột "ai làm" (by_user, created_by…) có khoá ngoại hợp lệ
    service_user_id  uuid NOT NULL UNIQUE REFERENCES core.users(id),
    installed_by     uuid NOT NULL REFERENCES core.users(id),    -- người bảo lãnh: quyền app ≤ quyền hiện tại của người này
    approved_by      uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    webhook_url      text DEFAULT ''::text NOT NULL CHECK (webhook_url = '' OR webhook_url ~ '^https?://[^\s]+$'),
    secret_hash      text,                                        -- NULL = chưa nhận khoá
    secret_version   integer DEFAULT 0 NOT NULL CHECK (secret_version >= 0),
    secret_claim_by  uuid REFERENCES core.users(id),              -- người được nhận khoá MỘT lần (người xin cài / xoay khoá, đã được duyệt)
    secret_set_at    timestamptz,
    status           text DEFAULT 'active'::text NOT NULL CHECK (status IN ('active', 'revoked')),
    revoked_at       timestamptz,
    revoked_by       uuid REFERENCES core.users(id),
    last_token_at    timestamptz,
    created_at       timestamptz DEFAULT now() NOT NULL,
    CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))
);
CALL core.secure_tenant('core.remote_apps');

-- "Vai" của app: tài nguyên × phạm vi × hành động — chỉ xem / ghi (không duyệt, không quản lý; không tài nguyên quản trị lõi).
CREATE TABLE core.remote_app_grants (
    tenant_id  uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    app_id     uuid NOT NULL REFERENCES core.remote_apps(id) ON DELETE CASCADE,
    resource   text NOT NULL CHECK (resource <> 'core.admin'),
    scope      text NOT NULL CHECK (scope IN ('own', 'unit', 'team', 'all')),
    actions    text[] NOT NULL CHECK (actions <> '{}' AND actions <@ ARRAY['read', 'write']),
    PRIMARY KEY (app_id, resource)
);
CALL core.secure_tenant('core.remote_app_grants');

-- Sự kiện app nhận (tên CÔNG BỐ, vd lead.assigned — module khai bằng Module.webhook).
CREATE TABLE core.remote_app_events (
    tenant_id  uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    app_id     uuid NOT NULL REFERENCES core.remote_apps(id) ON DELETE CASCADE,
    event      text NOT NULL CHECK (event ~ '^[a-z]+\.[a-z_]+$'),
    PRIMARY KEY (app_id, event)
);
CALL core.secure_tenant('core.remote_app_events');

-- Token truy cập (client credentials, ngắn hạn). Gắn phiên bản khoá: xoay khoá ⇒ token cũ hết hiệu lực.
CREATE TABLE core.remote_app_tokens (
    id              uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id       uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    app_id          uuid NOT NULL REFERENCES core.remote_apps(id) ON DELETE CASCADE,
    token_hash      text NOT NULL UNIQUE,
    scopes          text[] DEFAULT '{}'::text[] NOT NULL,         -- rỗng = mọi quyền của app; có = chỉ những "tài nguyên:hành động" này
    secret_version  integer NOT NULL,
    expires_at      timestamptz NOT NULL,
    revoked_at      timestamptz,
    last_used_at    timestamptz,
    created_at      timestamptz DEFAULT now() NOT NULL
);
CREATE INDEX idx_remote_app_tokens_app ON core.remote_app_tokens (app_id) WHERE revoked_at IS NULL;
CALL core.secure_tenant('core.remote_app_tokens');

-- Hộp thư đi webhook (outbox): ghi CÙNG giao dịch với sự kiện, worker gửi sau. Thân chỉ mang loại + mã + lúc xảy ra.
CREATE TABLE core.webhook_deliveries (
    id           uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id    uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    app_id       uuid NOT NULL REFERENCES core.remote_apps(id) ON DELETE CASCADE,
    event        text NOT NULL,
    event_id     bigint,                                           -- core.events.id gây ra (truy vết)
    data         jsonb DEFAULT '{}'::jsonb NOT NULL,               -- CHỈ mã (khoá *_id) — kernel/webhooks.py lọc lúc ghi
    occurred_at  timestamptz NOT NULL,
    status       text DEFAULT 'pending'::text NOT NULL CHECK (status IN ('pending', 'done', 'dead', 'cancelled')),
    attempts     integer DEFAULT 0 NOT NULL,
    next_at      timestamptz DEFAULT now() NOT NULL,
    last_status  integer,
    last_error   text DEFAULT ''::text NOT NULL,
    created_at   timestamptz DEFAULT now() NOT NULL,
    done_at      timestamptz
);
CREATE INDEX idx_webhook_deliveries_due ON core.webhook_deliveries (next_at) WHERE status = 'pending';
CREATE INDEX idx_webhook_deliveries_app ON core.webhook_deliveries (app_id, created_at DESC);
CALL core.secure_tenant('core.webhook_deliveries');

GRANT SELECT, INSERT ON core.remote_apps, core.remote_app_tokens, core.webhook_deliveries TO barista_app;
GRANT UPDATE (webhook_url, installed_by, approved_by, secret_hash, secret_version, secret_claim_by, secret_set_at, status, revoked_at, revoked_by)
    ON core.remote_apps TO barista_app;
GRANT SELECT, INSERT, DELETE ON core.remote_app_grants, core.remote_app_events TO barista_app;
GRANT UPDATE (revoked_at) ON core.remote_app_tokens TO barista_app;
GRANT UPDATE (status, last_error, done_at) ON core.webhook_deliveries TO barista_app;

-- Nhật ký thao tác: thêm người gọi "app" (người gọi thứ ba bên cạnh người và trợ lý).
ALTER TABLE core.audit_log DROP CONSTRAINT audit_log_via_check;
ALTER TABLE core.audit_log ADD CONSTRAINT audit_log_via_check CHECK (via IN ('human', 'agent', 'link', 'app')) NOT VALID;
