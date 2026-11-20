-- module: flow
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (nền).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA flow;

CREATE FUNCTION flow.case_on(cid uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$ SELECT core.subject_on((SELECT c.subject_kind FROM flow.cases c WHERE c.id = cid)) $$;

CREATE FUNCTION flow.subject_visible_case(id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $_$ SELECT EXISTS (SELECT 1 FROM flow.cases c WHERE c.id = $1) $_$;

CREATE FUNCTION flow.subject_visible_request(id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $_$ SELECT EXISTS (SELECT 1 FROM flow.requests r WHERE r.id = $1) $_$;

CREATE TABLE flow.case_events (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    case_id uuid NOT NULL,
    kind text NOT NULL,
    at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    by_user uuid,
    data jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT case_events_kind_check CHECK ((kind = ANY (ARRAY['opened'::text, 'item'::text, 'stage'::text, 'returned'::text, 'closed'::text, 'cancelled'::text, 'overdue'::text])))
);

ALTER TABLE ONLY flow.case_events FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE flow.case_events IS 'parent=flow.cases';

CREATE SEQUENCE flow.case_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE flow.case_events_id_seq OWNED BY flow.case_events.id;

CREATE TABLE flow.case_items (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    case_id uuid NOT NULL,
    item_key text NOT NULL,
    status text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    by_user uuid,
    at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT case_items_status_check CHECK ((status = ANY (ARRAY['received'::text, 'verified'::text, 'rejected'::text])))
);

ALTER TABLE ONLY flow.case_items FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE flow.case_items IS 'parent=flow.cases';

CREATE TABLE flow.cases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    template_id uuid NOT NULL,
    subject_kind text DEFAULT ''::text NOT NULL,
    subject_id uuid,
    title text NOT NULL,
    stage_key text,
    status text DEFAULT 'open'::text NOT NULL,
    owner_id uuid,
    team_id uuid,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    stage_since timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    CONSTRAINT cases_status_check CHECK ((status = ANY (ARRAY['open'::text, 'done'::text, 'cancelled'::text]))),
    CONSTRAINT cases_title_check CHECK ((btrim(title) <> ''::text))
);

ALTER TABLE ONLY flow.cases FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE flow.cases IS 'resource=flow';

CREATE TABLE flow.plans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    title text NOT NULL,
    goal text DEFAULT ''::text NOT NULL,
    owner_id uuid,
    team_id uuid,
    due_on date,
    status text DEFAULT 'open'::text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    closed_note text DEFAULT ''::text NOT NULL,
    goal_metric text,
    goal_target integer,
    goal_context text DEFAULT ''::text CONSTRAINT plans_goal_project_not_null NOT NULL,
    goal_value integer,
    goal_measured_at timestamp with time zone,
    goal_alerted_on date,
    CONSTRAINT plan_goal_pair CHECK (((goal_metric IS NULL) = (goal_target IS NULL))),
    CONSTRAINT plans_goal_target_check CHECK ((goal_target > 0)),
    CONSTRAINT plans_status_check CHECK ((status = ANY (ARRAY['open'::text, 'done'::text, 'cancelled'::text]))),
    CONSTRAINT plans_title_check CHECK ((btrim(title) <> ''::text))
);

ALTER TABLE ONLY flow.plans FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE flow.plans IS 'resource=flow';

CREATE TABLE flow.requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    kind text NOT NULL,
    plan_id uuid,
    title text NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    requested_by uuid NOT NULL,
    to_team uuid,
    due_on date,
    status text DEFAULT 'open'::text NOT NULL,
    task_id uuid,
    decided_by uuid,
    decided_at timestamp with time zone,
    decision_note text DEFAULT ''::text NOT NULL,
    budget_vnd bigint,
    vendor_name text DEFAULT ''::text NOT NULL,
    escalated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT requests_budget_vnd_check CHECK ((budget_vnd > 0)),
    CONSTRAINT requests_check CHECK (((kind = 'team'::text) = (to_team IS NOT NULL))),
    CONSTRAINT requests_check1 CHECK (((kind = 'team'::text) OR (budget_vnd IS NOT NULL))),
    CONSTRAINT requests_kind_check CHECK ((kind = ANY (ARRAY['team'::text, 'vendor'::text]))),
    CONSTRAINT requests_status_check CHECK ((status = ANY (ARRAY['open'::text, 'accepted'::text, 'declined'::text]))),
    CONSTRAINT requests_title_check CHECK ((btrim(title) <> ''::text))
);

ALTER TABLE ONLY flow.requests FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE flow.requests IS 'resource=flow';

CREATE TABLE flow.tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    title text NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'todo'::text NOT NULL,
    assignee_id uuid,
    team_id uuid,
    due_on date,
    case_id uuid,
    stage_key text,
    origin text DEFAULT 'manual'::text NOT NULL,
    blocked_reason text DEFAULT ''::text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    done_at timestamp with time zone,
    plan_id uuid,
    CONSTRAINT tasks_check CHECK (((assignee_id IS NOT NULL) OR (team_id IS NOT NULL))),
    CONSTRAINT tasks_check1 CHECK (((status = 'done'::text) = (done_at IS NOT NULL))),
    CONSTRAINT tasks_origin_check CHECK ((origin = ANY (ARRAY['manual'::text, 'stage'::text, 'returned'::text, 'assistant'::text, 'overdue'::text]))),
    CONSTRAINT tasks_status_check CHECK ((status = ANY (ARRAY['todo'::text, 'doing'::text, 'blocked'::text, 'done'::text, 'cancelled'::text]))),
    CONSTRAINT tasks_title_check CHECK ((btrim(title) <> ''::text))
);

ALTER TABLE ONLY flow.tasks FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE flow.tasks IS 'resource=flow';

CREATE TABLE flow.templates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    key text NOT NULL,
    version integer NOT NULL,
    name text NOT NULL,
    subject_kind text DEFAULT ''::text NOT NULL,
    auto_open boolean DEFAULT false NOT NULL,
    source text NOT NULL,
    stages jsonb NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT templates_key_check CHECK ((key ~ '^[a-z][a-z0-9_]{1,40}$'::text)),
    CONSTRAINT templates_name_check CHECK ((btrim(name) <> ''::text)),
    CONSTRAINT templates_stages_check CHECK (((jsonb_typeof(stages) = 'array'::text) AND (jsonb_array_length(stages) > 0))),
    CONSTRAINT templates_version_check CHECK ((version >= 1))
);

ALTER TABLE ONLY flow.templates FORCE ROW LEVEL SECURITY;

CREATE TABLE flow.work_calendars (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    team_id uuid,
    calendar text NOT NULL,
    CONSTRAINT work_calendars_calendar_check CHECK ((btrim(calendar) <> ''::text))
);

ALTER TABLE ONLY flow.work_calendars FORCE ROW LEVEL SECURITY;

ALTER TABLE ONLY flow.case_events ALTER COLUMN id SET DEFAULT nextval('flow.case_events_id_seq'::regclass);

ALTER TABLE ONLY flow.case_events
    ADD CONSTRAINT case_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY flow.case_items
    ADD CONSTRAINT case_items_pkey PRIMARY KEY (case_id, item_key);

ALTER TABLE ONLY flow.cases
    ADD CONSTRAINT cases_pkey PRIMARY KEY (id);

ALTER TABLE ONLY flow.plans
    ADD CONSTRAINT plans_pkey PRIMARY KEY (id);

ALTER TABLE ONLY flow.requests
    ADD CONSTRAINT requests_pkey PRIMARY KEY (id);

ALTER TABLE ONLY flow.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY flow.templates
    ADD CONSTRAINT templates_pkey PRIMARY KEY (id);

ALTER TABLE ONLY flow.templates
    ADD CONSTRAINT templates_tenant_id_key_version_key UNIQUE (tenant_id, key, version);

ALTER TABLE ONLY flow.work_calendars
    ADD CONSTRAINT work_calendars_pkey PRIMARY KEY (id);

ALTER TABLE ONLY flow.work_calendars
    ADD CONSTRAINT work_calendars_tenant_id_team_id_key UNIQUE NULLS NOT DISTINCT (tenant_id, team_id);

CREATE INDEX idx_flow_case_events_case ON flow.case_events USING btree (case_id, at);

CREATE INDEX idx_flow_cases_board ON flow.cases USING btree (tenant_id, template_id, status, stage_key);

CREATE INDEX idx_flow_cases_subject ON flow.cases USING btree (tenant_id, subject_kind, subject_id);

CREATE INDEX idx_flow_requests_open ON flow.requests USING btree (tenant_id, to_team) WHERE (status = 'open'::text);

CREATE INDEX idx_flow_tasks_case ON flow.tasks USING btree (case_id) WHERE (case_id IS NOT NULL);

CREATE INDEX idx_flow_tasks_mine ON flow.tasks USING btree (tenant_id, assignee_id, status, due_on);

CREATE INDEX idx_flow_tasks_plan ON flow.tasks USING btree (plan_id) WHERE (plan_id IS NOT NULL);

CREATE INDEX idx_flow_tasks_team ON flow.tasks USING btree (tenant_id, team_id, status);

CREATE UNIQUE INDEX uq_flow_cases_subject ON flow.cases USING btree (tenant_id, template_id, subject_kind, subject_id) WHERE (subject_id IS NOT NULL);

CREATE UNIQUE INDEX uq_flow_tasks_overdue ON flow.tasks USING btree (case_id, stage_key) WHERE ((origin = 'overdue'::text) AND (status = ANY (ARRAY['todo'::text, 'doing'::text, 'blocked'::text])));

CREATE UNIQUE INDEX uq_flow_templates_active ON flow.templates USING btree (tenant_id, key) WHERE is_active;

CREATE UNIQUE INDEX uq_flow_templates_auto ON flow.templates USING btree (tenant_id, subject_kind) WHERE (is_active AND auto_open);

ALTER TABLE ONLY flow.case_events
    ADD CONSTRAINT case_events_by_user_fkey FOREIGN KEY (by_user) REFERENCES core.users(id);

ALTER TABLE ONLY flow.case_events
    ADD CONSTRAINT case_events_case_id_fkey FOREIGN KEY (case_id) REFERENCES flow.cases(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.case_events
    ADD CONSTRAINT case_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.case_items
    ADD CONSTRAINT case_items_by_user_fkey FOREIGN KEY (by_user) REFERENCES core.users(id);

ALTER TABLE ONLY flow.case_items
    ADD CONSTRAINT case_items_case_id_fkey FOREIGN KEY (case_id) REFERENCES flow.cases(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.case_items
    ADD CONSTRAINT case_items_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.cases
    ADD CONSTRAINT cases_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES core.users(id);

ALTER TABLE ONLY flow.cases
    ADD CONSTRAINT cases_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY flow.cases
    ADD CONSTRAINT cases_template_id_fkey FOREIGN KEY (template_id) REFERENCES flow.templates(id) ON DELETE RESTRICT;

ALTER TABLE ONLY flow.cases
    ADD CONSTRAINT cases_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.plans
    ADD CONSTRAINT plans_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY flow.plans
    ADD CONSTRAINT plans_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES core.users(id);

ALTER TABLE ONLY flow.plans
    ADD CONSTRAINT plans_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY flow.plans
    ADD CONSTRAINT plans_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.requests
    ADD CONSTRAINT requests_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES core.users(id);

ALTER TABLE ONLY flow.requests
    ADD CONSTRAINT requests_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES flow.plans(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.requests
    ADD CONSTRAINT requests_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES core.users(id);

ALTER TABLE ONLY flow.requests
    ADD CONSTRAINT requests_task_id_fkey FOREIGN KEY (task_id) REFERENCES flow.tasks(id) ON DELETE SET NULL;

ALTER TABLE ONLY flow.requests
    ADD CONSTRAINT requests_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.requests
    ADD CONSTRAINT requests_to_team_fkey FOREIGN KEY (to_team) REFERENCES core.teams(id);

ALTER TABLE ONLY flow.tasks
    ADD CONSTRAINT tasks_assignee_id_fkey FOREIGN KEY (assignee_id) REFERENCES core.users(id);

ALTER TABLE ONLY flow.tasks
    ADD CONSTRAINT tasks_case_id_fkey FOREIGN KEY (case_id) REFERENCES flow.cases(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.tasks
    ADD CONSTRAINT tasks_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY flow.tasks
    ADD CONSTRAINT tasks_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES flow.plans(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.tasks
    ADD CONSTRAINT tasks_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY flow.tasks
    ADD CONSTRAINT tasks_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.templates
    ADD CONSTRAINT templates_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY flow.templates
    ADD CONSTRAINT templates_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.work_calendars
    ADD CONSTRAINT work_calendars_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id) ON DELETE CASCADE;

ALTER TABLE ONLY flow.work_calendars
    ADD CONSTRAINT work_calendars_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE flow.case_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE flow.case_items ENABLE ROW LEVEL SECURITY;

ALTER TABLE flow.cases ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_no_link ON flow.templates AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON flow.work_calendars AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_pack_on ON flow.cases AS RESTRICTIVE USING (core.subject_on(subject_kind));

CREATE POLICY p_pack_on ON flow.tasks AS RESTRICTIVE USING (((case_id IS NULL) OR flow.case_on(case_id)));

CREATE POLICY p_scope ON flow.case_events USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM flow.cases p
  WHERE (p.id = case_events.case_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM flow.cases p
  WHERE (p.id = case_events.case_id)))));

CREATE POLICY p_scope ON flow.case_items USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM flow.cases p
  WHERE (p.id = case_items.case_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM flow.cases p
  WHERE (p.id = case_items.case_id)))));

CREATE POLICY p_scope ON flow.cases USING (((tenant_id = core.tenant()) AND core.can_see('flow'::text, owner_id, team_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('flow'::text, owner_id, team_id)));

CREATE POLICY p_scope ON flow.plans USING (((tenant_id = core.tenant()) AND core.can_see('flow'::text, owner_id, team_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('flow'::text, owner_id, team_id)));

CREATE POLICY p_scope ON flow.requests USING (((tenant_id = core.tenant()) AND core.can_see('flow'::text, requested_by, to_team))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('flow'::text, requested_by, to_team)));

CREATE POLICY p_scope ON flow.tasks USING (((tenant_id = core.tenant()) AND core.can_see('flow'::text, assignee_id, team_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('flow'::text, assignee_id, team_id)));

CREATE POLICY p_team_pool ON flow.tasks FOR SELECT USING (((tenant_id = core.tenant()) AND (core.scope('flow'::text) <> 'none'::text) AND (assignee_id IS NULL) AND (team_id IN ( SELECT tm.team_id
   FROM core.team_members tm
  WHERE (tm.user_id = core.me())))));

CREATE POLICY p_team_take ON flow.tasks FOR UPDATE USING (((tenant_id = core.tenant()) AND (core.scope('flow'::text) <> 'none'::text) AND (assignee_id IS NULL) AND (team_id IN ( SELECT tm.team_id
   FROM core.team_members tm
  WHERE (tm.user_id = core.me()))))) WITH CHECK (((tenant_id = core.tenant()) AND (assignee_id = core.me())));

CREATE POLICY p_tenant ON flow.templates USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON flow.work_calendars USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_via_task ON flow.plans FOR SELECT USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM flow.tasks t
  WHERE (t.plan_id = plans.id)))));

ALTER TABLE flow.plans ENABLE ROW LEVEL SECURITY;

ALTER TABLE flow.requests ENABLE ROW LEVEL SECURITY;

ALTER TABLE flow.tasks ENABLE ROW LEVEL SECURITY;

ALTER TABLE flow.templates ENABLE ROW LEVEL SECURITY;

ALTER TABLE flow.work_calendars ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA flow TO barista_app;

GRANT SELECT,INSERT ON TABLE flow.case_events TO barista_app;

GRANT USAGE ON SEQUENCE flow.case_events_id_seq TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE flow.case_items TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE flow.cases TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE flow.plans TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE flow.requests TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE flow.tasks TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE flow.templates TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE flow.work_calendars TO barista_app;

-- Dữ liệu tham chiếu bắt buộc: loại đối tượng của module
INSERT INTO core.subject_kinds (kind, visible_fn, pack) VALUES ('flow.case', 'flow.subject_visible_case', 'base');
INSERT INTO core.subject_kinds (kind, visible_fn, pack) VALUES ('flow.request', 'flow.subject_visible_request', 'base');
