-- module: parties — định danh người (0111): gộp bằng con trỏ, mã định danh (SĐT / email), quan hệ, sự đồng ý, nghi trùng. Tách từ
-- directory/024_crm_capability_identity.sql (0130, ghi chú 08/11: bảng của gói về thư mục gói; phần nối khách CRM ở crm/024_customer_identity.sql —
-- gói crm cần gói directory nên chạy sau). deployment: maintenance
ALTER TABLE parties.parties
  ADD COLUMN merged_into UUID,
  ADD COLUMN listed BOOLEAN NOT NULL DEFAULT true,
  ADD CONSTRAINT parties_merged_into_fk FOREIGN KEY (tenant_id, merged_into) REFERENCES parties.parties (tenant_id, id),
  ADD CONSTRAINT parties_not_merged_into_self CHECK (merged_into IS NULL OR merged_into <> id);
CREATE INDEX idx_parties_merged_into ON parties.parties (tenant_id, merged_into) WHERE merged_into IS NOT NULL;
CREATE FUNCTION parties.no_merge_cycle() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN
 IF NEW.merged_into IS NOT NULL AND EXISTS (
   WITH RECURSIVE up AS (SELECT id, merged_into FROM parties.parties WHERE id = NEW.merged_into
                         UNION SELECT p.id, p.merged_into FROM parties.parties p JOIN up ON p.id = up.merged_into)
   SELECT 1 FROM up WHERE id = NEW.id) THEN
  RAISE EXCEPTION 'Gộp này tạo vòng giữa các định danh' USING ERRCODE = 'check_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER no_merge_cycle BEFORE UPDATE OF merged_into ON parties.parties FOR EACH ROW EXECUTE FUNCTION parties.no_merge_cycle();
COMMENT ON COLUMN parties.parties.merged_into IS 'Gộp bằng con trỏ (0111): dòng ở module khác giữ nguyên party_id; tra người qua parties.cluster';
COMMENT ON COLUMN parties.parties.listed IS 'Hiện trong danh mục chọn. Định danh tự tạo từ khách = false để không ngập danh mục bên nhận tiền';

CREATE TABLE parties.identifiers (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 party_id UUID NOT NULL,
 kind TEXT NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*$'),
 value TEXT NOT NULL CHECK (btrim(value) <> ''),
 source TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id, party_id) REFERENCES parties.parties (tenant_id, id),
 UNIQUE (tenant_id, kind, value)
);
CREATE INDEX idx_identifiers_party ON parties.identifiers (tenant_id, party_id);
CALL core.secure_table('parties.identifiers', 'parties.contacts');

CREATE TABLE parties.relations (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 party_id UUID NOT NULL,
 related_party_id UUID NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('guardian_of', 'pays_for', 'represents', 'refers')),
 note TEXT NOT NULL DEFAULT '',
 started_on DATE NOT NULL DEFAULT core.today(),
 ended_on DATE,
 created_by UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id, party_id) REFERENCES parties.parties (tenant_id, id),
 FOREIGN KEY (tenant_id, related_party_id) REFERENCES parties.parties (tenant_id, id),
 CHECK (party_id <> related_party_id),
 CHECK (ended_on IS NULL OR ended_on >= started_on)
);
CREATE UNIQUE INDEX uq_relations_open ON parties.relations (tenant_id, party_id, related_party_id, kind) WHERE ended_on IS NULL;
CREATE INDEX idx_relations_related ON parties.relations (tenant_id, related_party_id);
CALL core.secure_table('parties.relations', 'parties.contacts');

CREATE TABLE parties.consents (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 party_id UUID NOT NULL,
 purpose TEXT NOT NULL CHECK (purpose ~ '^[a-z][a-z0-9_]*$'),
 granted BOOLEAN NOT NULL,
 given_by_party_id UUID,
 source TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '',
 recorded_by UUID,
 at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id, party_id) REFERENCES parties.parties (tenant_id, id),
 FOREIGN KEY (tenant_id, given_by_party_id) REFERENCES parties.parties (tenant_id, id)
);
CREATE INDEX idx_consents_latest ON parties.consents (tenant_id, party_id, purpose, at DESC);
CALL core.secure_table('parties.consents', 'parties.contacts');

CREATE TABLE parties.merge_candidates (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 party_id UUID NOT NULL,
 other_party_id UUID NOT NULL,
 reason TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'merged', 'dismissed')),
 decided_by UUID,
 decided_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id, party_id) REFERENCES parties.parties (tenant_id, id),
 FOREIGN KEY (tenant_id, other_party_id) REFERENCES parties.parties (tenant_id, id),
 CHECK (party_id < other_party_id),
 UNIQUE (tenant_id, party_id, other_party_id)
);
CALL core.secure_table('parties.merge_candidates', 'parties.contacts');

GRANT SELECT, INSERT, UPDATE, DELETE ON parties.identifiers TO barista_app;
GRANT SELECT, INSERT, UPDATE ON parties.relations, parties.merge_candidates TO barista_app;
GRANT SELECT, INSERT ON parties.consents TO barista_app;   -- chỉ thêm: rút đồng ý là một dòng mới
