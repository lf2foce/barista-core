-- module: acct — lý do: 0156 — nhà cho tiền "âm" và tiền thừa mà KHÔNG nới ràng buộc số dương: sửa = bản ghi điều chỉnh chỉ thêm, trỏ vào cái nó sửa,
-- đi cùng bút toán (đảo) trong sổ cái. (1) giấy báo có / giảm phải thu (credit note) trên khoản phải thu; (2) đảo một phần / toàn bộ phiếu thu — tiền
-- quay về "tiền về chưa phân bổ" của đúng dòng sao kê (không biến mất, phân bổ lại được); (3) danh mục KHOẢN THU / KHOẢN CHI của công ty (dữ liệu, mỗi
-- mục một tài khoản sổ cái đã khai) để khoản thưởng / khoản khác nhập và báo cáo được. Chỉ thêm cột có mặc định + bảng mới ⇒ bản cũ chạy song song
-- (không đọc cột mới; ``credited_vnd`` = 0 với mọi khoản cũ) ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

ALTER TABLE acct.receivables
    ADD COLUMN credited_vnd BIGINT NOT NULL DEFAULT 0 CHECK (credited_vnd >= 0),   -- tổng giấy báo có đã ghi (số gốc = amount_vnd + credited_vnd)
    ADD COLUMN category TEXT NOT NULL DEFAULT '' CHECK (category ~ '^[a-z0-9_]{0,40}$');
ALTER TABLE acct.receivables ADD CONSTRAINT receivables_tenant_id_id UNIQUE (tenant_id, id);
ALTER TABLE acct.payments ADD CONSTRAINT payments_tenant_id_id UNIQUE (tenant_id, id);

-- Giấy báo có: giảm số phải thu của MỘT khoản (CĐT giảm / thu hồi phí, sửa số lập sai). Số dương + lý do; bút toán Nợ doanh thu / Có phải thu.
CREATE TABLE acct.receivable_credits (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL DEFAULT core.tenant(),
    receivable_id UUID NOT NULL,
    amount_vnd    BIGINT NOT NULL CHECK (amount_vnd > 0),
    reason        TEXT NOT NULL CHECK (btrim(reason) <> ''),
    corrects      TEXT NOT NULL DEFAULT '',          -- chứng từ / dòng nó sửa (mã đợt, số biên bản…) — chữ của người ghi
    credited_on   DATE NOT NULL,
    journal_id    UUID NOT NULL,
    created_by    UUID REFERENCES core.users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, receivable_id) REFERENCES acct.receivables(tenant_id, id)
);
-- cùng chứng từ sửa (vd mã dòng của hệ cũ) ghi lại ⇒ không ghi lần hai
CREATE UNIQUE INDEX uq_receivable_credits_corrects ON acct.receivable_credits (tenant_id, receivable_id, corrects) WHERE corrects <> '';
CALL core.secure_child('acct.receivable_credits', 'acct.receivables', 'receivable_id');
GRANT SELECT, INSERT ON acct.receivable_credits TO barista_app;
CREATE TRIGGER receivable_credit_immutable BEFORE UPDATE OR DELETE ON acct.receivable_credits FOR EACH ROW EXECUTE FUNCTION acct.immutable_settlement_fact();

-- Đảo phiếu thu (một phần / toàn bộ): phiếu gốc giữ nguyên; tiền về lại "chưa phân bổ" của dòng sao kê gốc. Số dương ≤ phần chưa đảo (lệnh kiểm dưới khoá).
CREATE TABLE acct.payment_reversals (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL DEFAULT core.tenant(),
    payment_id  UUID NOT NULL,
    amount_vnd  BIGINT NOT NULL CHECK (amount_vnd > 0),
    reason      TEXT NOT NULL CHECK (btrim(reason) <> ''),
    corrects    TEXT NOT NULL DEFAULT '',           -- chứng từ / dòng nó sửa — cùng giá trị ghi lại ⇒ không đảo lần hai
    reversed_on DATE NOT NULL,
    journal_id  UUID NOT NULL,
    created_by  UUID REFERENCES core.users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, payment_id) REFERENCES acct.payments(tenant_id, id)
);
CALL core.secure_child('acct.payment_reversals', 'acct.payments', 'payment_id');
GRANT SELECT, INSERT ON acct.payment_reversals TO barista_app;
CREATE TRIGGER payment_reversal_immutable BEFORE UPDATE OR DELETE ON acct.payment_reversals FOR EACH ROW EXECUTE FUNCTION acct.immutable_settlement_fact();
CREATE INDEX idx_payment_reversals_payment ON acct.payment_reversals (tenant_id, payment_id);
CREATE UNIQUE INDEX uq_payment_reversals_corrects ON acct.payment_reversals (tenant_id, payment_id, corrects) WHERE corrects <> '';

-- Danh mục khoản thu / chi của công ty: trống ⇒ danh mục mặc định trong code; có dòng ⇒ chỉ nhận mã công ty khai (như loại hình căn).
CREATE TABLE acct.categories (
    tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    code      TEXT NOT NULL CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'),
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    label     TEXT NOT NULL CHECK (btrim(label) <> ''),
    account   TEXT NOT NULL CHECK (account ~ '^(revenue|expense):[a-z_]+$'),
    active    BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, code),
    CHECK ((direction = 'in') = starts_with(account, 'revenue:'))
);
CALL core.secure_table('acct.categories', 'acct');
GRANT SELECT, INSERT, UPDATE ON acct.categories TO barista_app;
