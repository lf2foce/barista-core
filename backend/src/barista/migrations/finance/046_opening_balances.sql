-- module: acct, core — lý do: bảng lô số dư đầu kỳ của Tài chính (0119); chuỗi duyệt "mở lại kỳ" cho công ty đang chạy (phát hành chỉ chạy migration — như 020)
-- 0119 §3: SỐ DƯ ĐẦU KỲ theo lô. Dòng nhập (Excel / tay) vào `opening_lines` dưới một MÃ LÔ (bản nháp: batch_id NULL), rồi `acct.open_balances`
-- kiểm hàng rào + tổng kiểm và ghi MỘT bút toán mỗi dòng (nguồn sổ cái `acct.opening_batch`, đối ứng `equity:opening`, posted_on = ngày cắt D).
-- Khoản còn mở (phải thu / phải trả / ứng) còn thành dòng sổ phụ nguồn `<miền>.opening` (acct.receivables / acct.payables) — chỉ module của
-- đúng miền cấn trừ được. Chỉ THÊM ⇒ rolling.
CREATE TABLE acct.opening_batches (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    code                TEXT NOT NULL CHECK (code ~ '^[A-Za-z0-9_.-]{1,40}$'),
    entity_id           UUID NOT NULL REFERENCES core.legal_entities(id),
    cut_on              DATE NOT NULL,
    completeness        TEXT NOT NULL CHECK (completeness IN ('full', 'partial')),
    control_debit_vnd   BIGINT,
    control_credit_vnd  BIGINT,
    control_equity_vnd  BIGINT,
    debit_vnd           BIGINT NOT NULL CHECK (debit_vnd >= 0),
    credit_vnd          BIGINT NOT NULL CHECK (credit_vnd >= 0),
    difference_vnd      BIGINT NOT NULL DEFAULT 0,
    difference_reason   TEXT NOT NULL DEFAULT '' CHECK (length(difference_reason) <= 500),
    more_history_later  BOOLEAN NOT NULL,
    locked              BOOLEAN NOT NULL,
    status              TEXT NOT NULL DEFAULT 'posted' CHECK (status IN ('posted', 'reversed')),
    created_by          UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    reversed_by         UUID,
    reversed_at         TIMESTAMPTZ,
    reverse_reason      TEXT NOT NULL DEFAULT '' CHECK (length(reverse_reason) <= 500),
    CHECK (completeness = 'partial' OR difference_vnd = 0),
    CHECK (difference_vnd = 0 OR btrim(difference_reason) <> ''),
    UNIQUE (tenant_id, id)
);
CREATE UNIQUE INDEX uq_opening_batch_code ON acct.opening_batches (tenant_id, code) WHERE status = 'posted';
CALL core.secure_table('acct.opening_batches', 'acct');
GRANT SELECT, INSERT ON acct.opening_batches TO barista_app;
GRANT UPDATE (status, reversed_by, reversed_at, reverse_reason) ON acct.opening_batches TO barista_app;

CREATE TABLE acct.opening_lines (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    batch       TEXT NOT NULL CHECK (batch ~ '^[A-Za-z0-9_.-]{1,40}$'),
    batch_id    UUID,                                   -- NULL = bản nháp; ghi xong ⇒ lô đã ghi
    kind        TEXT NOT NULL CHECK (kind IN ('account', 'receivable', 'payable', 'advance')),
    account     TEXT NOT NULL DEFAULT '',
    domain      TEXT NOT NULL DEFAULT '' CHECK (domain ~ '^[a-z_]*$' AND domain <> 'acct'),
    party_id    UUID,
    party       TEXT NOT NULL DEFAULT '' CHECK (length(party) <= 300),
    label       TEXT NOT NULL CHECK (btrim(label) <> '' AND length(label) <= 300),
    ref         TEXT NOT NULL DEFAULT '' CHECK (length(ref) <= 60),
    debit_vnd   BIGINT NOT NULL DEFAULT 0 CHECK (debit_vnd >= 0),
    credit_vnd  BIGINT NOT NULL DEFAULT 0 CHECK (credit_vnd >= 0),
    issued_on   DATE,
    due_on      DATE,
    journal_key TEXT NOT NULL DEFAULT '',
    created_by  UUID,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((debit_vnd > 0) <> (credit_vnd > 0)),
    CHECK ((kind = 'account') = (domain = '')),
    CHECK (kind = 'account' OR issued_on IS NOT NULL),
    FOREIGN KEY (tenant_id, batch_id) REFERENCES acct.opening_batches (tenant_id, id)
);
CREATE INDEX idx_opening_lines_batch ON acct.opening_lines (tenant_id, batch) WHERE batch_id IS NULL;
CREATE INDEX idx_opening_lines_posted ON acct.opening_lines (batch_id);
CALL core.secure_table('acct.opening_lines', 'acct');
GRANT SELECT, INSERT, DELETE ON acct.opening_lines TO barista_app;
GRANT UPDATE (batch_id, journal_key) ON acct.opening_lines TO barista_app;
-- dòng đã thuộc lô đã ghi là chứng từ: không xoá, không đổi (bản nháp xoá / sửa được)
CREATE FUNCTION acct.opening_line_frozen() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
    IF OLD.batch_id IS NOT NULL THEN RAISE EXCEPTION 'Dòng đầu kỳ đã ghi sổ — sửa bằng đảo lô (mở kỳ có duyệt)'; END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER opening_line_frozen BEFORE UPDATE OR DELETE ON acct.opening_lines FOR EACH ROW EXECUTE FUNCTION acct.opening_line_frozen();

-- chuỗi duyệt "mở lại kỳ đã khoá" (0119 §2) cho công ty đã bật Tài chính; chuỗi công ty đã có / đã cài rồi xoá giữ nguyên
INSERT INTO core.approval_chains(tenant_id, key, label, steps)
SELECT p.tenant_id, 'period_reopen', 'Mở lại kỳ kế toán đã khoá', '[{"kind":"role","role":"director"}]'::jsonb
FROM core.tenant_packs p WHERE p.pack = 'finance'
  AND NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = p.tenant_id AND i.kind = 'chain' AND i.key = 'period_reopen')
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs(tenant_id, kind, key, pack, version)
SELECT tenant_id, 'chain', 'period_reopen', 'finance', NULL FROM core.tenant_packs WHERE pack = 'finance' ON CONFLICT DO NOTHING;
