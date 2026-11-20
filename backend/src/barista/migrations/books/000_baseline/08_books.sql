-- module: books
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (năng lực chung — books).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA books;

CREATE FUNCTION books.keep_original_due() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.original_due_on := coalesce(NEW.original_due_on, NEW.due_on);
    ELSIF ROW(NEW.original_due_on, NEW.calendar_ref, NEW.calendar_warning, NEW.rule_ref, NEW.basis)
          IS DISTINCT FROM ROW(OLD.original_due_on, OLD.calendar_ref, OLD.calendar_warning, OLD.rule_ref, OLD.basis) THEN
        RAISE EXCEPTION 'Nguồn tính hạn ban đầu không được sửa';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION books.subject_visible_company(id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $_$ SELECT $1 = core.tenant() AND core.scope('books') = 'all' $_$;

CREATE FUNCTION books.subject_visible_period(id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $_$ SELECT EXISTS (SELECT 1 FROM books.periods p WHERE p.id = $1) $_$;

CREATE TABLE books.filing_events (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    filing_id uuid NOT NULL,
    status text NOT NULL,
    note text NOT NULL,
    document_id uuid,
    by_user uuid,
    at timestamp with time zone DEFAULT now() NOT NULL,
    kind text DEFAULT 'status'::text NOT NULL,
    old_due_on date,
    new_due_on date,
    CONSTRAINT filing_event_added CHECK (((kind <> 'added'::text) OR (new_due_on IS NOT NULL))),
    CONSTRAINT filing_event_due CHECK (((kind <> 'rescheduled'::text) OR ((old_due_on IS NOT NULL) AND (new_due_on IS NOT NULL) AND (old_due_on <> new_due_on)))),
    CONSTRAINT filing_events_kind_check CHECK ((kind = ANY (ARRAY['status'::text, 'added'::text, 'rescheduled'::text]))),
    CONSTRAINT filing_events_note_check CHECK ((btrim(note) <> ''::text)),
    CONSTRAINT filing_events_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'filed'::text, 'not_required'::text])))
);

ALTER TABLE ONLY books.filing_events FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE books.filing_events IS 'parent=books.filings';

CREATE SEQUENCE books.filing_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE books.filing_events_id_seq OWNED BY books.filing_events.id;

CREATE TABLE books.filings (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    period_id uuid NOT NULL,
    obligation_id uuid NOT NULL,
    label text NOT NULL,
    due_on date NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    evidence text DEFAULT ''::text NOT NULL,
    document_id uuid,
    done_by uuid,
    done_at timestamp with time zone,
    rule_ref text DEFAULT ''::text NOT NULL,
    basis text DEFAULT ''::text NOT NULL,
    original_due_on date NOT NULL,
    calendar_ref text DEFAULT ''::text NOT NULL,
    calendar_warning text DEFAULT ''::text NOT NULL,
    CONSTRAINT filings_check CHECK (((status = 'pending'::text) OR ((btrim(evidence) <> ''::text) AND (done_at IS NOT NULL)))),
    CONSTRAINT filings_label_check CHECK ((btrim(label) <> ''::text)),
    CONSTRAINT filings_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'filed'::text, 'not_required'::text])))
);

ALTER TABLE ONLY books.filings FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE books.filings IS 'parent=books.periods';

CREATE TABLE books.obligations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    code text NOT NULL,
    label text NOT NULL,
    cadence text NOT NULL,
    due_months integer NOT NULL,
    due_day integer NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source text DEFAULT 'manual'::text NOT NULL,
    basis text DEFAULT ''::text NOT NULL,
    rule_version text DEFAULT ''::text NOT NULL,
    holiday_rule text DEFAULT 'keep'::text NOT NULL,
    holiday_calendar text DEFAULT ''::text NOT NULL,
    CONSTRAINT obligation_holiday_calendar CHECK (((holiday_rule = 'keep'::text) OR (holiday_calendar <> ''::text))),
    CONSTRAINT obligations_cadence_check CHECK ((cadence = ANY (ARRAY['monthly'::text, 'quarterly'::text, 'yearly'::text]))),
    CONSTRAINT obligations_code_check CHECK ((code ~ '^[A-Z0-9][A-Z0-9_-]{0,29}$'::text)),
    CONSTRAINT obligations_due_day_check CHECK (((due_day >= 1) AND (due_day <= 31))),
    CONSTRAINT obligations_due_months_check CHECK (((due_months >= 0) AND (due_months <= 12))),
    CONSTRAINT obligations_holiday_rule_check CHECK ((holiday_rule = ANY (ARRAY['keep'::text, 'next_workday'::text]))),
    CONSTRAINT obligations_label_check CHECK ((btrim(label) <> ''::text)),
    CONSTRAINT obligations_source_check CHECK ((source = ANY (ARRAY['manual'::text, 'rule'::text])))
);

ALTER TABLE ONLY books.obligations FORCE ROW LEVEL SECURITY;

CREATE TABLE books.periods (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    period_start date NOT NULL,
    owner_id uuid,
    reviewer_id uuid,
    team_id uuid,
    status text DEFAULT 'open'::text NOT NULL,
    opened_by text DEFAULT 'auto'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT periods_opened_by_check CHECK ((opened_by = ANY (ARRAY['auto'::text, 'manual'::text]))),
    CONSTRAINT periods_period_start_check CHECK ((period_start = (date_trunc('month'::text, (period_start)::timestamp with time zone))::date)),
    CONSTRAINT periods_status_check CHECK ((status = ANY (ARRAY['open'::text, 'cancelled'::text])))
);

ALTER TABLE ONLY books.periods FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE books.periods IS 'resource=books';

CREATE TABLE books.setup (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    owner_id uuid,
    reviewer_id uuid,
    start_period date NOT NULL,
    end_period date,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    entity_kind text DEFAULT 'company'::text NOT NULL,
    reg_pack text DEFAULT 'vn-tax'::text NOT NULL,
    profile jsonb DEFAULT '{}'::jsonb NOT NULL,
    rules_synced_on date,
    CONSTRAINT setup_check CHECK (((end_period = (date_trunc('month'::text, (end_period)::timestamp with time zone))::date) AND (end_period >= start_period))),
    CONSTRAINT setup_entity_kind_check CHECK ((entity_kind = ANY (ARRAY['company'::text, 'household'::text]))),
    CONSTRAINT setup_profile_check CHECK ((jsonb_typeof(profile) = 'object'::text)),
    CONSTRAINT setup_start_period_check CHECK ((start_period = (date_trunc('month'::text, (start_period)::timestamp with time zone))::date))
);

ALTER TABLE ONLY books.setup FORCE ROW LEVEL SECURITY;

ALTER TABLE ONLY books.filing_events ALTER COLUMN id SET DEFAULT nextval('books.filing_events_id_seq'::regclass);

ALTER TABLE ONLY books.filing_events
    ADD CONSTRAINT filing_events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY books.filings
    ADD CONSTRAINT filings_period_id_obligation_id_key UNIQUE (period_id, obligation_id);

ALTER TABLE ONLY books.filings
    ADD CONSTRAINT filings_pkey PRIMARY KEY (id);

ALTER TABLE ONLY books.obligations
    ADD CONSTRAINT obligations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY books.obligations
    ADD CONSTRAINT obligations_tenant_id_code_key UNIQUE (tenant_id, code);

ALTER TABLE ONLY books.periods
    ADD CONSTRAINT periods_pkey PRIMARY KEY (id);

ALTER TABLE ONLY books.periods
    ADD CONSTRAINT periods_tenant_id_period_start_key UNIQUE (tenant_id, period_start);

ALTER TABLE ONLY books.setup
    ADD CONSTRAINT setup_pkey PRIMARY KEY (tenant_id);

CREATE INDEX idx_books_filing_events ON books.filing_events USING btree (filing_id, at);

CREATE INDEX idx_books_filings_due ON books.filings USING btree (tenant_id, status, due_on);

CREATE TRIGGER books_initial_due BEFORE INSERT OR UPDATE ON books.filings FOR EACH ROW EXECUTE FUNCTION books.keep_original_due();

ALTER TABLE ONLY books.filing_events
    ADD CONSTRAINT filing_events_by_user_fkey FOREIGN KEY (by_user) REFERENCES core.users(id);

ALTER TABLE ONLY books.filing_events
    ADD CONSTRAINT filing_events_filing_id_fkey FOREIGN KEY (filing_id) REFERENCES books.filings(id) ON DELETE CASCADE;

ALTER TABLE ONLY books.filing_events
    ADD CONSTRAINT filing_events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY books.filings
    ADD CONSTRAINT filings_done_by_fkey FOREIGN KEY (done_by) REFERENCES core.users(id);

ALTER TABLE ONLY books.filings
    ADD CONSTRAINT filings_obligation_id_fkey FOREIGN KEY (obligation_id) REFERENCES books.obligations(id) ON DELETE RESTRICT;

ALTER TABLE ONLY books.filings
    ADD CONSTRAINT filings_period_id_fkey FOREIGN KEY (period_id) REFERENCES books.periods(id) ON DELETE CASCADE;

ALTER TABLE ONLY books.filings
    ADD CONSTRAINT filings_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY books.obligations
    ADD CONSTRAINT obligations_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY books.periods
    ADD CONSTRAINT periods_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES core.users(id);

ALTER TABLE ONLY books.periods
    ADD CONSTRAINT periods_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES core.users(id);

ALTER TABLE ONLY books.periods
    ADD CONSTRAINT periods_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY books.periods
    ADD CONSTRAINT periods_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY books.setup
    ADD CONSTRAINT setup_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES core.users(id);

ALTER TABLE ONLY books.setup
    ADD CONSTRAINT setup_reviewer_id_fkey FOREIGN KEY (reviewer_id) REFERENCES core.users(id);

ALTER TABLE ONLY books.setup
    ADD CONSTRAINT setup_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE books.filing_events ENABLE ROW LEVEL SECURITY;

ALTER TABLE books.filings ENABLE ROW LEVEL SECURITY;

ALTER TABLE books.obligations ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_no_link ON books.obligations AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON books.setup AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_scope ON books.filing_events USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM books.filings p
  WHERE (p.id = filing_events.filing_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM books.filings p
  WHERE (p.id = filing_events.filing_id)))));

CREATE POLICY p_scope ON books.filings USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM books.periods p
  WHERE (p.id = filings.period_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM books.periods p
  WHERE (p.id = filings.period_id)))));

CREATE POLICY p_scope ON books.periods USING (((tenant_id = core.tenant()) AND core.can_see('books'::text, owner_id, team_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('books'::text, owner_id, team_id)));

CREATE POLICY p_tenant ON books.obligations USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON books.setup USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

ALTER TABLE books.periods ENABLE ROW LEVEL SECURITY;

ALTER TABLE books.setup ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA books TO barista_app;

GRANT SELECT,INSERT ON TABLE books.filing_events TO barista_app;

GRANT USAGE ON SEQUENCE books.filing_events_id_seq TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE books.filings TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE books.obligations TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE books.periods TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE books.setup TO barista_app;

-- Dữ liệu tham chiếu bắt buộc: loại đối tượng của module
INSERT INTO core.subject_kinds (kind, visible_fn, pack) VALUES ('books.company', 'books.subject_visible_company', 'books');
INSERT INTO core.subject_kinds (kind, visible_fn, pack) VALUES ('books.period', 'books.subject_visible_period', 'books');
