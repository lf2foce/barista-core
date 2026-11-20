-- module: mkt
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (ngành — marketing).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA mkt;

CREATE TABLE mkt.campaigns (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    channel text NOT NULL,
    project_id uuid,
    project_code text DEFAULT ''::text NOT NULL,
    owner_id uuid NOT NULL,
    team_id uuid,
    budget_vnd bigint DEFAULT 0 NOT NULL,
    start_on date NOT NULL,
    end_on date,
    status text DEFAULT 'running'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT campaigns_budget_vnd_check CHECK ((budget_vnd >= 0)),
    CONSTRAINT campaigns_channel_check CHECK ((channel = ANY (ARRAY['facebook'::text, 'google'::text, 'tiktok'::text, 'zalo'::text, 'event'::text, 'telesales'::text, 'other'::text]))),
    CONSTRAINT campaigns_check CHECK (((end_on IS NULL) OR (end_on >= start_on))),
    CONSTRAINT campaigns_code_check CHECK ((code ~ '^[A-Za-z0-9_-]{2,30}$'::text)),
    CONSTRAINT campaigns_name_check CHECK ((btrim(name) <> ''::text)),
    CONSTRAINT campaigns_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'running'::text, 'paused'::text, 'ended'::text])))
);

ALTER TABLE ONLY mkt.campaigns FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE mkt.campaigns IS 'resource=mkt';

CREATE TABLE mkt.coverage (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    spend_reliable_from date,
    lead_reliable_from date,
    spend_complete_through date,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY mkt.coverage FORCE ROW LEVEL SECURITY;

CREATE TABLE mkt.spend (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    campaign_id uuid NOT NULL,
    day date NOT NULL,
    amount_vnd bigint NOT NULL,
    leads_platform integer DEFAULT 0 NOT NULL,
    CONSTRAINT spend_amount_vnd_check CHECK ((amount_vnd >= 0)),
    CONSTRAINT spend_leads_platform_check CHECK ((leads_platform >= 0))
);

ALTER TABLE ONLY mkt.spend FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE mkt.spend IS 'parent=mkt.campaigns';

ALTER TABLE ONLY mkt.campaigns
    ADD CONSTRAINT campaigns_pkey PRIMARY KEY (id);

ALTER TABLE ONLY mkt.campaigns
    ADD CONSTRAINT campaigns_tenant_id_code_key UNIQUE (tenant_id, code);

ALTER TABLE ONLY mkt.coverage
    ADD CONSTRAINT coverage_pkey PRIMARY KEY (tenant_id);

ALTER TABLE ONLY mkt.spend
    ADD CONSTRAINT spend_campaign_id_day_key UNIQUE (campaign_id, day);

ALTER TABLE ONLY mkt.spend
    ADD CONSTRAINT spend_pkey PRIMARY KEY (id);

ALTER TABLE ONLY mkt.campaigns
    ADD CONSTRAINT campaigns_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES core.users(id);

ALTER TABLE ONLY mkt.campaigns
    ADD CONSTRAINT campaigns_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY mkt.campaigns
    ADD CONSTRAINT campaigns_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY mkt.coverage
    ADD CONSTRAINT coverage_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY mkt.spend
    ADD CONSTRAINT spend_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES mkt.campaigns(id) ON DELETE CASCADE;

ALTER TABLE ONLY mkt.spend
    ADD CONSTRAINT spend_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE mkt.campaigns ENABLE ROW LEVEL SECURITY;

ALTER TABLE mkt.coverage ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_no_link ON mkt.coverage AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_scope ON mkt.campaigns USING (((tenant_id = core.tenant()) AND core.can_see('mkt'::text, owner_id, team_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('mkt'::text, owner_id, team_id)));

CREATE POLICY p_scope ON mkt.spend USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM mkt.campaigns p
  WHERE (p.id = spend.campaign_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM mkt.campaigns p
  WHERE (p.id = spend.campaign_id)))));

CREATE POLICY p_tenant ON mkt.coverage USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

ALTER TABLE mkt.spend ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA mkt TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE mkt.campaigns TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE mkt.coverage TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE mkt.spend TO barista_app;
