-- module: acct
-- 0182 — tiền đặt tại đối tác được HOÀN về: phần phân bổ của MỘT dòng sao kê (0057) cũng là một sự thật có dòng riêng, như phiếu thu (acct.payments) và tiền
-- thu hồi (acct.recovery_receipts) — để bất biến "đã phân bổ của dòng = tổng các phần phân bổ" vẫn đúng. Đảo tiền hoàn = dòng âm (chỉ thêm). Bảng mới, rỗng
-- ⇒ không bảo trì.
CREATE TABLE acct.placement_receipts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 line_id UUID NOT NULL,
 amount_vnd BIGINT NOT NULL CHECK (amount_vnd <> 0),
 journal_id UUID NOT NULL,
 source TEXT NOT NULL CHECK (source ~ '^[a-z_]+\.[a-z_]+$'),
 source_ref TEXT NOT NULL CHECK (length(source_ref) BETWEEN 1 AND 200),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, journal_id),
 FOREIGN KEY (tenant_id, line_id) REFERENCES acct.statement_lines (tenant_id, id)
);
CREATE INDEX idx_placement_receipts_line ON acct.placement_receipts (tenant_id, line_id);
CALL core.secure_child('acct.placement_receipts', 'acct.statement_lines', 'line_id');
GRANT SELECT, INSERT ON acct.placement_receipts TO barista_app;
CREATE TRIGGER placement_receipt_immutable BEFORE UPDATE OR DELETE ON acct.placement_receipts FOR EACH ROW EXECUTE FUNCTION acct.immutable_settlement_fact();
