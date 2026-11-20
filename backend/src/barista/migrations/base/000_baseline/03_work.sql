-- module: work
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (nền).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA work;

CREATE TABLE work.events (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    item_id uuid NOT NULL,
    at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    kind text NOT NULL,
    text text NOT NULL,
    data jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT events_kind_check CHECK ((kind = ANY (ARRAY['created'::text, 'check'::text, 'woke'::text, 'exception'::text, 'human'::text, 'done'::text, 'failed'::text, 'cancelled'::text])))
);

ALTER TABLE ONLY work.events FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE work.events IS 'parent=work.items';

CREATE SEQUENCE work.events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE work.events_id_seq OWNED BY work.events.id;

CREATE TABLE work.items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    owner_id uuid NOT NULL,
    team_id uuid,
    subject_ref text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    due_on date,
    check_every_s integer DEFAULT 86400 NOT NULL,
    next_check_at timestamp with time zone DEFAULT now() NOT NULL,
    agent_runs integer DEFAULT 0 NOT NULL,
    max_agent_runs integer DEFAULT 10 NOT NULL,
    last_note text DEFAULT ''::text NOT NULL,
    facts jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    CONSTRAINT items_check_every_s_check CHECK ((check_every_s >= 10)),
    CONSTRAINT items_kind_check CHECK ((kind ~ '^[a-z_]+$'::text)),
    CONSTRAINT items_status_check CHECK ((status = ANY (ARRAY['active'::text, 'waiting'::text, 'done'::text, 'failed'::text, 'cancelled'::text])))
);

ALTER TABLE ONLY work.items FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE work.items IS 'resource=work';

ALTER TABLE ONLY work.events ALTER COLUMN id SET DEFAULT nextval('work.events_id_seq'::regclass);

ALTER TABLE ONLY work.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY work.items
    ADD CONSTRAINT items_pkey PRIMARY KEY (id);

CREATE INDEX idx_work_events_item ON work.events USING btree (item_id, at);

CREATE INDEX idx_work_owner ON work.items USING btree (tenant_id, owner_id, status);

CREATE UNIQUE INDEX uq_work_open ON work.items USING btree (tenant_id, kind, subject_ref) WHERE (status = ANY (ARRAY['active'::text, 'waiting'::text]));

ALTER TABLE ONLY work.events
    ADD CONSTRAINT events_item_id_fkey FOREIGN KEY (item_id) REFERENCES work.items(id) ON DELETE CASCADE;

ALTER TABLE ONLY work.events
    ADD CONSTRAINT events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY work.items
    ADD CONSTRAINT items_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY work.items
    ADD CONSTRAINT items_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES core.users(id);

ALTER TABLE ONLY work.items
    ADD CONSTRAINT items_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY work.items
    ADD CONSTRAINT items_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE work.events ENABLE ROW LEVEL SECURITY;

ALTER TABLE work.items ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_scope ON work.events USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM work.items p
  WHERE (p.id = events.item_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM work.items p
  WHERE (p.id = events.item_id)))));

CREATE POLICY p_scope ON work.items USING (((tenant_id = core.tenant()) AND core.can_see('work'::text, owner_id, team_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('work'::text, owner_id, team_id)));

GRANT USAGE ON SCHEMA work TO barista_app;

GRANT SELECT,INSERT ON TABLE work.events TO barista_app;

GRANT USAGE ON SEQUENCE work.events_id_seq TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE work.items TO barista_app;
