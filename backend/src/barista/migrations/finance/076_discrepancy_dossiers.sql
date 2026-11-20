-- module: acct
-- 0223 — HỒ SƠ ĐIỀU TRA KHOẢN LỆCH: tiền về (dòng sao kê) không khớp công nợ ⇒ code dựng hồ sơ (ứng viên có điểm + lời giải thích tính bằng code theo mẫu đã biết,
-- kèm bằng chứng), đề xuất việc, giao một người, theo tới khi xong. Chỉ THÊM ba bảng mới rỗng ⇒ bản cũ chạy song song không đọc chúng ⇒ không bảo trì.
-- (1) ``acct.discrepancy_rules`` = CHÍNH SÁCH là dữ liệu công ty: ai phụ trách khoản lệch theo pháp nhân / nguồn khoản phải thu, và các ngưỡng (số ngày chưa xử lý,
--     ngưỡng chênh nhỏ, hạn xử lý). Không dòng nào ⇒ ngưỡng mặc định trong code, người phụ trách = người phụ trách khoản phải thu → kế toán.
-- (2) ``acct.discrepancy_dossiers`` = một hồ sơ MỖI dòng sao kê (UNIQUE) — sự thật: ứng viên + lời giải thích chụp lúc dựng / làm mới; tiền KHÔNG nằm ở đây
--     (mọi tác động tiền đi qua lệnh Tài chính có sẵn: apply_line, credit_receivable… — không ghi sổ cái từ hồ sơ).
-- (3) ``acct.discrepancy_events`` = nhật ký chỉ thêm của hồ sơ (dựng, làm mới, quyết định, soạn tin, đóng).
SET LOCAL lock_timeout = '10s';

CREATE TABLE acct.discrepancy_rules (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    entity_id        UUID REFERENCES core.legal_entities(id),                   -- NULL = mọi pháp nhân
    source           TEXT NOT NULL DEFAULT '' CHECK (length(source) <= 100),   -- tiền tố nguồn khoản phải thu (vd <module>.fee); '' = mọi nguồn
    owner_id         UUID REFERENCES core.users(id),                            -- người phụ trách khoản lệch khớp luật này; NULL = luật chỉ đặt ngưỡng
    stale_days       INT CHECK (stale_days BETWEEN 0 AND 90),                    -- dòng sao kê chưa xử lý quá ngần này ngày ⇒ dựng hồ sơ (chỉ đọc ở luật source = '')
    fee_tolerance_vnd BIGINT CHECK (fee_tolerance_vnd BETWEEN 0 AND 10000000),   -- chênh ≤ mức này ⇒ nghi phí chuyển khoản / khấu trừ nhỏ của bên trả
    resolve_days     INT CHECK (resolve_days BETWEEN 1 AND 90),                  -- hạn xử lý của việc theo dõi (ngày kể từ lúc dựng hồ sơ)
    active           BOOLEAN NOT NULL DEFAULT true,
    note             TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    updated_by       UUID REFERENCES core.users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),   -- BẬT tính từ đây: vòng quét chỉ xét dòng sao kê nhận từ ngày công ty đặt luật đầu tiên
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_discrepancy_rules_scope ON acct.discrepancy_rules
    (tenant_id, coalesce(entity_id, '00000000-0000-0000-0000-000000000000'::uuid), source);
CALL core.secure_table('acct.discrepancy_rules', 'acct');
GRANT SELECT, INSERT, UPDATE ON acct.discrepancy_rules TO barista_app;

CREATE TABLE acct.discrepancy_dossiers (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    code             TEXT NOT NULL CHECK (code ~ '^KL-[0-9A-Z]{6,12}$'),
    line_id          UUID NOT NULL,                                              -- dòng sao kê gây lệch; một dòng một hồ sơ (kể cả đã đóng — không mở lại)
    entity_id        UUID NOT NULL REFERENCES core.legal_entities(id),
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'written_off', 'dismissed')),
    trigger          TEXT NOT NULL CHECK (trigger IN ('stale_line', 'overdue_receivable', 'manual')),
    pattern          TEXT NOT NULL DEFAULT 'unknown',                            -- mẫu giải thích chính (khoá trong code) — lọc / đếm
    confidence       TEXT NOT NULL DEFAULT 'low' CHECK (confidence IN ('high', 'medium', 'low')),
    facts            JSONB NOT NULL DEFAULT '{}',                                -- ảnh chụp lúc dựng / làm mới: dòng, ứng viên + điểm, lời giải thích + bằng chứng, hành động đề xuất
    target_codes     TEXT[] NOT NULL DEFAULT '{}',                               -- khoản phải thu hồ sơ nhắm tới (mã) — hồ sơ chưa xong khi còn nợ ở các khoản này
    owner_id         UUID REFERENCES core.users(id),
    team_id          UUID REFERENCES core.teams(id),
    due_on           DATE NOT NULL,
    decision         TEXT NOT NULL DEFAULT '' CHECK (decision IN ('', 'vat_follow_up', 'other_entity', 'asked_payer')),
    decision_note    TEXT NOT NULL DEFAULT '' CHECK (length(decision_note) <= 1000),
    decided_by       UUID REFERENCES core.users(id),
    decided_at       TIMESTAMPTZ,
    draft            TEXT NOT NULL DEFAULT '' CHECK (length(draft) <= 4000),     -- tin nháp hỏi bên trả — KHÔNG BAO GIỜ tự gửi
    draft_at         TIMESTAMPTZ,
    written_off_vnd  BIGINT NOT NULL DEFAULT 0 CHECK (written_off_vnd >= 0),
    resolution_note  TEXT NOT NULL DEFAULT '' CHECK (length(resolution_note) <= 1000),
    closed_at        TIMESTAMPTZ,
    closed_by        UUID REFERENCES core.users(id),
    created_by       UUID REFERENCES core.users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    refreshed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((status = 'open') = (closed_at IS NULL)),
    FOREIGN KEY (tenant_id, line_id) REFERENCES acct.statement_lines (tenant_id, id)
);
ALTER TABLE acct.discrepancy_dossiers ADD CONSTRAINT discrepancy_dossiers_tenant_id_id UNIQUE (tenant_id, id);
CREATE UNIQUE INDEX uq_discrepancy_dossiers_code ON acct.discrepancy_dossiers (tenant_id, code);
CREATE UNIQUE INDEX uq_discrepancy_dossiers_line ON acct.discrepancy_dossiers (tenant_id, line_id);
CREATE INDEX idx_discrepancy_dossiers_open ON acct.discrepancy_dossiers (tenant_id, due_on) WHERE status = 'open';
CREATE INDEX idx_discrepancy_dossiers_owner ON acct.discrepancy_dossiers (tenant_id, owner_id) WHERE status = 'open';
CALL core.secure_table('acct.discrepancy_dossiers', 'acct', 'owner_id', 'team_id');
GRANT SELECT, INSERT, UPDATE ON acct.discrepancy_dossiers TO barista_app;

CREATE TABLE acct.discrepancy_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL DEFAULT core.tenant(),
    dossier_id  UUID NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('opened', 'refreshed', 'decision', 'draft', 'write_off', 'closed', 'assigned')),
    text        TEXT NOT NULL CHECK (length(text) <= 1000),
    data        JSONB NOT NULL DEFAULT '{}',
    created_by  UUID REFERENCES core.users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, dossier_id) REFERENCES acct.discrepancy_dossiers (tenant_id, id)
);
CREATE INDEX idx_discrepancy_events_dossier ON acct.discrepancy_events (tenant_id, dossier_id, created_at);
CALL core.secure_child('acct.discrepancy_events', 'acct.discrepancy_dossiers', 'dossier_id');
GRANT SELECT, INSERT ON acct.discrepancy_events TO barista_app;
