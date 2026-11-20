-- module: leadflow
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (ngành — sales).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA leadflow;

CREATE TABLE leadflow.external_refs (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    provider text NOT NULL,
    ref text NOT NULL,
    lead_id uuid NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT external_refs_provider_check CHECK ((btrim(provider) <> ''::text)),
    CONSTRAINT external_refs_ref_check CHECK ((btrim(ref) <> ''::text))
);

ALTER TABLE ONLY leadflow.external_refs FORCE ROW LEVEL SECURITY;

CREATE TABLE leadflow.lead_state (
    lead_id uuid NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    project_code text DEFAULT ''::text NOT NULL,
    status text NOT NULL,
    holder_id uuid,
    holders uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    rule_id uuid,
    rule jsonb DEFAULT '{}'::jsonb NOT NULL,
    assigned_at timestamp with time zone,
    first_call_due timestamp with time zone,
    first_call_at timestamp with time zone,
    first_call_ok boolean,
    recalls integer DEFAULT 0 NOT NULL,
    followup_due timestamp with time zone,
    followup_flagged timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source text DEFAULT ''::text NOT NULL,
    queue_reason text DEFAULT ''::text NOT NULL,
    CONSTRAINT lead_state_status_check CHECK ((status = ANY (ARRAY['waiting'::text, 'queued'::text, 'contacted'::text, 'closed'::text])))
);

ALTER TABLE ONLY leadflow.lead_state FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE leadflow.lead_state IS 'parent=crm.leads';

CREATE TABLE leadflow.receivers (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    user_id uuid NOT NULL,
    paused_until timestamp with time zone,
    note text DEFAULT ''::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY leadflow.receivers FORCE ROW LEVEL SECURITY;

CREATE TABLE leadflow.rules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    name text NOT NULL,
    project_code text DEFAULT ''::text NOT NULL,
    effective_from date NOT NULL,
    team_id uuid,
    roles text[] DEFAULT ARRAY['sale'::text] NOT NULL,
    strategy text DEFAULT 'rotate'::text NOT NULL,
    cap_uncalled integer DEFAULT 0 NOT NULL,
    first_call_min integer DEFAULT 15 NOT NULL,
    followup_hours integer DEFAULT 48 NOT NULL,
    max_recalls integer DEFAULT 2 NOT NULL,
    work_days smallint[] DEFAULT ARRAY[(1)::smallint, (2)::smallint, (3)::smallint, (4)::smallint, (5)::smallint, (6)::smallint] NOT NULL,
    work_open_min integer DEFAULT 510 NOT NULL,
    work_close_min integer DEFAULT 1080 NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source text DEFAULT ''::text NOT NULL,
    mode text DEFAULT 'auto'::text NOT NULL,
    coord_team_id uuid,
    CONSTRAINT rules_cap_uncalled_check CHECK ((cap_uncalled >= 0)),
    CONSTRAINT rules_check CHECK ((work_open_min < work_close_min)),
    CONSTRAINT rules_coord_team CHECK (((mode = 'auto'::text) OR (coord_team_id IS NOT NULL))),
    CONSTRAINT rules_first_call_min_check CHECK (((first_call_min >= 1) AND (first_call_min <= 10080))),
    CONSTRAINT rules_followup_hours_check CHECK (((followup_hours >= 1) AND (followup_hours <= 2160))),
    CONSTRAINT rules_max_recalls_check CHECK (((max_recalls >= 0) AND (max_recalls <= 10))),
    CONSTRAINT rules_mode_check CHECK ((mode = ANY (ARRAY['auto'::text, 'coordinator'::text]))),
    CONSTRAINT rules_name_check CHECK ((btrim(name) <> ''::text)),
    CONSTRAINT rules_roles_check CHECK ((cardinality(roles) > 0)),
    CONSTRAINT rules_source_check CHECK ((source = ANY (ARRAY[''::text, 'ads'::text, 'telesales'::text, 'self'::text, 'referral'::text, 'event'::text, 'import'::text]))),
    CONSTRAINT rules_strategy_check CHECK ((strategy = ANY (ARRAY['rotate'::text, 'least_loaded'::text]))),
    CONSTRAINT rules_work_close_min_check CHECK (((work_close_min >= 1) AND (work_close_min <= 1440))),
    CONSTRAINT rules_work_open_min_check CHECK (((work_open_min >= 0) AND (work_open_min <= 1439)))
);

ALTER TABLE ONLY leadflow.rules FORCE ROW LEVEL SECURITY;

CREATE TABLE leadflow.sla_events (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    lead_id uuid NOT NULL,
    kind text NOT NULL,
    user_id uuid,
    at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    data jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT sla_events_kind_check CHECK ((kind = ANY (ARRAY['assigned'::text, 'queued'::text, 'first_call_met'::text, 'first_call_breached'::text, 'recalled'::text, 'followup_breached'::text])))
);

ALTER TABLE ONLY leadflow.sla_events FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE leadflow.sla_events IS 'parent=crm.leads';

CREATE SEQUENCE leadflow.sla_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE leadflow.sla_events_id_seq OWNED BY leadflow.sla_events.id;

ALTER TABLE ONLY leadflow.sla_events ALTER COLUMN id SET DEFAULT nextval('leadflow.sla_events_id_seq'::regclass);

ALTER TABLE ONLY leadflow.external_refs
    ADD CONSTRAINT external_refs_pkey PRIMARY KEY (tenant_id, provider, ref);

ALTER TABLE ONLY leadflow.lead_state
    ADD CONSTRAINT lead_state_pkey PRIMARY KEY (lead_id);

ALTER TABLE ONLY leadflow.receivers
    ADD CONSTRAINT receivers_pkey PRIMARY KEY (tenant_id, user_id);

ALTER TABLE ONLY leadflow.rules
    ADD CONSTRAINT rules_pkey PRIMARY KEY (id);

ALTER TABLE ONLY leadflow.rules
    ADD CONSTRAINT rules_scope_day UNIQUE (tenant_id, project_code, source, effective_from);

ALTER TABLE ONLY leadflow.sla_events
    ADD CONSTRAINT sla_events_pkey PRIMARY KEY (id);

CREATE INDEX idx_lead_state_open ON leadflow.lead_state USING btree (tenant_id, status, first_call_due) WHERE (status = ANY (ARRAY['waiting'::text, 'queued'::text, 'contacted'::text]));

CREATE INDEX idx_sla_events_kind ON leadflow.sla_events USING btree (tenant_id, kind, at DESC);

CREATE INDEX idx_sla_events_lead ON leadflow.sla_events USING btree (lead_id, at);

ALTER TABLE ONLY leadflow.external_refs
    ADD CONSTRAINT external_refs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY leadflow.lead_state
    ADD CONSTRAINT lead_state_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY leadflow.receivers
    ADD CONSTRAINT receivers_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY leadflow.rules
    ADD CONSTRAINT rules_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY leadflow.sla_events
    ADD CONSTRAINT sla_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE leadflow.external_refs ENABLE ROW LEVEL SECURITY;

ALTER TABLE leadflow.lead_state ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_no_link ON leadflow.external_refs AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON leadflow.receivers AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON leadflow.rules AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_scope ON leadflow.lead_state USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM crm.leads p
  WHERE (p.id = lead_state.lead_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM crm.leads p
  WHERE (p.id = lead_state.lead_id)))));

CREATE POLICY p_scope ON leadflow.sla_events USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM crm.leads p
  WHERE (p.id = sla_events.lead_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM crm.leads p
  WHERE (p.id = sla_events.lead_id)))));

CREATE POLICY p_tenant ON leadflow.external_refs USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON leadflow.receivers USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON leadflow.rules USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

ALTER TABLE leadflow.receivers ENABLE ROW LEVEL SECURITY;

ALTER TABLE leadflow.rules ENABLE ROW LEVEL SECURITY;

ALTER TABLE leadflow.sla_events ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA leadflow TO barista_app;

GRANT SELECT,INSERT ON TABLE leadflow.external_refs TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE leadflow.lead_state TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE leadflow.receivers TO barista_app;

GRANT SELECT,INSERT ON TABLE leadflow.rules TO barista_app;

GRANT SELECT,INSERT ON TABLE leadflow.sla_events TO barista_app;

GRANT USAGE ON SEQUENCE leadflow.sla_events_id_seq TO barista_app;
