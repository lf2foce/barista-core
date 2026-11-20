-- module: acct
-- 013 (0099): chứng từ tiền mang PHÁP NHÂN — khoản phải thu (pháp nhân ghi doanh thu, vd theo dự án), phiếu thu (= pháp nhân của khoản),
-- dòng sao kê (= pháp nhân của tài khoản ngân hàng), lệnh chi, chi trả thu hộ. Mặc định = pháp nhân mặc định của công ty. Chỉ THÊM.
ALTER TABLE acct.receivables ADD COLUMN entity_id UUID REFERENCES core.legal_entities(id);
ALTER TABLE acct.payments ADD COLUMN entity_id UUID REFERENCES core.legal_entities(id);
ALTER TABLE acct.payouts ADD COLUMN entity_id UUID REFERENCES core.legal_entities(id);
ALTER TABLE acct.statement_lines ADD COLUMN entity_id UUID REFERENCES core.legal_entities(id);
ALTER TABLE acct.remittances ADD COLUMN entity_id UUID REFERENCES core.legal_entities(id);

UPDATE acct.receivables t SET entity_id = e.id FROM core.legal_entities e WHERE e.tenant_id = t.tenant_id AND e.is_default;
UPDATE acct.payments t SET entity_id = e.id FROM core.legal_entities e WHERE e.tenant_id = t.tenant_id AND e.is_default;
UPDATE acct.payouts t SET entity_id = e.id FROM core.legal_entities e WHERE e.tenant_id = t.tenant_id AND e.is_default;
UPDATE acct.statement_lines t SET entity_id = e.id FROM core.legal_entities e WHERE e.tenant_id = t.tenant_id AND e.is_default;
UPDATE acct.remittances t SET entity_id = e.id FROM core.legal_entities e WHERE e.tenant_id = t.tenant_id AND e.is_default;

ALTER TABLE acct.receivables ALTER COLUMN entity_id SET NOT NULL;
CREATE TRIGGER t_receivables_entity BEFORE INSERT ON acct.receivables FOR EACH ROW EXECUTE FUNCTION core.fill_entity();
ALTER TABLE acct.payments ALTER COLUMN entity_id SET NOT NULL;
CREATE TRIGGER t_payments_entity BEFORE INSERT ON acct.payments FOR EACH ROW EXECUTE FUNCTION core.fill_entity();
ALTER TABLE acct.payouts ALTER COLUMN entity_id SET NOT NULL;
CREATE TRIGGER t_payouts_entity BEFORE INSERT ON acct.payouts FOR EACH ROW EXECUTE FUNCTION core.fill_entity();
ALTER TABLE acct.statement_lines ALTER COLUMN entity_id SET NOT NULL;
CREATE TRIGGER t_statement_lines_entity BEFORE INSERT ON acct.statement_lines FOR EACH ROW EXECUTE FUNCTION core.fill_entity();
ALTER TABLE acct.remittances ALTER COLUMN entity_id SET NOT NULL;
CREATE TRIGGER t_remittances_entity BEFORE INSERT ON acct.remittances FOR EACH ROW EXECUTE FUNCTION core.fill_entity();
