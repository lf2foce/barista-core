-- module: kb
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (nền).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA kb;

CREATE FUNCTION kb.shelf_visible(k text) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT EXISTS (
        SELECT 1 FROM kb.shelves s
        WHERE s.tenant_id = core.tenant() AND s.key = k AND (
            s.everyone
            OR core.me() = ANY (s.user_ids)
            OR EXISTS (SELECT 1 FROM core.memberships mb WHERE mb.tenant_id = core.tenant() AND mb.user_id = core.me() AND mb.active
                         AND mb.role = ANY (s.roles))
            OR EXISTS (
                WITH RECURSIVE up AS (
                    SELECT t.id, t.parent_id FROM core.teams t JOIN core.team_members tm ON tm.team_id = t.id
                    WHERE tm.tenant_id = core.tenant() AND tm.user_id = core.me()
                    UNION
                    SELECT p.id, p.parent_id FROM core.teams p JOIN up ON p.id = up.parent_id)
                SELECT 1 FROM up WHERE up.id = ANY (s.team_ids))))
$$;

CREATE TABLE kb.acl (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    document_id uuid NOT NULL,
    principal_type text NOT NULL,
    principal text NOT NULL,
    CONSTRAINT acl_principal_check CHECK ((principal = lower(principal))),
    CONSTRAINT acl_principal_type_check CHECK ((principal_type = ANY (ARRAY['user'::text, 'group'::text, 'domain'::text])))
);

ALTER TABLE ONLY kb.acl FORCE ROW LEVEL SECURITY;

CREATE TABLE kb.chunks (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    document_id uuid NOT NULL,
    seq integer NOT NULL,
    heading text DEFAULT ''::text NOT NULL,
    text text NOT NULL,
    folded text NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, folded)) STORED,
    page integer,
    CONSTRAINT chunks_page_check CHECK (((page IS NULL) OR (page >= 1)))
);

ALTER TABLE ONLY kb.chunks FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE kb.chunks IS 'parent=kb.documents';

CREATE SEQUENCE kb.chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE kb.chunks_id_seq OWNED BY kb.chunks.id;

CREATE TABLE kb.documents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    title text NOT NULL,
    shelf text DEFAULT 'common'::text NOT NULL,
    kind text DEFAULT 'other'::text NOT NULL,
    context_code text DEFAULT ''::text CONSTRAINT documents_project_code_not_null NOT NULL,
    source text DEFAULT ''::text NOT NULL,
    effective_from date,
    effective_to date,
    body text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_kind text DEFAULT 'internal'::text NOT NULL,
    external_id text,
    synced_at timestamp with time zone,
    supersedes_id uuid,
    suggestion jsonb,
    CONSTRAINT documents_check CHECK (((effective_to IS NULL) OR (effective_from IS NULL) OR (effective_to >= effective_from))),
    CONSTRAINT documents_kind_check CHECK ((kind = ANY (ARRAY['policy'::text, 'project'::text, 'legal'::text, 'process'::text, 'faq'::text, 'script'::text, 'template'::text, 'other'::text]))),
    CONSTRAINT documents_source_kind_check CHECK ((source_kind = ANY (ARRAY['internal'::text, 'gdrive'::text]))),
    CONSTRAINT documents_title_check CHECK ((btrim(title) <> ''::text)),
    CONSTRAINT kb_documents_not_self_superseding CHECK (((supersedes_id IS NULL) OR (supersedes_id <> id)))
);

ALTER TABLE ONLY kb.documents FORCE ROW LEVEL SECURITY;

CREATE TABLE kb.extraction_edits (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    extraction_id uuid NOT NULL,
    field text NOT NULL,
    cells jsonb NOT NULL,
    reason text NOT NULL,
    by_user uuid NOT NULL,
    at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT extraction_edits_reason_check CHECK ((btrim(reason) <> ''::text))
);

ALTER TABLE ONLY kb.extraction_edits FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE kb.extraction_edits IS 'parent=kb.extractions';

CREATE SEQUENCE kb.extraction_edits_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE kb.extraction_edits_id_seq OWNED BY kb.extraction_edits.id;

CREATE TABLE kb.extractions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    document_id uuid NOT NULL,
    doc_type text NOT NULL,
    fields jsonb NOT NULL,
    from_scan boolean NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT extractions_doc_type_check CHECK ((doc_type ~ '^[a-z_]+\.[a-z_]+$'::text))
);

ALTER TABLE ONLY kb.extractions FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE kb.extractions IS 'parent=kb.documents';

CREATE TABLE kb.files (
    document_id uuid NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    filename text NOT NULL,
    content_type text NOT NULL,
    size_bytes integer NOT NULL,
    data bytea NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT files_data_check CHECK ((octet_length(data) <= 31457280)),
    CONSTRAINT files_filename_check CHECK ((btrim(filename) <> ''::text))
);

ALTER TABLE ONLY kb.files FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE kb.files IS 'parent=kb.documents';

CREATE TABLE kb.shelves (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    key text NOT NULL,
    name text NOT NULL,
    everyone boolean DEFAULT false NOT NULL,
    team_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    roles text[] DEFAULT '{}'::text[] NOT NULL,
    user_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT shelves_key_check CHECK ((key ~ '^[a-z0-9_]{1,40}$'::text)),
    CONSTRAINT shelves_name_check CHECK (((btrim(name) <> ''::text) AND (length(name) <= 80)))
);

ALTER TABLE ONLY kb.shelves FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE kb.shelves IS 'resource=kb.common';

ALTER TABLE ONLY kb.chunks ALTER COLUMN id SET DEFAULT nextval('kb.chunks_id_seq'::regclass);

ALTER TABLE ONLY kb.extraction_edits ALTER COLUMN id SET DEFAULT nextval('kb.extraction_edits_id_seq'::regclass);

ALTER TABLE ONLY kb.acl
    ADD CONSTRAINT acl_pkey PRIMARY KEY (document_id, principal_type, principal);

ALTER TABLE ONLY kb.chunks
    ADD CONSTRAINT chunks_document_id_seq_key UNIQUE (document_id, seq);

ALTER TABLE ONLY kb.chunks
    ADD CONSTRAINT chunks_pkey PRIMARY KEY (id);

ALTER TABLE ONLY kb.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);

ALTER TABLE ONLY kb.extraction_edits
    ADD CONSTRAINT extraction_edits_pkey PRIMARY KEY (id);

ALTER TABLE ONLY kb.extractions
    ADD CONSTRAINT extractions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY kb.files
    ADD CONSTRAINT files_pkey PRIMARY KEY (document_id);

ALTER TABLE ONLY kb.shelves
    ADD CONSTRAINT shelves_pkey PRIMARY KEY (id);

ALTER TABLE ONLY kb.shelves
    ADD CONSTRAINT shelves_tenant_id_key_key UNIQUE (tenant_id, key);

CREATE INDEX idx_kb_acl_principal ON kb.acl USING btree (tenant_id, principal_type, principal);

CREATE INDEX idx_kb_chunks_tsv ON kb.chunks USING gin (tsv);

CREATE INDEX idx_kb_extraction_edits ON kb.extraction_edits USING btree (extraction_id, field, id DESC);

CREATE INDEX idx_kb_extractions_doc ON kb.extractions USING btree (document_id, doc_type, created_at DESC);

CREATE UNIQUE INDEX uq_kb_documents_supersedes ON kb.documents USING btree (supersedes_id) WHERE (supersedes_id IS NOT NULL);

CREATE UNIQUE INDEX uq_kb_external ON kb.documents USING btree (tenant_id, source_kind, external_id) WHERE (external_id IS NOT NULL);

ALTER TABLE ONLY kb.acl
    ADD CONSTRAINT acl_document_id_fkey FOREIGN KEY (document_id) REFERENCES kb.documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.acl
    ADD CONSTRAINT acl_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.chunks
    ADD CONSTRAINT chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES kb.documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.chunks
    ADD CONSTRAINT chunks_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.documents
    ADD CONSTRAINT documents_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY kb.documents
    ADD CONSTRAINT documents_shelf_fk FOREIGN KEY (tenant_id, shelf) REFERENCES kb.shelves(tenant_id, key);

ALTER TABLE ONLY kb.documents
    ADD CONSTRAINT documents_supersedes_id_fkey FOREIGN KEY (supersedes_id) REFERENCES kb.documents(id) ON DELETE SET NULL;

ALTER TABLE ONLY kb.documents
    ADD CONSTRAINT documents_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.extraction_edits
    ADD CONSTRAINT extraction_edits_by_user_fkey FOREIGN KEY (by_user) REFERENCES core.users(id);

ALTER TABLE ONLY kb.extraction_edits
    ADD CONSTRAINT extraction_edits_extraction_id_fkey FOREIGN KEY (extraction_id) REFERENCES kb.extractions(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.extraction_edits
    ADD CONSTRAINT extraction_edits_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.extractions
    ADD CONSTRAINT extractions_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY kb.extractions
    ADD CONSTRAINT extractions_document_id_fkey FOREIGN KEY (document_id) REFERENCES kb.documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.extractions
    ADD CONSTRAINT extractions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.files
    ADD CONSTRAINT files_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY kb.files
    ADD CONSTRAINT files_document_id_fkey FOREIGN KEY (document_id) REFERENCES kb.documents(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.files
    ADD CONSTRAINT files_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY kb.shelves
    ADD CONSTRAINT shelves_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY kb.shelves
    ADD CONSTRAINT shelves_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE kb.acl ENABLE ROW LEVEL SECURITY;

ALTER TABLE kb.chunks ENABLE ROW LEVEL SECURITY;

ALTER TABLE kb.documents ENABLE ROW LEVEL SECURITY;

ALTER TABLE kb.extraction_edits ENABLE ROW LEVEL SECURITY;

ALTER TABLE kb.extractions ENABLE ROW LEVEL SECURITY;

ALTER TABLE kb.files ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_no_link ON kb.acl AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON kb.shelves AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_scope ON kb.chunks USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM kb.documents p
  WHERE (p.id = chunks.document_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM kb.documents p
  WHERE (p.id = chunks.document_id)))));

CREATE POLICY p_scope ON kb.extraction_edits USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM kb.extractions p
  WHERE (p.id = extraction_edits.extraction_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM kb.extractions p
  WHERE (p.id = extraction_edits.extraction_id)))));

CREATE POLICY p_scope ON kb.extractions USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM kb.documents p
  WHERE (p.id = extractions.document_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM kb.documents p
  WHERE (p.id = extractions.document_id)))));

CREATE POLICY p_scope ON kb.files USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM kb.documents p
  WHERE (p.id = files.document_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM kb.documents p
  WHERE (p.id = files.document_id)))));

CREATE POLICY p_shelf ON kb.documents USING (((tenant_id = core.tenant()) AND core.can_see('kb.common'::text, created_by, NULL::uuid) AND (core.is_system() OR kb.shelf_visible(shelf)) AND ((source_kind = 'internal'::text) OR core.is_system() OR (EXISTS ( SELECT 1
   FROM kb.acl a
  WHERE ((a.document_id = documents.id) AND (((a.principal_type = 'user'::text) AND (a.principal = core.my_email())) OR ((a.principal_type = 'domain'::text) AND (a.principal = split_part(core.my_email(), '@'::text, 2))) OR ((a.principal_type = 'group'::text) AND (EXISTS ( SELECT 1
           FROM core.identity_groups g
          WHERE ((g.group_email = a.principal) AND (g.user_email = core.my_email())))))))))))) WITH CHECK (((tenant_id = core.tenant()) AND ((source_kind = 'internal'::text) OR core.is_system()) AND (core.is_system() OR kb.shelf_visible(shelf))));

CREATE POLICY p_tenant ON kb.acl USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON kb.shelves USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

ALTER TABLE kb.shelves ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA kb TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE kb.acl TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE kb.chunks TO barista_app;

GRANT USAGE ON SEQUENCE kb.chunks_id_seq TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE kb.documents TO barista_app;

GRANT SELECT,INSERT ON TABLE kb.extraction_edits TO barista_app;

GRANT USAGE ON SEQUENCE kb.extraction_edits_id_seq TO barista_app;

GRANT SELECT,INSERT ON TABLE kb.extractions TO barista_app;

GRANT SELECT,INSERT,DELETE ON TABLE kb.files TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE kb.shelves TO barista_app;
