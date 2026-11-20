-- module: crm, parties, flow, core — lý do: định danh người (0111) nối khách CRM; trigger trên crm.customers bảo đảm mọi đường chèn đều có định danh (cùng cách 021); chuyển crm.lead và bật gói crm / directory cho công ty đang có sales (0110). Tách từ directory/024_crm_capability_identity.sql (0130, ghi chú 08/11; bảng định danh ở directory/024_party_identity.sql). deployment: maintenance
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
