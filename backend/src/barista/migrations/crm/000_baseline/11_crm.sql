-- module: crm
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (ngành — sales).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA crm;

CREATE FUNCTION crm.subject_visible_lead(id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $_$ SELECT EXISTS (SELECT 1 FROM crm.leads l WHERE l.id = $1) $_$;

CREATE TABLE crm.activities (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    lead_id uuid NOT NULL,
    kind text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    by_user uuid,
    at timestamp with time zone DEFAULT now() NOT NULL,
    outcome text DEFAULT ''::text NOT NULL,
    CONSTRAINT activities_kind_check CHECK ((kind = ANY (ARRAY['call'::text, 'meet'::text, 'zalo'::text, 'email'::text, 'visit'::text, 'send_profile'::text, 'note'::text]))),
    CONSTRAINT activities_outcome_check CHECK ((outcome = ANY (ARRAY[''::text, 'connected'::text, 'no_answer'::text, 'busy'::text, 'wrong_number'::text])))
);

ALTER TABLE ONLY crm.activities FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE crm.activities IS 'parent=crm.leads';

CREATE TABLE crm.appointments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    lead_id uuid NOT NULL,
    kind text NOT NULL,
    at timestamp with time zone NOT NULL,
    place text DEFAULT ''::text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'scheduled'::text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    CONSTRAINT appointments_check CHECK (((status = 'scheduled'::text) = (closed_at IS NULL))),
    CONSTRAINT appointments_kind_check CHECK ((kind = ANY (ARRAY['meet'::text, 'visit'::text, 'call'::text]))),
    CONSTRAINT appointments_status_check CHECK ((status = ANY (ARRAY['scheduled'::text, 'done'::text, 'cancelled'::text, 'missed'::text])))
);

ALTER TABLE ONLY crm.appointments FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE crm.appointments IS 'parent=crm.leads';

CREATE TABLE crm.customers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    name text NOT NULL,
    phone text NOT NULL,
    email text DEFAULT ''::text NOT NULL,
    birthday date,
    owner_id uuid,
    team_id uuid,
    note text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT customers_name_check CHECK ((btrim(name) <> ''::text)),
    CONSTRAINT customers_phone_check CHECK ((phone ~ '^0[0-9]{9,10}$'::text))
);

ALTER TABLE ONLY crm.customers FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE crm.customers IS 'resource=crm';

CREATE TABLE crm.leads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    customer_id uuid NOT NULL,
    project_id uuid,
    project_code text DEFAULT ''::text NOT NULL,
    stage text DEFAULT 'lead'::text NOT NULL,
    source text DEFAULT 'self'::text NOT NULL,
    campaign_id uuid,
    interest jsonb DEFAULT '{}'::jsonb NOT NULL,
    owner_id uuid,
    team_id uuid,
    next_follow_up date,
    last_activity_at timestamp with time zone,
    lost_reason text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    captured_at timestamp with time zone,
    CONSTRAINT leads_source_check CHECK ((source = ANY (ARRAY['ads'::text, 'telesales'::text, 'self'::text, 'referral'::text, 'event'::text, 'import'::text]))),
    CONSTRAINT leads_stage_check CHECK ((stage = ANY (ARRAY['lead'::text, 'prospect'::text, 'deal'::text, 'lost'::text])))
);

ALTER TABLE ONLY crm.leads FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE crm.leads IS 'resource=crm';

CREATE TABLE crm.stage_events (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    lead_id uuid NOT NULL,
    kind text NOT NULL,
    from_stage text,
    to_stage text,
    at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    by_user uuid,
    reason text DEFAULT ''::text NOT NULL,
    data jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT stage_events_kind_check CHECK ((kind = ANY (ARRAY['created'::text, 'stage'::text, 'assigned'::text])))
);

ALTER TABLE ONLY crm.stage_events FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE crm.stage_events IS 'parent=crm.leads';

CREATE SEQUENCE crm.stage_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE crm.stage_events_id_seq OWNED BY crm.stage_events.id;

ALTER TABLE ONLY crm.stage_events ALTER COLUMN id SET DEFAULT nextval('crm.stage_events_id_seq'::regclass);

ALTER TABLE ONLY crm.activities
    ADD CONSTRAINT activities_pkey PRIMARY KEY (id);

ALTER TABLE ONLY crm.appointments
    ADD CONSTRAINT appointments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY crm.customers
    ADD CONSTRAINT customers_pkey PRIMARY KEY (id);

ALTER TABLE ONLY crm.customers
    ADD CONSTRAINT customers_tenant_id_phone_key UNIQUE (tenant_id, phone);

ALTER TABLE ONLY crm.leads
    ADD CONSTRAINT leads_pkey PRIMARY KEY (id);

ALTER TABLE ONLY crm.stage_events
    ADD CONSTRAINT stage_events_pkey PRIMARY KEY (id);

CREATE INDEX idx_activities_lead ON crm.activities USING btree (lead_id, at DESC);

CREATE INDEX idx_appointments_lead ON crm.appointments USING btree (lead_id, at DESC);

CREATE INDEX idx_appointments_open ON crm.appointments USING btree (tenant_id, at) WHERE (status = 'scheduled'::text);

CREATE INDEX idx_leads_campaign ON crm.leads USING btree (campaign_id) WHERE (campaign_id IS NOT NULL);

CREATE INDEX idx_leads_owner ON crm.leads USING btree (tenant_id, owner_id, stage);

CREATE INDEX idx_stage_events_lead ON crm.stage_events USING btree (lead_id, at);

CREATE UNIQUE INDEX uq_leads_customer_project ON crm.leads USING btree (tenant_id, customer_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid));

ALTER TABLE ONLY crm.activities
    ADD CONSTRAINT activities_by_user_fkey FOREIGN KEY (by_user) REFERENCES core.users(id);

ALTER TABLE ONLY crm.activities
    ADD CONSTRAINT activities_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES crm.leads(id) ON DELETE CASCADE;

ALTER TABLE ONLY crm.activities
    ADD CONSTRAINT activities_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY crm.appointments
    ADD CONSTRAINT appointments_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY crm.appointments
    ADD CONSTRAINT appointments_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES crm.leads(id) ON DELETE CASCADE;

ALTER TABLE ONLY crm.appointments
    ADD CONSTRAINT appointments_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY crm.customers
    ADD CONSTRAINT customers_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES core.users(id);

ALTER TABLE ONLY crm.customers
    ADD CONSTRAINT customers_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY crm.customers
    ADD CONSTRAINT customers_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY crm.leads
    ADD CONSTRAINT leads_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customers(id) ON DELETE CASCADE;

ALTER TABLE ONLY crm.leads
    ADD CONSTRAINT leads_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES core.users(id);

ALTER TABLE ONLY crm.leads
    ADD CONSTRAINT leads_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY crm.leads
    ADD CONSTRAINT leads_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY crm.stage_events
    ADD CONSTRAINT stage_events_by_user_fkey FOREIGN KEY (by_user) REFERENCES core.users(id);

ALTER TABLE ONLY crm.stage_events
    ADD CONSTRAINT stage_events_lead_id_fkey FOREIGN KEY (lead_id) REFERENCES crm.leads(id) ON DELETE CASCADE;

ALTER TABLE ONLY crm.stage_events
    ADD CONSTRAINT stage_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE crm.activities ENABLE ROW LEVEL SECURITY;

ALTER TABLE crm.appointments ENABLE ROW LEVEL SECURITY;

ALTER TABLE crm.customers ENABLE ROW LEVEL SECURITY;

ALTER TABLE crm.leads ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_scope ON crm.activities USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM crm.leads p
  WHERE (p.id = activities.lead_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM crm.leads p
  WHERE (p.id = activities.lead_id)))));

CREATE POLICY p_scope ON crm.appointments USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM crm.leads p
  WHERE (p.id = appointments.lead_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM crm.leads p
  WHERE (p.id = appointments.lead_id)))));

CREATE POLICY p_scope ON crm.customers USING (((tenant_id = core.tenant()) AND (core.can_see('crm'::text, owner_id, team_id) OR (EXISTS ( SELECT 1
   FROM crm.leads l
  WHERE (l.customer_id = customers.id)))))) WITH CHECK (((tenant_id = core.tenant()) AND (core.can_see('crm'::text, owner_id, team_id) OR (EXISTS ( SELECT 1
   FROM crm.leads l
  WHERE (l.customer_id = customers.id))))));

CREATE POLICY p_scope ON crm.leads USING (((tenant_id = core.tenant()) AND core.can_see('crm'::text, owner_id, team_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('crm'::text, owner_id, team_id)));

CREATE POLICY p_scope ON crm.stage_events USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM crm.leads p
  WHERE (p.id = stage_events.lead_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM crm.leads p
  WHERE (p.id = stage_events.lead_id)))));

ALTER TABLE crm.stage_events ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA crm TO barista_app;

GRANT SELECT,INSERT ON TABLE crm.activities TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE crm.appointments TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE crm.customers TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE crm.leads TO barista_app;

GRANT SELECT,INSERT ON TABLE crm.stage_events TO barista_app;

GRANT USAGE ON SEQUENCE crm.stage_events_id_seq TO barista_app;

-- Dữ liệu tham chiếu bắt buộc: loại đối tượng của module
INSERT INTO core.subject_kinds (kind, visible_fn, pack) VALUES ('crm.lead', 'crm.subject_visible_lead', 'sales');
