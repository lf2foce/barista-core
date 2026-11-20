-- module: asks
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (nền).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA asks;

CREATE TABLE asks.items (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    request_id uuid NOT NULL,
    seq integer NOT NULL,
    kind text NOT NULL,
    prompt text NOT NULL,
    options text[] DEFAULT '{}'::text[] NOT NULL,
    ref text DEFAULT ''::text NOT NULL,
    answer text DEFAULT ''::text NOT NULL,
    document_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    answered_at timestamp with time zone,
    accepted_by uuid,
    accepted_at timestamp with time zone,
    CONSTRAINT items_check CHECK (((kind <> 'choice'::text) OR ((cardinality(options) >= 2) AND (cardinality(options) <= 8)))),
    CONSTRAINT items_kind_check CHECK ((kind = ANY (ARRAY['file'::text, 'choice'::text, 'text'::text]))),
    CONSTRAINT items_prompt_check CHECK ((btrim(prompt) <> ''::text)),
    CONSTRAINT items_status_check CHECK ((status = ANY (ARRAY['open'::text, 'answered'::text, 'accepted'::text])))
);

ALTER TABLE ONLY asks.items FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE asks.items IS 'parent=asks.requests';

CREATE TABLE asks.requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    subject_kind text NOT NULL,
    subject_id uuid NOT NULL,
    subject_title text DEFAULT ''::text NOT NULL,
    title text NOT NULL,
    recipient text DEFAULT ''::text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    shelf text NOT NULL,
    due_on date,
    status text DEFAULT 'open'::text NOT NULL,
    owner_id uuid,
    reminded_on date,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    team_id uuid,
    CONSTRAINT requests_status_check CHECK ((status = ANY (ARRAY['open'::text, 'closed'::text]))),
    CONSTRAINT requests_title_check CHECK ((btrim(title) <> ''::text))
);

ALTER TABLE ONLY asks.requests FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE asks.requests IS 'resource=asks';

ALTER TABLE ONLY asks.items
    ADD CONSTRAINT items_pkey PRIMARY KEY (id);

ALTER TABLE ONLY asks.items
    ADD CONSTRAINT items_request_id_seq_key UNIQUE (request_id, seq);

ALTER TABLE ONLY asks.requests
    ADD CONSTRAINT requests_pkey PRIMARY KEY (id);

CREATE INDEX idx_asks_due ON asks.requests USING btree (due_on) WHERE (status = 'open'::text);

CREATE INDEX idx_asks_subject ON asks.requests USING btree (tenant_id, subject_kind, subject_id);

ALTER TABLE ONLY asks.items
    ADD CONSTRAINT items_accepted_by_fkey FOREIGN KEY (accepted_by) REFERENCES core.users(id);

ALTER TABLE ONLY asks.items
    ADD CONSTRAINT items_request_id_fkey FOREIGN KEY (request_id) REFERENCES asks.requests(id) ON DELETE CASCADE;

ALTER TABLE ONLY asks.items
    ADD CONSTRAINT items_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY asks.requests
    ADD CONSTRAINT requests_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES core.users(id);

ALTER TABLE ONLY asks.requests
    ADD CONSTRAINT requests_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY asks.requests
    ADD CONSTRAINT requests_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE asks.items ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_link ON asks.requests USING (((tenant_id = core.tenant()) AND (id = core.link_ref()))) WITH CHECK (((tenant_id = core.tenant()) AND (id = core.link_ref())));

CREATE POLICY p_scope ON asks.items USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM asks.requests p
  WHERE (p.id = items.request_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM asks.requests p
  WHERE (p.id = items.request_id)))));

CREATE POLICY p_sender ON asks.requests USING (((tenant_id = core.tenant()) AND ((owner_id = core.me()) OR ((team_id IS NOT NULL) AND (team_id = ANY (core.my_teams())))))) WITH CHECK (((tenant_id = core.tenant()) AND ((owner_id = core.me()) OR ((team_id IS NOT NULL) AND (team_id = ANY (core.my_teams()))))));

CREATE POLICY p_subject ON asks.requests USING (((tenant_id = core.tenant()) AND core.subject_visible(subject_kind, subject_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.subject_visible(subject_kind, subject_id)));

ALTER TABLE asks.requests ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA asks TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE asks.items TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE asks.requests TO barista_app;
