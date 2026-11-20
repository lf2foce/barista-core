-- module: parties, core — lý do: tạo identity và bật capability phụ thuộc/cấp quyền danh mục cho vai hiện hữu
-- 0104: identity is independent of login and industry; no personnel/payroll data here.
CREATE SCHEMA parties;
CREATE TABLE parties.parties (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 kind TEXT NOT NULL CHECK (kind IN ('person', 'organization')),
 display_name TEXT NOT NULL CHECK (btrim(display_name) <> ''),
 user_id UUID,
 active BOOLEAN NOT NULL DEFAULT true,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id), UNIQUE (tenant_id, user_id)
);
CALL core.secure_table('parties.parties', 'parties', 'user_id');
GRANT USAGE ON SCHEMA parties TO barista_app;
GRANT SELECT, INSERT, UPDATE ON parties.parties TO barista_app;
INSERT INTO parties.parties (tenant_id, kind, display_name, user_id)
SELECT m.tenant_id, 'person', u.name, u.id FROM core.users u
JOIN core.memberships m ON m.user_id = u.id;

INSERT INTO core.tenant_packs(tenant_id,pack)
SELECT tenant_id,'directory' FROM core.tenant_packs WHERE pack='finance' ON CONFLICT DO NOTHING;
INSERT INTO core.grants(tenant_id,role,resource,scope,actions)
SELECT t.id,r.key,'parties','all',ARRAY['read','write','manage'] FROM core.tenants t
JOIN core.roles r ON r.tenant_id=t.id WHERE r.key IN ('admin','accountant')
ON CONFLICT DO NOTHING;

-- Historical beneficiaries may no longer be members; preserve their identity without restoring login access.
INSERT INTO parties.parties(tenant_id,kind,display_name,user_id,active)
SELECT DISTINCT a.tenant_id,'person',u.name,u.id,false FROM ledger.accounts a JOIN core.users u ON u.id=a.owner_user_id
WHERE a.code LIKE 'wallet:%' AND NOT EXISTS(SELECT 1 FROM parties.parties p WHERE p.tenant_id=a.tenant_id AND p.user_id=u.id);
