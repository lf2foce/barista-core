-- module: connectors
-- 100 (ADR 0244, 19/11/2026) — KHUNG KẾT NỐI CHUNG: mọi nối ra hệ ngoài (tổng đài, quảng cáo, kênh bán, vận chuyển, CMS…) dùng chung bốn bảng này;
-- gói kết nối chỉ viết bộ nối + lệnh nhận. Chỉ THÊM (schema mới) ⇒ bản cũ chạy song song không vướng, không cần bảo trì.
--  * connections: một kết nối của công ty tới một hệ ngoài (bộ nối ``kind`` do gói ``owner`` khai). ``cursor`` là chuỗi của bộ nối (mốc thời gian, trang…),
--    lõi chỉ giữ hộ. ``mode`` = live | record_only (0207 — dữ liệu vào đủ, không chia / không báo) là thuộc tính của KẾT NỐI.
--  * secrets: khoá của kết nối, MÃ HOÁ AES-GCM bằng khoá bản cài (``BARISTA_CONNECTOR_KEY``) — DB / bản sao lưu lộ cũng không lộ khoá hệ ngoài.
--  * events: chống lặp webhook theo mã sự kiện của hệ ngoài (khoá chính).
--  * pending: MỤC giao lỗi (lệnh nhận của gói từ chối một mục) — lõi giao TỪNG MỤC trong điểm lưu riêng, mục lỗi nằm đây thử lại theo lịch, con trỏ
--    vẫn tiến (một mục hỏng không chặn cả lô, không kéo lại cả lô). Hết lượt thử ⇒ ``state = 'failed'``, hiện ở màn + Sức khoẻ.
--  * runs: nhật ký từng lượt — số mục + mã lỗi + câu người đọc được; không thân phản hồi (dữ liệu khách), không khoá.
SET LOCAL lock_timeout = '10s';

CREATE SCHEMA connectors;
GRANT USAGE ON SCHEMA connectors TO barista_app;

CREATE TABLE connectors.connections (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    owner text NOT NULL CHECK (owner ~ '^[a-z][a-z0-9_]*$'),
    kind text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*$'),
    label text NOT NULL CHECK (btrim(label) <> '' AND length(label) <= 120),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
    mode text NOT NULL DEFAULT 'live' CHECK (mode IN ('live', 'record_only')),
    settings jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
    cursor text NOT NULL DEFAULT '' CHECK (length(cursor) <= 2000),
    last_sync_at timestamptz,
    last_ok_at timestamptz,
    last_error text NOT NULL DEFAULT '' CHECK (length(last_error) <= 500),
    error_kind text NOT NULL DEFAULT '',
    fails integer NOT NULL DEFAULT 0,
    next_try_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid REFERENCES core.users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id)
);
CREATE INDEX idx_connections_due ON connectors.connections (next_try_at) WHERE status = 'active';
CALL core.secure_table('connectors.connections', 'connectors');
GRANT SELECT, INSERT, UPDATE, DELETE ON connectors.connections TO barista_app;

CREATE TABLE connectors.secrets (
    tenant_id uuid NOT NULL DEFAULT core.tenant(),
    connection_id uuid NOT NULL,
    name text NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]*$'),
    sealed bytea NOT NULL,
    hint text NOT NULL DEFAULT '' CHECK (length(hint) <= 12),
    updated_by uuid REFERENCES core.users(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, connection_id, name),
    FOREIGN KEY (tenant_id, connection_id) REFERENCES connectors.connections (tenant_id, id) ON DELETE CASCADE
);
CALL core.secure_child('connectors.secrets', 'connectors.connections', 'connection_id');
GRANT SELECT, INSERT, UPDATE, DELETE ON connectors.secrets TO barista_app;

CREATE TABLE connectors.events (
    tenant_id uuid NOT NULL DEFAULT core.tenant(),
    connection_id uuid NOT NULL,
    event_id text NOT NULL CHECK (btrim(event_id) <> '' AND length(event_id) <= 200),
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, connection_id, event_id),
    FOREIGN KEY (tenant_id, connection_id) REFERENCES connectors.connections (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_connector_events_at ON connectors.events (tenant_id, received_at);
CALL core.secure_child('connectors.events', 'connectors.connections', 'connection_id');
GRANT SELECT, INSERT, DELETE ON connectors.events TO barista_app;

CREATE TABLE connectors.runs (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL DEFAULT core.tenant(),
    connection_id uuid NOT NULL,
    via text NOT NULL CHECK (via IN ('pull', 'hook', 'check')),
    at timestamptz NOT NULL DEFAULT now(),
    ok boolean NOT NULL,
    items integer NOT NULL DEFAULT 0,
    error_kind text NOT NULL DEFAULT '',
    error text NOT NULL DEFAULT '' CHECK (length(error) <= 500),
    FOREIGN KEY (tenant_id, connection_id) REFERENCES connectors.connections (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_connector_runs ON connectors.runs (tenant_id, connection_id, at DESC);
CALL core.secure_child('connectors.runs', 'connectors.connections', 'connection_id');
GRANT SELECT, INSERT, DELETE ON connectors.runs TO barista_app;
GRANT USAGE ON SEQUENCE connectors.runs_id_seq TO barista_app;

CREATE TABLE connectors.pending (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL DEFAULT core.tenant(),
    connection_id uuid NOT NULL,
    item jsonb NOT NULL,
    via text NOT NULL CHECK (via IN ('pull', 'hook')),
    state text NOT NULL DEFAULT 'retry' CHECK (state IN ('retry', 'failed')),
    attempts integer NOT NULL DEFAULT 1,
    error text NOT NULL DEFAULT '' CHECK (length(error) <= 500),
    next_try_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, connection_id) REFERENCES connectors.connections (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_connector_pending ON connectors.pending (tenant_id, connection_id, next_try_at) WHERE state = 'retry';
CALL core.secure_child('connectors.pending', 'connectors.connections', 'connection_id');
GRANT SELECT, INSERT, UPDATE, DELETE ON connectors.pending TO barista_app;
GRANT USAGE ON SEQUENCE connectors.pending_id_seq TO barista_app;
