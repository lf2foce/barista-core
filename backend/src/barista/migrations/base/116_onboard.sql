-- module: onboard
-- 116 (ADR 0263, 28/11/2026) — ĐƯA CÔNG TY VÀO DÙNG: khung chuyển dữ liệu từ hệ cũ (sổ mã gốc + dấu vân tay, lượt chuyển, ngoại lệ dòng), đối chiếu có
-- ngưỡng + ký, danh sách việc theo 7 chặng, ngày chuyển. Chỉ THÊM (schema mới) ⇒ bản cũ chạy song song không vướng, không cần bảo trì.
--  * runs: một LƯỢT chuyển (thử ``mock`` / cuối ``final``) của một nguồn — ai chạy, mốc dữ liệu nguồn, phiên bản ánh xạ, số đếm, trạng thái.
--  * source_ids: sổ MÃ GỐC (nguồn × thực thể × mã ở hệ cũ → mã Barista) + dấu vân tay (băm bản ghi trung tính) — chạy lại cùng nguồn không tạo trùng; dấu
--    đổi ⇒ cập nhật (nếu thực thể khai) hoặc thành ngoại lệ "đổi sau khi nạp". Dùng được trên bản thật (thay sổ ``legacy_map`` chỉ-DB-thử của 0106 §6).
--    ``target_id`` trống = lệnh của thực thể chỉ GHI NHẬN trên bản ghi có sẵn (ký, ghi nhận thanh toán…), không sinh mã riêng — dòng vẫn chứng "đã nạp".
--  * pending: lệnh của một bản ghi đang nằm ở PHIẾU DUYỆT (mã phiếu) — lượt sau hỏi trạng thái phiếu thay vì chạy lại: phiếu đã thực hiện ⇒ ghi sổ mã gốc,
--    bị từ chối ⇒ ngoại lệ. ``attempt`` = số lần đã gửi lại (phiếu trước không chạy được) — nằm trong khoá chống lặp của lần gửi.
--  * exceptions: dòng nguồn KHÔNG vào (dữ liệu sai / thiếu, tham chiếu thiếu, lệnh từ chối…) — ghi lại, không tự điền.
--  * recon: đối chiếu từng chỉ số (theo tháng nếu khai) giữa nguồn và Barista sau mỗi lượt — khớp / lệch theo ngưỡng.
--  * signoffs: người ký biên bản đối chiếu của một lượt (qua chuỗi duyệt — người duyệt ghi tên).
--  * checklist: trạng thái từng việc trong danh sách (việc mặc định khai ở mã — lõi + gói; bảng chỉ giữ phần công ty đã xác nhận).
--  * live: ngày chuyển — một dòng / công ty (ngày cắt, lúc bấm, ai, người duyệt, kết quả từng bước chuyển). Bảng của module (module không ghi core.tenants).
SET LOCAL lock_timeout = '10s';

CREATE SCHEMA onboard;
GRANT USAGE ON SCHEMA onboard TO barista_app;

CREATE TABLE onboard.runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    source text NOT NULL CHECK (source ~ '^[a-z][a-z0-9_]{0,39}$'),
    kind text NOT NULL CHECK (kind IN ('mock', 'final')),
    source_cut_at timestamptz,
    mapping_version text NOT NULL DEFAULT '' CHECK (length(mapping_version) <= 80),
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
    error text NOT NULL DEFAULT '' CHECK (length(error) <= 2000),
    counts jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(counts) = 'object'),
    by_user uuid REFERENCES core.users(id),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE (tenant_id, id)
);
CREATE INDEX idx_onboard_runs ON onboard.runs (tenant_id, started_at DESC);
CALL core.secure_tenant('onboard.runs');
GRANT SELECT, INSERT, UPDATE ON onboard.runs TO barista_app;

CREATE TABLE onboard.source_ids (
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    source text NOT NULL CHECK (source ~ '^[a-z][a-z0-9_]{0,39}$'),
    entity text NOT NULL CHECK (entity ~ '^[a-z][a-z0-9_]{0,39}$'),
    source_id text NOT NULL CHECK (btrim(source_id) <> '' AND length(source_id) <= 200),
    target_id uuid,                                          -- trống = thực thể không có mã đích (lệnh chỉ ghi nhận)
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    version integer NOT NULL DEFAULT 0 CHECK (version >= 0),   -- số lần đã cập nhật từ nguồn (0 = mới tạo); khoá chống lặp của lần cập nhật kế tiếp mang số này + 1
    run_id uuid NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz,
    PRIMARY KEY (tenant_id, source, entity, source_id),
    FOREIGN KEY (tenant_id, run_id) REFERENCES onboard.runs (tenant_id, id)
);
CALL core.secure_tenant('onboard.source_ids');
GRANT SELECT, INSERT, UPDATE ON onboard.source_ids TO barista_app;

CREATE TABLE onboard.pending (
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    source text NOT NULL CHECK (source ~ '^[a-z][a-z0-9_]{0,39}$'),
    entity text NOT NULL CHECK (entity ~ '^[a-z][a-z0-9_]{0,39}$'),
    source_id text NOT NULL CHECK (btrim(source_id) <> '' AND length(source_id) <= 200),
    approval_id uuid NOT NULL,                               -- phiếu duyệt của lõi (không khoá ngoại chéo module — trạng thái hỏi qua truy vấn của lõi)
    fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
    version integer NOT NULL DEFAULT 0 CHECK (version >= 0),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    state text NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'done')),   -- done = đã ghi sổ mã gốc
    run_id uuid NOT NULL,
    at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source, entity, source_id),
    FOREIGN KEY (tenant_id, run_id) REFERENCES onboard.runs (tenant_id, id)
);
CALL core.secure_tenant('onboard.pending');
GRANT SELECT, INSERT, UPDATE ON onboard.pending TO barista_app;

CREATE TABLE onboard.exceptions (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    run_id uuid NOT NULL,
    entity text NOT NULL CHECK (entity ~ '^[a-z][a-z0-9_]{0,39}$'),
    source_id text NOT NULL DEFAULT '' CHECK (length(source_id) <= 200),
    reason text NOT NULL CHECK (reason IN ('bad_source', 'missing_ref', 'duplicate', 'rejected', 'needs_approval', 'changed_after_load', 'missing_at_source', 'retry_conflict')),
    detail text NOT NULL DEFAULT '' CHECK (length(detail) <= 2000),
    at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, run_id) REFERENCES onboard.runs (tenant_id, id)
);
CREATE INDEX idx_onboard_exceptions ON onboard.exceptions (tenant_id, run_id, entity, id);
CALL core.secure_tenant('onboard.exceptions');
GRANT SELECT, INSERT ON onboard.exceptions TO barista_app;
GRANT USAGE ON SEQUENCE onboard.exceptions_id_seq TO barista_app;

CREATE TABLE onboard.recon (
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    run_id uuid NOT NULL,
    metric text NOT NULL CHECK (metric ~ '^[a-z][a-z0-9_.]{0,79}$'),
    label text NOT NULL DEFAULT '' CHECK (length(label) <= 160),
    period text NOT NULL DEFAULT '' CHECK (period = '' OR period ~ '^[0-9]{4}-[0-9]{2}$'),
    source_value numeric NOT NULL,
    target_value numeric NOT NULL,
    tolerance_pct numeric NOT NULL DEFAULT 0 CHECK (tolerance_pct >= 0 AND tolerance_pct <= 100),
    status text NOT NULL CHECK (status IN ('khop', 'lech', 'loi')),   -- loi = chỉ số không tính được ở lượt này (lý do ở note) — chưa đối chiếu, không coi là khớp
    note text NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    PRIMARY KEY (tenant_id, run_id, metric, period),
    FOREIGN KEY (tenant_id, run_id) REFERENCES onboard.runs (tenant_id, id)
);
CALL core.secure_tenant('onboard.recon');
GRANT SELECT, INSERT ON onboard.recon TO barista_app;

CREATE TABLE onboard.signoffs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    run_id uuid NOT NULL,
    signed_by uuid NOT NULL REFERENCES core.users(id),
    approved_by uuid[] NOT NULL DEFAULT '{}',                -- người duyệt từng bước của chuỗi (trống = không qua phiếu: chủ tài khoản duy nhất / công ty bỏ luật)
    note text NOT NULL DEFAULT '' CHECK (length(note) <= 1000),
    mismatches integer NOT NULL DEFAULT 0,
    exceptions integer NOT NULL DEFAULT 0,                     -- số dòng ngoại lệ người ký chấp nhận (có lệch / ngoại lệ ⇒ bắt ghi lý do)
    signed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, run_id),
    FOREIGN KEY (tenant_id, run_id) REFERENCES onboard.runs (tenant_id, id)
);
CALL core.secure_tenant('onboard.signoffs');
GRANT SELECT, INSERT ON onboard.signoffs TO barista_app;

CREATE TABLE onboard.checklist (
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    key text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'),
    status text NOT NULL CHECK (status IN ('todo', 'done', 'skipped')),
    evidence text NOT NULL DEFAULT '' CHECK (length(evidence) <= 500),
    run_id uuid,
    confirmed_by uuid REFERENCES core.users(id),
    confirmed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key),
    FOREIGN KEY (tenant_id, run_id) REFERENCES onboard.runs (tenant_id, id)
);
CALL core.secure_tenant('onboard.checklist');
GRANT SELECT, INSERT, UPDATE ON onboard.checklist TO barista_app;

CREATE TABLE onboard.live (
    tenant_id uuid PRIMARY KEY DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    cut_date date NOT NULL,
    live_at timestamptz NOT NULL DEFAULT now(),
    by_user uuid NOT NULL REFERENCES core.users(id),
    approved_by uuid[] NOT NULL DEFAULT '{}',                -- người duyệt từng bước của chuỗi (trống = không qua phiếu: chủ tài khoản duy nhất / công ty bỏ luật)
    run_id uuid,
    steps jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(steps) = 'array'),
    note text NOT NULL DEFAULT '' CHECK (length(note) <= 1000),
    FOREIGN KEY (tenant_id, run_id) REFERENCES onboard.runs (tenant_id, id)
);
CALL core.secure_tenant('onboard.live');
GRANT SELECT, INSERT ON onboard.live TO barista_app;
