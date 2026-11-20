-- module: mkt — 0209: phần QUYẾT + LÀM của vòng quảng cáo dời từ gói kết nối Meta (0208) vào lõi, KHÔNG phụ thuộc kênh: chính sách công ty (ngưỡng theo dõi
-- / duyệt, tỷ giá, công tắc gửi chuyển đổi về kênh — MẶC ĐỊNH TẮT), phát hiện của tác nhân theo dõi (số chụp lên thẻ), SỔ THAY ĐỔI (ai đề xuất / bấm / duyệt,
-- trước / sau, phản hồi kênh, "đã làm tay"), hàng CHUYỂN ĐỔI gửi về kênh (lead ký ⇒ kênh tối ưu theo người mua). Đối tượng trên kênh nêu bằng
-- (channel, level, ref) — ``channel`` là khoá gói kênh cắm điểm mkt.ad_*; '' = chưa kênh nào nối (chỉ đường làm tay). Không cột SĐT / email: dấu băm
-- lấy từ crm lúc gửi, không lưu. Bảng MỚI ⇒ bản cũ chạy song song không đụng ⇒ không dừng dịch vụ.
SET LOCAL lock_timeout = '10s';

CREATE TABLE mkt.ad_policy (
    tenant_id     uuid PRIMARY KEY DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    settings      jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
    last_watch_on date,
    updated_by    uuid REFERENCES core.users(id),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
-- người có quyền điều khiển ĐỌC được chính sách (ngưỡng duyệt áp lúc họ bấm); sửa chỉ qua mkt.set_ad_policy (quyền mkt.ad_policy)
CALL core.secure_table('mkt.ad_policy', 'mkt.ad_control');
GRANT SELECT, INSERT ON mkt.ad_policy TO barista_app;
GRANT UPDATE (settings, last_watch_on, updated_by, updated_at) ON mkt.ad_policy TO barista_app;

CREATE TABLE mkt.ad_findings (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    day         date NOT NULL,
    scope       text NOT NULL CHECK (scope IN ('campaign', 'plan', 'offering', 'ad')),
    scope_key   text NOT NULL CHECK (btrim(scope_key) <> ''),
    rule        text NOT NULL CHECK (rule IN ('no_result', 'high_cpql', 'no_platform_lead')),
    title       text NOT NULL DEFAULT '',
    window_from date NOT NULL,
    window_to   date NOT NULL,
    figures     jsonb NOT NULL CHECK (jsonb_typeof(figures) = 'object'),
    channel     text NOT NULL DEFAULT '' CHECK (channel ~ '^[a-z0-9_]{0,40}$'),
    level       text CHECK (level IN ('campaign', 'adset', 'ad')),
    ref         text NOT NULL DEFAULT '' CHECK (ref ~ '^[A-Za-z0-9_-]{0,40}$'),
    proposal    jsonb CHECK (proposal IS NULL OR jsonb_typeof(proposal) = 'object'),
    state       text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'done', 'dismissed', 'expired')),
    decided_by  uuid REFERENCES core.users(id),
    decided_at  timestamptz,
    note        text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, day, scope, scope_key, rule)
);
CREATE INDEX idx_ad_findings_open ON mkt.ad_findings (tenant_id, scope, scope_key, rule) WHERE state = 'open';
CALL core.secure_table('mkt.ad_findings', 'mkt.ad_control');
GRANT SELECT, INSERT ON mkt.ad_findings TO barista_app;
GRANT UPDATE (state, decided_by, decided_at, note) ON mkt.ad_findings TO barista_app;

-- SỔ THAY ĐỔI (chỉ thêm; dòng chỉ đổi trạng thái gửi / xác nhận): mỗi lần gọi kênh, mỗi lần người khai đã làm tay.
CREATE TABLE mkt.ad_changes (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    channel      text NOT NULL DEFAULT '' CHECK (channel ~ '^[a-z0-9_]{0,40}$'),
    level        text NOT NULL CHECK (level IN ('campaign', 'adset', 'ad')),
    ref          text NOT NULL CHECK (ref ~ '^[A-Za-z0-9_-]{1,40}$'),
    name         text NOT NULL DEFAULT '',
    currency     text NOT NULL DEFAULT '' CHECK (currency ~ '^([A-Z]{3})?$'),
    action       text NOT NULL CHECK (action IN ('pause', 'resume', 'set_budget')),
    mode         text NOT NULL CHECK (mode IN ('api', 'manual')),
    state        text NOT NULL CHECK (state IN ('queued', 'sent', 'failed', 'manual')),
    before       jsonb NOT NULL CHECK (jsonb_typeof(before) = 'object'),
    after        jsonb NOT NULL CHECK (jsonb_typeof(after) = 'object'),
    origin       text NOT NULL DEFAULT 'person' CHECK (origin IN ('person', 'watcher')),
    finding_id   uuid REFERENCES mkt.ad_findings(id),
    requested_by uuid REFERENCES core.users(id),   -- người bấm (thẻ của trợ lý: người xác nhận; phiếu duyệt: người xin)
    approved_by  uuid[] NOT NULL DEFAULT '{}',      -- người duyệt của chuỗi "mkt_ad_change" (khi vượt ngưỡng)
    note         text NOT NULL DEFAULT '',
    response     jsonb,
    error        text NOT NULL DEFAULT '',
    attempts     integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at   timestamptz NOT NULL DEFAULT now(),
    executed_at  timestamptz,
    confirmed_at timestamptz,
    checked_at   timestamptz,
    CHECK ((state IN ('sent', 'manual')) = (executed_at IS NOT NULL)),
    CHECK ((mode = 'manual') = (state = 'manual')),
    CHECK (mode = 'manual' OR channel <> '')
);
CREATE INDEX idx_ad_changes_object ON mkt.ad_changes (tenant_id, channel, level, ref, created_at DESC);
CREATE INDEX idx_ad_changes_queued ON mkt.ad_changes (tenant_id, created_at) WHERE state = 'queued';
CREATE INDEX idx_ad_changes_unconfirmed ON mkt.ad_changes (tenant_id, created_at) WHERE state IN ('sent', 'manual') AND confirmed_at IS NULL;
CALL core.secure_table('mkt.ad_changes', 'mkt.ad_control');
GRANT SELECT, INSERT ON mkt.ad_changes TO barista_app;
GRANT UPDATE (state, response, error, attempts, executed_at, confirmed_at, checked_at) ON mkt.ad_changes TO barista_app;

-- CHUYỂN ĐỔI gửi về kênh: một kết quả bán của một lead (crm.lead_outcome_recorded) ⇒ một dòng. ``event_id`` cố định theo (lead, mã kết quả) ⇒ kênh khử
-- trùng khi gửi lại. Không SĐT / email / dấu băm: lấy từ crm lúc gửi.
CREATE TABLE mkt.ad_conversions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    channel       text NOT NULL CHECK (channel ~ '^[a-z0-9_]{1,40}$'),
    lead_id       uuid NOT NULL,                    -- crm.leads (không khoá ngoại chéo module)
    outcome_ref   text NOT NULL DEFAULT '' CHECK (length(outcome_ref) <= 80),
    event_id      text NOT NULL CHECK (event_id ~ '^[a-z0-9-]{8,80}$'),
    campaign_code text NOT NULL DEFAULT '',
    ad_code       text NOT NULL DEFAULT '',
    value_vnd     bigint NOT NULL DEFAULT 0 CHECK (value_vnd >= 0),
    occurred_at   timestamptz NOT NULL,
    state         text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'sent', 'failed', 'cancelled')),
    attempts      integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_at       timestamptz NOT NULL DEFAULT now(),
    response      jsonb,
    error         text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now(),
    sent_at       timestamptz,
    CHECK ((state = 'sent') = (sent_at IS NOT NULL)),
    UNIQUE (tenant_id, channel, event_id)
);
CREATE INDEX idx_ad_conversions_due ON mkt.ad_conversions (tenant_id, next_at) WHERE state = 'queued';
CREATE INDEX idx_ad_conversions_lead ON mkt.ad_conversions (tenant_id, lead_id);
CALL core.secure_table('mkt.ad_conversions', 'mkt.ad_control');
GRANT SELECT, INSERT ON mkt.ad_conversions TO barista_app;
GRANT UPDATE (state, attempts, next_at, response, error, sent_at) ON mkt.ad_conversions TO barista_app;
