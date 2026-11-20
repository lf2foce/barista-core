-- module: acct — lý do: 0165 — thuế GTGT ĐẦU RA lát A. (1) LOẠI THUẾ của công ty (dữ liệu: cách tính theo luật, có được giảm 2% không, giá đã gồm
-- thuế hay chưa, áp mặc định cho tài khoản doanh thu / nguồn nào) — mức thuế KHÔNG nằm ở đây mà ở bộ quy định vn-vat theo ngày hoá đơn; (2) SỔ HOÁ ĐƠN
-- GTGT: mỗi lần xuất / điều chỉnh là một bản ghi chỉ thêm (ký hiệu, số, ngày, người lập, mức đã CHỤP + mã quy tắc), đi cùng bút toán sổ cái; huỷ chỉ
-- điền cột huỷ một lần (bút toán đảo). Chỉ bảng mới ⇒ bản cũ chạy song song (không đọc bảng mới) ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

CREATE TABLE acct.tax_categories (
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    code       TEXT NOT NULL CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'),
    label      TEXT NOT NULL CHECK (btrim(label) <> ''),
    treatment  TEXT NOT NULL CHECK (treatment IN ('standard', 'five', 'zero', 'exempt', 'not_declared')),
    reducible  BOOLEAN NOT NULL DEFAULT false,          -- được giảm 2% trong khoảng ngày của luật (chỉ thuế suất phổ thông) — kế toán đối chiếu danh mục loại trừ
    price_mode TEXT NOT NULL DEFAULT 'exclusive' CHECK (price_mode IN ('exclusive', 'inclusive')),
    applies_to TEXT[] NOT NULL DEFAULT '{}',            -- mặc định cho khoản phải thu có tài khoản doanh thu / nguồn này (vd revenue:brokerage, edu.package)
    note       TEXT NOT NULL DEFAULT '',                -- căn cứ công ty chọn loại này (ai xác nhận, ngày nào)
    active     BOOLEAN NOT NULL DEFAULT true,
    updated_by UUID REFERENCES core.users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, code),
    CHECK (treatment = 'standard' OR NOT reducible)
);
CALL core.secure_table('acct.tax_categories', 'acct');
GRANT SELECT, INSERT, UPDATE ON acct.tax_categories TO barista_app;

CREATE TABLE acct.vat_invoices (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL DEFAULT core.tenant(),
    receivable_id     UUID NOT NULL,
    entity_id         UUID NOT NULL REFERENCES core.legal_entities(id),   -- pháp nhân bán (mỗi mã số thuế một tờ khai) = pháp nhân của khoản phải thu
    kind              TEXT NOT NULL CHECK (kind IN ('invoice', 'adjustment')),
    adjusts           UUID REFERENCES acct.vat_invoices(id),            -- hoá đơn điều chỉnh GIẢM trỏ hoá đơn gốc
    symbol            TEXT NOT NULL CHECK (symbol ~ '^[0-9A-Z]{1,12}$'),  -- ký hiệu mẫu số + ký hiệu hoá đơn (vd 1C26TAA)
    number            TEXT NOT NULL CHECK (number ~ '^[0-9]{1,12}$'),
    issued_on         DATE NOT NULL,                                     -- ngày lập hoá đơn = ngày chọn mức thuế + ngày hạch toán
    category          TEXT NOT NULL,                                     -- loại thuế của công ty lúc xuất (chụp, không trỏ — loại đổi sau không đổi hoá đơn cũ)
    treatment         TEXT NOT NULL CHECK (treatment IN ('standard', 'five', 'zero', 'exempt', 'not_declared')),
    rate_bps          INT CHECK (rate_bps BETWEEN 0 AND 10000),          -- NULL = không tính thuế (không chịu thuế / không kê khai)
    price_mode        TEXT NOT NULL CHECK (price_mode IN ('exclusive', 'inclusive')),
    base_vnd          BIGINT NOT NULL CHECK (base_vnd >= 0),              -- giá tính thuế (điều chỉnh: phần GIẢM, số dương)
    tax_vnd           BIGINT NOT NULL CHECK (tax_vnd >= 0),
    gross_vnd         BIGINT NOT NULL CHECK (gross_vnd > 0),
    rule_refs         TEXT[] NOT NULL CHECK (cardinality(rule_refs) > 0),  -- <bộ>@<phiên bản>:<mã> đã áp — chỉ dùng quy tắc đã xác minh
    journal_id        UUID,                                              -- bút toán thuế (NULL khi không có số thuế phải tách)
    reason            TEXT NOT NULL DEFAULT '',
    issued_by         UUID REFERENCES core.users(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_on      DATE,
    cancelled_by      UUID REFERENCES core.users(id),
    cancel_reason     TEXT NOT NULL DEFAULT '',
    cancel_journal_id UUID,
    provider          TEXT NOT NULL DEFAULT '',                          -- đầu nối nhà cung cấp hoá đơn điện tử: lát sau
    provider_ref      TEXT NOT NULL DEFAULT '',
    CHECK (gross_vnd = base_vnd + tax_vnd),
    CHECK ((rate_bps IS NULL) = (treatment IN ('exempt', 'not_declared'))),
    CHECK (rate_bps IS NOT NULL OR tax_vnd = 0),
    CHECK ((kind = 'adjustment') = (adjusts IS NOT NULL)),
    CHECK (kind = 'invoice' OR (cancelled_on IS NULL AND btrim(reason) <> '')),
    CHECK (cancelled_on IS NULL OR (btrim(cancel_reason) <> '' AND cancelled_on >= issued_on)),
    FOREIGN KEY (tenant_id, receivable_id) REFERENCES acct.receivables(tenant_id, id)
);
-- một ký hiệu + số chỉ dùng MỘT lần trong một pháp nhân (kể cả hoá đơn đã huỷ — số đã cấp không cấp lại)
CREATE UNIQUE INDEX uq_vat_invoices_number ON acct.vat_invoices (tenant_id, entity_id, symbol, number);
-- mỗi khoản phải thu tối đa một hoá đơn gốc còn hiệu lực (huỷ rồi mới lập hoá đơn thay thế)
CREATE UNIQUE INDEX uq_vat_invoices_active ON acct.vat_invoices (tenant_id, receivable_id) WHERE kind = 'invoice' AND cancelled_on IS NULL;
CREATE INDEX idx_vat_invoices_period ON acct.vat_invoices (tenant_id, entity_id, issued_on);
CALL core.secure_child('acct.vat_invoices', 'acct.receivables', 'receivable_id');
GRANT SELECT, INSERT, UPDATE ON acct.vat_invoices TO barista_app;

-- chỉ thêm: không xoá; sửa duy nhất được phép là điền cột HUỶ một lần (từ trống sang có), mọi cột khác giữ nguyên
CREATE FUNCTION acct.vat_invoice_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Hoá đơn GTGT chỉ thêm — không xoá (huỷ bằng bút toán đảo)';
    END IF;
    IF OLD.cancelled_on IS NOT NULL
       OR NEW.cancelled_on IS NULL
       OR (to_jsonb(NEW) - ARRAY['cancelled_on', 'cancelled_by', 'cancel_reason', 'cancel_journal_id'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['cancelled_on', 'cancelled_by', 'cancel_reason', 'cancel_journal_id']) THEN
        RAISE EXCEPTION 'Hoá đơn GTGT chỉ thêm — chỉ được ghi huỷ một lần';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER t_vat_invoice_guard BEFORE UPDATE OR DELETE ON acct.vat_invoices FOR EACH ROW EXECUTE FUNCTION acct.vat_invoice_guard();
