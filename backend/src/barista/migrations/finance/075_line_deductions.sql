-- module: acct
-- 0217 — KHẤU TRỪ CỦA BÊN TRẢ trên MỘT dòng sao kê (0057): bên thu hộ (hãng vận chuyển thu COD, sàn TMĐT) chuyển về MỘT khoản đã TRỪ phí cho NHIỀU khoản
-- phải thu ⇒ dòng sao kê = tổng các khoản − phí. Phần bên trả giữ lại (phí vận chuyển, phí sàn, tiền sàn đã hoàn khách) là một sự thật có dòng riêng
-- (``acct.line_deductions`` — như phiếu thu / tiền thu hồi / tiền đặt hoàn về), ghi Nợ tài khoản chi phí / phải trả của gói nguồn, Có tiền về chưa phân bổ
-- ⇒ dòng phân bổ được đúng số GỘP vào các khoản phải thu (``acct.settle_line``). Chỉ THÊM cột có mặc định + bảng mới rỗng, và NỚI hai CHECK: bản cũ không
-- ghi khấu trừ ⇒ ``deducted_vnd = 0`` ⇒ CHECK mới trùng CHECK cũ trên mọi dòng bản cũ ghi ⇒ chạy song song đúng ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
ALTER TABLE acct.statement_lines ADD COLUMN deducted_vnd BIGINT NOT NULL DEFAULT 0 CHECK (deducted_vnd >= 0);
COMMENT ON COLUMN acct.statement_lines.deducted_vnd IS 'Phần bên trả giữ lại (phí, tiền đã hoàn khách) = tổng acct.line_deductions; dòng phân bổ được amount_vnd + deducted_vnd (0217)';
ALTER TABLE acct.statement_lines DROP CONSTRAINT statement_lines_allocated_check;
ALTER TABLE acct.statement_lines ADD CONSTRAINT statement_lines_allocated_check
 CHECK (allocated_vnd >= 0 AND allocated_vnd <= amount_vnd + deducted_vnd) NOT VALID;
ALTER TABLE acct.statement_lines VALIDATE CONSTRAINT statement_lines_allocated_check;
ALTER TABLE acct.statement_lines DROP CONSTRAINT statement_lines_check;
ALTER TABLE acct.statement_lines ADD CONSTRAINT statement_lines_check CHECK ((status = 'applied') = (allocated_vnd = amount_vnd + deducted_vnd)) NOT VALID;
ALTER TABLE acct.statement_lines VALIDATE CONSTRAINT statement_lines_check;

CREATE TABLE acct.line_deductions (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 line_id UUID NOT NULL,
 account TEXT NOT NULL CHECK (account ~ '^[a-z_]+:[a-z0-9_]+$'),
 amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
 memo TEXT NOT NULL DEFAULT '' CHECK (length(memo) <= 300),
 source TEXT NOT NULL CHECK (source ~ '^[a-z_]+\.[a-z_]+$'),            -- bảng kê của bên thu hộ ở gói nguồn (vd <module>.cod_batch)
 source_ref TEXT NOT NULL CHECK (length(source_ref) BETWEEN 1 AND 200),
 journal_id UUID NOT NULL,
 created_by UUID REFERENCES core.users (id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id, line_id) REFERENCES acct.statement_lines (tenant_id, id)
);
CREATE INDEX idx_line_deductions_line ON acct.line_deductions (tenant_id, line_id);
CALL core.secure_child('acct.line_deductions', 'acct.statement_lines', 'line_id');
GRANT SELECT, INSERT ON acct.line_deductions TO barista_app;
CREATE TRIGGER line_deduction_immutable BEFORE UPDATE OR DELETE ON acct.line_deductions FOR EACH ROW EXECUTE FUNCTION acct.immutable_settlement_fact();
