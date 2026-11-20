-- module: parties, crm, flow, core — lý do: định danh người (0111) nối khách CRM; trigger trên crm.customers bảo đảm mọi đường chèn đều có định danh (cùng cách 021); chuyển crm.lead và bật gói crm / directory cho công ty đang có sales (0110)
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

-- Khách đang có: mỗi khách một định danh, không làm ngập danh mục chọn.
ALTER TABLE crm.customers ADD COLUMN party_id UUID, ALTER COLUMN phone DROP NOT NULL;
WITH src AS MATERIALIZED (SELECT id AS customer_id, tenant_id, name, gen_random_uuid() AS party_id FROM crm.customers),
     made AS (INSERT INTO parties.parties (id, tenant_id, kind, display_name, listed)
              SELECT party_id, tenant_id, 'person', name, false FROM src RETURNING id)
UPDATE crm.customers c SET party_id = s.party_id FROM src s WHERE c.id = s.customer_id;
INSERT INTO parties.identifiers (tenant_id, party_id, kind, value, source)
SELECT tenant_id, party_id, 'phone', phone, 'crm' FROM crm.customers WHERE phone IS NOT NULL;
INSERT INTO parties.identifiers (tenant_id, party_id, kind, value, source)
SELECT DISTINCT ON (tenant_id, lower(btrim(email))) tenant_id, party_id, 'email', lower(btrim(email)), 'crm'
FROM crm.customers WHERE btrim(email) <> '' ORDER BY tenant_id, lower(btrim(email)), created_at;
-- Hai khách cùng email: không tự gộp, ghi nghi trùng để người xem.
INSERT INTO parties.merge_candidates (tenant_id, party_id, other_party_id, reason)
SELECT DISTINCT c.tenant_id, least(c.party_id, i.party_id), greatest(c.party_id, i.party_id), 'Cùng email'
FROM crm.customers c JOIN parties.identifiers i ON i.tenant_id = c.tenant_id AND i.kind = 'email' AND i.value = lower(btrim(c.email))
WHERE i.party_id <> c.party_id
ON CONFLICT DO NOTHING;
ALTER TABLE crm.customers ALTER COLUMN party_id SET NOT NULL, ADD CONSTRAINT customers_tenant_party_key UNIQUE (tenant_id, party_id);

CREATE FUNCTION parties.ensure_for_customer() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE found UUID;
BEGIN
 IF NEW.party_id IS NOT NULL THEN RETURN NEW; END IF;
 IF NEW.phone IS NOT NULL THEN
  SELECT party_id INTO found FROM parties.identifiers WHERE tenant_id = NEW.tenant_id AND kind = 'phone' AND value = NEW.phone;
 END IF;
 IF found IS NULL THEN
  INSERT INTO parties.parties (tenant_id, kind, display_name, listed) VALUES (NEW.tenant_id, 'person', NEW.name, false) RETURNING id INTO found;
  IF NEW.phone IS NOT NULL THEN
   INSERT INTO parties.identifiers (tenant_id, party_id, kind, value, source) VALUES (NEW.tenant_id, found, 'phone', NEW.phone, 'crm');
  END IF;
  IF btrim(NEW.email) <> '' THEN
   INSERT INTO parties.identifiers (tenant_id, party_id, kind, value, source) VALUES (NEW.tenant_id, found, 'email', lower(btrim(NEW.email)), 'crm')
   ON CONFLICT DO NOTHING;
  END IF;
 END IF;
 NEW.party_id := found;
 RETURN NEW;
END $$;
CREATE TRIGGER customer_has_party BEFORE INSERT ON crm.customers FOR EACH ROW EXECUTE FUNCTION parties.ensure_for_customer();

-- Công ty chưa có danh mục sản phẩm (id trống): mỗi sản phẩm ghi bằng chữ vẫn là một lead riêng.
DROP INDEX crm.uq_leads_customer_project;
CREATE UNIQUE INDEX uq_leads_customer_project ON crm.leads (tenant_id, customer_id, COALESCE(project_id::text, project_code));

-- Nhu cầu đã ghi: phần thuộc ngành được viết sẵn thành chữ để CRM hiện mà không cần biết từng thuộc tính.
UPDATE crm.leads SET interest = interest || jsonb_build_object('detail', concat_ws(' · ',
    CASE WHEN interest->>'bedrooms' IS NOT NULL THEN (interest->>'bedrooms') || 'PN' END,
    NULLIF(interest->>'direction', ''), NULLIF(interest->>'purpose', '')))
WHERE interest ?| ARRAY['bedrooms', 'direction', 'purpose'];

-- Gói: công ty đang bật / có quyền `sales` nhận `crm` và `directory`; quyền mặc định có sẵn giữ nguyên.
UPDATE core.subject_kinds SET pack = 'crm' WHERE kind = 'crm.lead';
INSERT INTO core.tenant_packs (tenant_id, pack) SELECT tenant_id, 'crm' FROM core.tenant_packs WHERE pack = 'sales' ON CONFLICT DO NOTHING;
INSERT INTO core.tenant_packs (tenant_id, pack) SELECT tenant_id, 'directory' FROM core.tenant_packs WHERE pack = 'sales' ON CONFLICT DO NOTHING;
INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note)
SELECT tenant_id, 'crm', 'migration 024', 'Bán hàng BĐS cần Khách hàng' FROM core.tenant_entitlements WHERE pack = 'sales' ON CONFLICT DO NOTHING;
INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note)
SELECT tenant_id, 'directory', 'migration 024', 'Khách hàng cần danh mục cá nhân & tổ chức' FROM core.tenant_entitlements WHERE pack = 'sales' ON CONFLICT DO NOTHING;
-- Liên hệ & đồng ý là tài nguyên riêng: kế toán chọn bên nhận tiền, không vì thế mà tra được SĐT khách.
INSERT INTO core.grants (tenant_id, role, resource, scope, actions)
SELECT p.tenant_id, r.key, 'parties.contacts', 'all', CASE r.key WHEN 'director' THEN ARRAY['read'] ELSE ARRAY['read', 'write', 'manage'] END
FROM core.tenant_packs p JOIN core.roles r ON r.tenant_id = p.tenant_id AND r.key IN ('admin', 'director')
WHERE p.pack = 'directory' ON CONFLICT DO NOTHING;
INSERT INTO core.grants (tenant_id, role, resource, scope, actions)
SELECT p.tenant_id, r.key, 'parties', 'all', CASE r.key WHEN 'director' THEN ARRAY['read'] ELSE ARRAY['read', 'write', 'manage'] END
FROM core.tenant_packs p JOIN core.roles r ON r.tenant_id = p.tenant_id AND r.key IN ('admin', 'director')
WHERE p.pack = 'directory'
  AND NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = p.tenant_id AND i.kind = 'grant' AND i.key = r.key || '|parties')
ON CONFLICT DO NOTHING;

-- Cò "Có lead mới" đổi nhãn trường (0110): quy tắc tự động đang dùng nhãn cũ vẫn điền đúng.
UPDATE flow.automations SET message = replace(message, '{Dự án}', '{Sản phẩm quan tâm}') WHERE trigger = 'crm.lead_created' AND message LIKE '%{Dự án}%';
