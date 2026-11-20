-- module: papers
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (nền).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA papers;

CREATE TABLE papers.documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    kind text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    year integer NOT NULL,
    number integer,
    symbol text DEFAULT ''::text NOT NULL,
    title text NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    recipient text DEFAULT ''::text NOT NULL,
    content jsonb DEFAULT '{}'::jsonb NOT NULL,
    ref text DEFAULT ''::text NOT NULL,
    created_by uuid,
    team_id uuid,
    issued_by uuid,
    issued_on date,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    kind_label text DEFAULT ''::text NOT NULL,
    abbr text DEFAULT ''::text NOT NULL,
    layout text DEFAULT 'official'::text NOT NULL,
    CONSTRAINT documents_kind_check CHECK ((kind ~ '^[a-z_]+$'::text)),
    CONSTRAINT documents_layout_check CHECK ((layout = ANY (ARRAY['official'::text, 'handout'::text]))),
    CONSTRAINT documents_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'issued'::text, 'void'::text])))
);

ALTER TABLE ONLY papers.documents FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE papers.documents IS 'resource=papers';

CREATE TABLE papers.settings (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    company_name text DEFAULT ''::text NOT NULL,
    short_name text DEFAULT ''::text NOT NULL,
    address text DEFAULT ''::text NOT NULL,
    tax_code text DEFAULT ''::text NOT NULL,
    phone text DEFAULT ''::text NOT NULL,
    bank_account text DEFAULT ''::text NOT NULL,
    bank_name text DEFAULT ''::text NOT NULL,
    city text DEFAULT 'Hà Nội'::text NOT NULL,
    signer_title text DEFAULT 'GIÁM ĐỐC'::text NOT NULL,
    signer_name text DEFAULT ''::text NOT NULL,
    CONSTRAINT settings_short_name_check CHECK ((short_name ~ '^[A-ZĐ0-9]{0,10}$'::text))
);

ALTER TABLE ONLY papers.settings FORCE ROW LEVEL SECURITY;

ALTER TABLE ONLY papers.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY papers.documents
    ADD CONSTRAINT documents_tenant_id_kind_year_number_key UNIQUE (tenant_id, kind, year, number);

ALTER TABLE ONLY papers.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (tenant_id);

ALTER TABLE ONLY papers.documents
    ADD CONSTRAINT documents_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY papers.documents
    ADD CONSTRAINT documents_issued_by_fkey FOREIGN KEY (issued_by) REFERENCES core.users(id);

ALTER TABLE ONLY papers.documents
    ADD CONSTRAINT documents_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY papers.documents
    ADD CONSTRAINT documents_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY papers.settings
    ADD CONSTRAINT settings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE papers.documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_no_link ON papers.settings AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_scope ON papers.documents USING (((tenant_id = core.tenant()) AND core.can_see('papers'::text, created_by, team_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('papers'::text, created_by, team_id)));

CREATE POLICY p_tenant ON papers.settings USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

ALTER TABLE papers.settings ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA papers TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE papers.documents TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE papers.settings TO barista_app;
