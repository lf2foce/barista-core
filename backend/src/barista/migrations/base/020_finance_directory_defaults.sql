-- module: core
-- 0104/0105: running installs get what the finance + directory packs now declare; deploy only runs migrations, so `install_defaults`
-- never fills these on its own. Adds what is missing; a chain or grant the company already has or removed stays as the company left it.
INSERT INTO core.approval_chains(tenant_id,key,label,steps)
SELECT p.tenant_id,'settlement_payment','Chi nghĩa vụ','[{"kind":"role","role":"director"}]'::jsonb
FROM core.tenant_packs p WHERE p.pack='finance'
  AND NOT EXISTS(SELECT 1 FROM core.pack_installs i WHERE i.tenant_id=p.tenant_id AND i.kind='chain' AND i.key='settlement_payment')
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs(tenant_id,kind,key,pack,version)
SELECT tenant_id,'chain','settlement_payment','finance',NULL FROM core.tenant_packs WHERE pack='finance' ON CONFLICT DO NOTHING;

INSERT INTO core.grants(tenant_id,role,resource,scope,actions)
SELECT p.tenant_id,'director','parties','all',ARRAY['read'] FROM core.tenant_packs p
JOIN core.roles r ON r.tenant_id=p.tenant_id AND r.key='director' WHERE p.pack='directory'
  AND NOT EXISTS(SELECT 1 FROM core.pack_installs i WHERE i.tenant_id=p.tenant_id AND i.kind='grant' AND i.key='director|parties')
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs(tenant_id,kind,key,pack,version)
SELECT p.tenant_id,'grant','director|parties','directory',NULL FROM core.tenant_packs p
JOIN core.roles r ON r.tenant_id=p.tenant_id AND r.key='director' WHERE p.pack='directory' ON CONFLICT DO NOTHING;

-- Finance requires directory: a company entitled to finance but not yet using it must be able to enable the closure (016 only handled enabled packs).
INSERT INTO core.tenant_entitlements(tenant_id,pack,granted_by,note)
SELECT tenant_id,'directory','migration 020','Tài chính bắt buộc danh mục cá nhân & tổ chức' FROM core.tenant_entitlements WHERE pack='finance'
ON CONFLICT DO NOTHING;
