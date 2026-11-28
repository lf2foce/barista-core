-- module: outbound, core — lý do: ADR 0265 — hộp thư đi (schema mới outbound) + quyền mặc định mới của gói nền cho công ty có trước (chỉ thêm dòng core.grants + nhật ký cài, theo dõi bằng pack_installs — như schedule/073)
-- 118 (ADR 0265 mục 3) — HỘP THƯ ĐI: một hàng chờ chung cho mọi tin công ty gửi RA NGOÀI (email trước; Zalo OA / SMS là bộ nối sau — thêm kênh
-- không đổi schema: ``channel`` là chữ, không enum). Chỉ THÊM (schema mới) ⇒ bản cũ chạy song song không vướng, không cần bảo trì.
--  * messages: một tin. ``draft`` (trợ lý / luật / người soạn) → người BẤM gửi → ``queued`` → vòng gửi nền → ``sent`` | thử lại lùi dần (vẫn
--    ``queued``, ``attempts`` tăng, ``last_error``) | quá số lần ⇒ ``dead`` | ``cancelled``. ``dedupe_key`` chống soạn / gửi lặp (một khoá một tin
--    trong công ty). ``purpose`` = giao dịch | chăm sóc | tiếp thị — hai mục đích sau phải có sự đồng ý của người nhận (họ ``consent``).
--    ``party_id`` trỏ người nhận trong danh mục cá nhân & tổ chức (không khoá ngoại chéo module); ``to_address`` là địa chỉ CHỐT lúc soạn.
--    ``lease_until``: lượt gửi đang giữ tin (hai tiến trình không gửi trùng; tiến trình chết ⇒ hết thuê, tin quay lại hàng chờ).
--  * attachments: tệp đính kèm — MỘT CHỖ DÙNG của tệp trong sổ ``core.files`` (0122; không cascade từ sổ tệp).
--  * log: nhật ký từng bước của tin (soạn, bấm gửi, từng lượt gửi + lỗi, huỷ, gửi lại) — ai, lúc nào.
--  * sender: tên người gửi + địa chỉ nhận trả lời của CÔNG TY (trống ⇒ hộp chung của bản cài).
SET LOCAL lock_timeout = '10s';

CREATE SCHEMA outbound;
GRANT USAGE ON SCHEMA outbound TO barista_app;

CREATE TABLE outbound.messages (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    channel       text NOT NULL DEFAULT 'email' CHECK (channel ~ '^[a-z][a-z0-9_]*$'),
    purpose       text NOT NULL DEFAULT 'transactional' CHECK (purpose IN ('transactional', 'care', 'marketing')),
    party_id      uuid,
    to_name       text NOT NULL DEFAULT '' CHECK (length(to_name) <= 200),
    to_address    text NOT NULL CHECK (btrim(to_address) <> '' AND length(to_address) <= 320),
    subject       text NOT NULL CHECK (btrim(subject) <> '' AND length(subject) <= 200),
    body          text NOT NULL CHECK (btrim(body) <> '' AND length(body) <= 20000),
    template      text NOT NULL DEFAULT '' CHECK (length(template) <= 60),
    data          jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(data) = 'object'),
    source        text NOT NULL DEFAULT '' CHECK (length(source) <= 60),
    source_ref    text NOT NULL DEFAULT '' CHECK (length(source_ref) <= 100),
    dedupe_key    text CHECK (dedupe_key IS NULL OR (btrim(dedupe_key) <> '' AND length(dedupe_key) <= 200)),
    status        text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'queued', 'sent', 'dead', 'cancelled')),
    attempts      integer NOT NULL DEFAULT 0,
    last_error    text NOT NULL DEFAULT '' CHECK (length(last_error) <= 500),
    next_at       timestamptz NOT NULL DEFAULT now(),
    lease_until   timestamptz,
    created_by    uuid REFERENCES core.users(id),
    drafted_by_ai boolean NOT NULL DEFAULT false,
    released_by   uuid REFERENCES core.users(id),
    released_at   timestamptz,
    sent_at       timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, dedupe_key),
    CHECK (status IN ('draft', 'cancelled') OR released_by IS NOT NULL)   -- không tin nào rời nháp mà không có NGƯỜI bấm gửi
);
CREATE INDEX idx_outbound_due ON outbound.messages (next_at) WHERE status = 'queued';
CREATE INDEX idx_outbound_recent ON outbound.messages (tenant_id, created_at DESC);
CALL core.secure_table('outbound.messages', 'outbound', 'created_by');
GRANT SELECT, INSERT, UPDATE ON outbound.messages TO barista_app;

CREATE TABLE outbound.attachments (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL DEFAULT core.tenant(),
    message_id  uuid NOT NULL,
    file_id     uuid NOT NULL REFERENCES core.files(id),   -- KHÔNG cascade: khoá ngoại là cách kernel biết tệp còn chỗ dùng (0122)
    filename    text NOT NULL CHECK (btrim(filename) <> '' AND length(filename) <= 300),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id, file_id),
    FOREIGN KEY (tenant_id, message_id) REFERENCES outbound.messages (tenant_id, id) ON DELETE CASCADE
);
CALL core.secure_child('outbound.attachments', 'outbound.messages', 'message_id');
GRANT SELECT, INSERT, DELETE ON outbound.attachments TO barista_app;

CREATE TABLE outbound.log (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL DEFAULT core.tenant(),
    message_id  uuid NOT NULL,
    at          timestamptz NOT NULL DEFAULT now(),
    kind        text NOT NULL CHECK (kind IN ('drafted', 'released', 'attempt', 'sent', 'dead', 'cancelled', 'retried')),
    note        text NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    actor       uuid REFERENCES core.users(id),
    FOREIGN KEY (tenant_id, message_id) REFERENCES outbound.messages (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX idx_outbound_log ON outbound.log (tenant_id, message_id, id);
CALL core.secure_child('outbound.log', 'outbound.messages', 'message_id');
GRANT SELECT, INSERT ON outbound.log TO barista_app;
GRANT USAGE ON SEQUENCE outbound.log_id_seq TO barista_app;

CREATE TABLE outbound.sender (
    tenant_id   uuid PRIMARY KEY DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    from_name   text NOT NULL DEFAULT '' CHECK (length(from_name) <= 80 AND from_name !~ '[\r\n<>"]'),
    reply_to    text NOT NULL DEFAULT '' CHECK (length(reply_to) <= 320 AND reply_to !~ '[\r\n\s<>,;]'),
    updated_by  uuid REFERENCES core.users(id),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CALL core.secure_tenant('outbound.sender');
GRANT SELECT, INSERT, UPDATE ON outbound.sender TO barista_app;

CALL core.tenant_fks(ARRAY['outbound']);

-- Quyền mặc định mới của gói nền (``kernel/core_ops.py``) cho công ty có trước — như schedule/073: chỉ vai đang có ở công ty, chỉ khi chưa từng cài (có dòng
-- nhật ký ⇒ giữ quyết định của công ty). Công ty mới nhận qua admin.install_defaults như mọi quyền mặc định.
WITH want(role, resource, scope, actions) AS (VALUES
    ('admin',    'outbound', 'all',  ARRAY['read', 'write', 'manage']),
    ('director', 'outbound', 'all',  ARRAY['read', 'write', 'manage']),
    ('office',   'outbound', 'team', ARRAY['read', 'write']),
    ('staff',    'outbound', 'own',  ARRAY['read', 'write']))
INSERT INTO core.grants(tenant_id, role, resource, scope, actions)
SELECT r.tenant_id, r.key, w.resource, w.scope, w.actions
FROM core.roles r JOIN want w ON w.role = r.key
WHERE NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = r.tenant_id AND i.kind = 'grant' AND i.key = r.key || '|' || w.resource)
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs(tenant_id, kind, key, pack, version)
SELECT r.tenant_id, 'grant', r.key || '|' || w.resource, 'base', NULL
FROM core.roles r JOIN (VALUES ('admin', 'outbound'), ('director', 'outbound'), ('office', 'outbound'), ('staff', 'outbound')) AS w(role, resource) ON w.role = r.key
ON CONFLICT DO NOTHING;
