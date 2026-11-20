-- module: acct
-- ADR 0151: tiền vào / ra theo TÀI KHOẢN ngân hàng của công ty và số hoá đơn đã xuất. Chỉ THÊM bảng / cột có mặc định ⇒ bản cũ chạy song song
-- không vỡ (bản cũ không biết cột mới, không ghi vào bảng mới) ⇒ không cần dừng dịch vụ.
SET LOCAL lock_timeout = '10s';

-- Tài khoản ngân hàng của công ty: mỗi tài khoản thuộc một pháp nhân; ``columns`` = MẪU CỘT sao kê của ngân hàng đó (dữ liệu công ty — tên cột
-- trong file tải từ ngân hàng ứng với ô nào), không viết trong code.
CREATE TABLE acct.bank_accounts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    code        TEXT NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{1,29}$'),
    label       TEXT NOT NULL CHECK (btrim(label) <> ''),
    bank        TEXT NOT NULL DEFAULT '',
    account_no  TEXT NOT NULL DEFAULT '' CHECK (account_no ~ '^[0-9]{0,30}$'),
    entity_id   UUID NOT NULL REFERENCES core.legal_entities(id),
    columns     JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(columns) = 'object'),
    active      BOOLEAN NOT NULL DEFAULT true,
    created_by  UUID REFERENCES core.users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code),
    UNIQUE (tenant_id, id)
);
CREATE UNIQUE INDEX uq_bank_accounts_no ON acct.bank_accounts (tenant_id, account_no) WHERE account_no <> '';
CREATE TRIGGER t_bank_accounts_entity BEFORE INSERT ON acct.bank_accounts FOR EACH ROW EXECUTE FUNCTION core.fill_entity();
CALL core.secure_table('acct.bank_accounts', 'acct');
GRANT SELECT, INSERT, UPDATE ON acct.bank_accounts TO barista_app;

-- Dòng tiền VÀO biết thuộc tài khoản nào (trống = phiếu thu nhập tay / sao kê cũ chưa gắn tài khoản)
ALTER TABLE acct.statement_lines ADD COLUMN bank_account_id UUID REFERENCES acct.bank_accounts(id);
CREATE INDEX idx_statement_lines_account ON acct.statement_lines (tenant_id, bank_account_id, received_on);

-- Dòng tiền RA của sao kê: chỉ để ĐỐI CHIẾU với lệnh chi đã ghi (chi ngoài hệ thống ⇒ cảnh báo) — không ghi sổ cái từ đây (lệnh chi ghi sổ khi
-- kế toán xác nhận chuyển khoản). Một giao dịch ngân hàng một dòng; "bỏ qua" phải có lý do (chi thuê văn phòng, thuế… chưa quản lý ở đây).
CREATE TABLE acct.bank_out_lines (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    bank_account_id UUID REFERENCES acct.bank_accounts(id),
    entity_id       UUID NOT NULL REFERENCES core.legal_entities(id),
    paid_on         DATE NOT NULL,
    amount_vnd      BIGINT NOT NULL CHECK (amount_vnd > 0),
    memo            TEXT NOT NULL DEFAULT '',
    payee           TEXT NOT NULL DEFAULT '',
    bank_ref        TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'ignored')),
    note            TEXT NOT NULL DEFAULT '',
    created_by      UUID REFERENCES core.users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by      UUID REFERENCES core.users(id),
    decided_at      TIMESTAMPTZ,
    CHECK (status <> 'ignored' OR btrim(note) <> '')
);
CREATE UNIQUE INDEX uq_bank_out_lines_ref ON acct.bank_out_lines (tenant_id, bank_ref) WHERE bank_ref <> '';
CREATE INDEX idx_bank_out_lines_day ON acct.bank_out_lines (tenant_id, bank_account_id, paid_on);
CREATE TRIGGER t_bank_out_lines_entity BEFORE INSERT ON acct.bank_out_lines FOR EACH ROW EXECUTE FUNCTION core.fill_entity();
CALL core.secure_table('acct.bank_out_lines', 'acct');
GRANT SELECT, INSERT, UPDATE ON acct.bank_out_lines TO barista_app;

-- Số hoá đơn đã xuất cho bên trả (kế toán ghi sau khi phát hành hoá đơn điện tử) — bên trả ghi số này vào nội dung chuyển khoản ⇒ đối soát khớp
ALTER TABLE acct.receivables
    ADD COLUMN invoice_no  TEXT NOT NULL DEFAULT '' CHECK (invoice_no ~ '^[0-9A-Za-z/._-]{0,40}$'),
    ADD COLUMN invoice_on  DATE,
    ADD COLUMN invoiced_by UUID REFERENCES core.users(id);
