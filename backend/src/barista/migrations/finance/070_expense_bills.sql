-- module: acct — lý do: 0170 — CHI PHÍ VẬN HÀNH: phiếu chi phí / hoá đơn mua vào. (1) PHIẾU (acct.bills): bên bán (party), ngày chứng từ, pháp nhân,
-- cách trả (ghi nợ nhà cung cấp ⇒ một nghĩa vụ acct.payables nguồn acct.bill, chi qua đường đề nghị chi sẵn có; đã trả ngay ⇒ Có tiền gửi / tiền mặt),
-- quyết định thuế GTGT đầu vào CHỤP trên phiếu (khấu trừ / nằm trong chi phí + lý do + mã quy tắc), khoá tự nhiên chống nhập lặp; chỉ thêm — huỷ chỉ
-- điền cột huỷ một lần (bút toán đảo). (2) DÒNG (acct.bill_lines): khoản mục chi (danh mục công ty, tài khoản chi phí), số tiền gồm thuế, phần thuế,
-- chiều chi phí (sản phẩm / dự án, đội / bộ phận, đối tượng tập hợp chi phí); chỉ thêm. (3) ĐỐI TƯỢNG TẬP HỢP CHI PHÍ (acct.cost_objects): dữ liệu
-- công ty (công trình, hợp đồng…). Chỉ bảng mới ⇒ bản cũ chạy song song (không đọc bảng mới) ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

CREATE TABLE acct.cost_objects (
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    code       TEXT NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_.-]{0,39}$'),
    label      TEXT NOT NULL CHECK (btrim(label) <> ''),
    kind       TEXT NOT NULL DEFAULT '',                -- nhãn nhóm công ty tự đặt (vd "Công trình", "Hợp đồng") — chỉ để lọc / hiện
    active     BOOLEAN NOT NULL DEFAULT true,
    updated_by UUID REFERENCES core.users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, code)
);
CALL core.secure_table('acct.cost_objects', 'acct');
GRANT SELECT, INSERT, UPDATE ON acct.cost_objects TO barista_app;

CREATE TABLE acct.bills (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL DEFAULT core.tenant(),
    entity_id         UUID NOT NULL REFERENCES core.legal_entities(id),   -- pháp nhân chịu chi phí (sổ của pháp nhân đó)
    supplier_id       UUID,                                              -- bên bán (parties — không khoá ngoại chéo module); trống = chi không ghi bên bán
    supplier_name     TEXT NOT NULL DEFAULT '',                          -- tên lúc ghi (chụp)
    supplier_key      TEXT NOT NULL DEFAULT '',                          -- tên chuẩn hoá + loại: nhập lại cùng tên dùng lại đúng bên đã ghi
    doc_no            TEXT NOT NULL DEFAULT '',                          -- số hoá đơn mua vào / số chứng từ của bên bán
    ref               TEXT NOT NULL DEFAULT '',                          -- mã riêng của dòng trong nguồn nhập (khoá chống lặp rõ ràng)
    natural_key       TEXT,                                              -- ref:… hoặc doc:<pháp nhân>:<bên bán>:<số> — NULL khi không có cả hai
    fingerprint       TEXT NOT NULL,                                     -- nội dung đã chuẩn hoá: ghi lại cùng khoá mà khác nội dung ⇒ xung đột
    bill_on           DATE NOT NULL,                                     -- ngày chứng từ = ngày hạch toán
    mode              TEXT NOT NULL CHECK (mode IN ('payable', 'paid')),
    pay_account       TEXT NOT NULL DEFAULT '',                          -- tài khoản sổ cái đã trả (cash:…) khi mode = paid
    bank_account_id   UUID REFERENCES acct.bank_accounts(id),
    bank_ref          TEXT NOT NULL DEFAULT '',
    due_on            DATE,
    memo              TEXT NOT NULL DEFAULT '',
    total_vnd         BIGINT NOT NULL CHECK (total_vnd > 0),               -- tổng thanh toán (gồm thuế)
    vat_vnd           BIGINT NOT NULL DEFAULT 0 CHECK (vat_vnd >= 0),      -- tổng thuế GTGT ghi trên chứng từ
    input_vnd         BIGINT NOT NULL DEFAULT 0 CHECK (input_vnd >= 0),    -- phần thuế đã ghi vào thuế GTGT đầu vào được khấu trừ
    vat_treatment     TEXT NOT NULL CHECK (vat_treatment IN ('none', 'input', 'in_cost')),
    vat_note          TEXT NOT NULL DEFAULT '',                          -- vì sao thuế nằm trong chi phí (công tắc tắt, quy tắc chưa xác minh…)
    rule_refs         TEXT[] NOT NULL DEFAULT '{}',                      -- <bộ>@<phiên bản>:<mã> đã xét
    payable_id        UUID,                                              -- nghĩa vụ phải trả nhà cung cấp (mode = payable)
    journal_id        UUID NOT NULL,
    created_by        UUID REFERENCES core.users(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_on      DATE,
    cancelled_by      UUID REFERENCES core.users(id),
    cancel_reason     TEXT NOT NULL DEFAULT '',
    cancel_journal_id UUID,
    UNIQUE (tenant_id, id),
    CHECK ((mode = 'payable') = (payable_id IS NOT NULL)),
    CHECK (mode = 'payable' OR pay_account ~ '^cash:[a-z_]+$'),
    CHECK (mode = 'paid' OR supplier_id IS NOT NULL),
    CHECK (input_vnd <= vat_vnd AND vat_vnd < total_vnd),
    CHECK ((vat_treatment = 'input') = (input_vnd > 0)),
    CHECK (vat_treatment <> 'none' OR vat_vnd = 0),
    CHECK (cancelled_on IS NULL OR (btrim(cancel_reason) <> '' AND cancelled_on >= bill_on))
);
CREATE UNIQUE INDEX uq_bills_natural_key ON acct.bills (tenant_id, natural_key) WHERE natural_key IS NOT NULL;
CREATE INDEX idx_bills_period ON acct.bills (tenant_id, entity_id, bill_on);
CREATE INDEX idx_bills_supplier_key ON acct.bills (tenant_id, supplier_key) WHERE supplier_key <> '';
CALL core.secure_table('acct.bills', 'acct');
GRANT SELECT, INSERT, UPDATE ON acct.bills TO barista_app;

CREATE TABLE acct.bill_lines (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL DEFAULT core.tenant(),
    bill_id       UUID NOT NULL,
    line_no       INT NOT NULL CHECK (line_no >= 1),
    category      TEXT NOT NULL,                                     -- mã khoản mục chi của công ty lúc ghi (chụp)
    account       TEXT NOT NULL CHECK (account ~ '^expense:[a-z_]+$'),   -- tài khoản chi phí đã ghi
    amount_vnd    BIGINT NOT NULL CHECK (amount_vnd > 0),             -- số tiền gồm thuế
    vat_rate_bps  INT CHECK (vat_rate_bps BETWEEN 0 AND 10000),       -- thuế suất ghi trên hoá đơn bên bán (dữ liệu chứng từ, không tính lại)
    vat_vnd       BIGINT NOT NULL DEFAULT 0 CHECK (vat_vnd >= 0),
    input_vnd     BIGINT NOT NULL DEFAULT 0 CHECK (input_vnd >= 0),   -- phần thuế ghi vào thuế đầu vào (0 hoặc đúng vat_vnd)
    expense_vnd   BIGINT NOT NULL CHECK (expense_vnd > 0),            -- phần ghi vào chi phí = số tiền − thuế đầu vào
    offering_code TEXT NOT NULL DEFAULT '',                          -- [[offering]] (dự án / sản phẩm của gói khai qua điểm cắm acct.cost_offering)
    team_id       UUID REFERENCES core.teams(id),                    -- đội / bộ phận chịu chi phí
    cost_object   TEXT NOT NULL DEFAULT '',                          -- mã đối tượng tập hợp chi phí (acct.cost_objects)
    memo          TEXT NOT NULL DEFAULT '',
    UNIQUE (tenant_id, bill_id, line_no),
    CHECK (amount_vnd = expense_vnd + input_vnd),
    CHECK (input_vnd = 0 OR input_vnd = vat_vnd),
    CHECK (vat_vnd < amount_vnd),
    FOREIGN KEY (tenant_id, bill_id) REFERENCES acct.bills(tenant_id, id)
);
CREATE INDEX idx_bill_lines_bill ON acct.bill_lines (tenant_id, bill_id);
CALL core.secure_child('acct.bill_lines', 'acct.bills', 'bill_id');
GRANT SELECT, INSERT ON acct.bill_lines TO barista_app;

-- chỉ thêm: không xoá; sửa duy nhất được phép là điền cột HUỶ một lần (từ trống sang có), mọi cột khác giữ nguyên
CREATE FUNCTION acct.bill_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Phiếu chi phí chỉ thêm — không xoá (huỷ bằng bút toán đảo)';
    END IF;
    IF OLD.cancelled_on IS NOT NULL
       OR NEW.cancelled_on IS NULL
       OR (to_jsonb(NEW) - ARRAY['cancelled_on', 'cancelled_by', 'cancel_reason', 'cancel_journal_id'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['cancelled_on', 'cancelled_by', 'cancel_reason', 'cancel_journal_id']) THEN
        RAISE EXCEPTION 'Phiếu chi phí chỉ thêm — chỉ được ghi huỷ một lần';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER t_bill_guard BEFORE UPDATE OR DELETE ON acct.bills FOR EACH ROW EXECUTE FUNCTION acct.bill_guard();

CREATE FUNCTION acct.bill_line_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Dòng phiếu chi phí chỉ thêm — không sửa / xoá (huỷ cả phiếu bằng bút toán đảo)';
END $$;
CREATE TRIGGER t_bill_line_guard BEFORE UPDATE OR DELETE ON acct.bill_lines FOR EACH ROW EXECUTE FUNCTION acct.bill_line_guard();
