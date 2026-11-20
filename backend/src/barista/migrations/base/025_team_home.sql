-- module: core
-- 0062 bước sau, 0109: một người làm ở nhiều đội / cơ sở (giáo viên dạy hai cơ sở). Mỗi người tối đa một đội CHÍNH; các đội khác là tham gia.
ALTER TABLE core.team_members ADD COLUMN is_home BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE core.team_members DROP CONSTRAINT team_members_pkey;
ALTER TABLE core.team_members ADD CONSTRAINT team_members_pkey PRIMARY KEY (tenant_id, user_id, team_id);
CREATE UNIQUE INDEX uq_team_members_home ON core.team_members (tenant_id, user_id) WHERE is_home;
