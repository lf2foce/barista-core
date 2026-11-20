-- module: ledger
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (lõi — sổ cái).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA ledger;

CREATE FUNCTION ledger.check_balanced() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE s BIGINT; n INTEGER;
BEGIN
    SELECT coalesce(sum(p.amount_vnd), 0), count(*) INTO s, n FROM ledger.postings p WHERE p.journal_id = NEW.journal_id;
    IF s <> 0 OR n < 2 THEN
        RAISE EXCEPTION 'bút toán % lệch: tổng % (cần 0), % dòng', NEW.journal_id, s, n;
    END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION ledger.immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN RAISE EXCEPTION 'sổ cái chỉ thêm: sai thì ghi bút toán đảo'; END $$;

CREATE TABLE ledger.accounts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    kind text NOT NULL,
    owner_user_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT accounts_code_check CHECK ((code ~ '^[a-z_]+(:[a-z0-9_-]+)?$'::text)),
    CONSTRAINT accounts_kind_check CHECK ((kind = ANY (ARRAY['asset'::text, 'liability'::text, 'equity'::text, 'revenue'::text, 'expense'::text])))
);

ALTER TABLE ONLY ledger.accounts FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE ledger.accounts IS 'resource=ledger';

CREATE TABLE ledger.journals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    memo text NOT NULL,
    source text NOT NULL,
    source_ref text DEFAULT ''::text NOT NULL,
    idempotency_key text NOT NULL,
    reverses uuid,
    created_by uuid
);

ALTER TABLE ONLY ledger.journals FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE ledger.journals IS 'resource=ledger';

CREATE TABLE ledger.postings (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    journal_id uuid NOT NULL,
    account_id uuid NOT NULL,
    amount_vnd bigint NOT NULL,
    memo text DEFAULT ''::text NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT postings_amount_vnd_check CHECK ((amount_vnd <> 0))
);

ALTER TABLE ONLY ledger.postings FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE ledger.postings IS 'parent=ledger.accounts';

CREATE SEQUENCE ledger.postings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ledger.postings_id_seq OWNED BY ledger.postings.id;

ALTER TABLE ONLY ledger.postings ALTER COLUMN id SET DEFAULT nextval('ledger.postings_id_seq'::regclass);

ALTER TABLE ONLY ledger.accounts
    ADD CONSTRAINT accounts_id_tenant_id_key UNIQUE (id, tenant_id);

ALTER TABLE ONLY ledger.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ledger.accounts
    ADD CONSTRAINT accounts_tenant_id_code_key UNIQUE (tenant_id, code);

ALTER TABLE ONLY ledger.journals
    ADD CONSTRAINT journals_id_tenant_id_key UNIQUE (id, tenant_id);

ALTER TABLE ONLY ledger.journals
    ADD CONSTRAINT journals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY ledger.journals
    ADD CONSTRAINT journals_tenant_id_idempotency_key_key UNIQUE (tenant_id, idempotency_key);

ALTER TABLE ONLY ledger.postings
    ADD CONSTRAINT postings_pkey PRIMARY KEY (id);

CREATE INDEX idx_postings_account ON ledger.postings USING btree (account_id, at);

CREATE INDEX idx_postings_journal ON ledger.postings USING btree (journal_id);

CREATE CONSTRAINT TRIGGER t_balanced AFTER INSERT ON ledger.postings DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger.check_balanced();

CREATE TRIGGER t_journals_immutable BEFORE DELETE OR UPDATE ON ledger.journals FOR EACH ROW EXECUTE FUNCTION ledger.immutable();

CREATE TRIGGER t_postings_immutable BEFORE DELETE OR UPDATE ON ledger.postings FOR EACH ROW EXECUTE FUNCTION ledger.immutable();

ALTER TABLE ONLY ledger.accounts
    ADD CONSTRAINT accounts_owner_user_id_fkey FOREIGN KEY (owner_user_id) REFERENCES core.users(id);

ALTER TABLE ONLY ledger.accounts
    ADD CONSTRAINT accounts_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY ledger.journals
    ADD CONSTRAINT journals_reverses_fkey FOREIGN KEY (reverses) REFERENCES ledger.journals(id);

ALTER TABLE ONLY ledger.journals
    ADD CONSTRAINT journals_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY ledger.postings
    ADD CONSTRAINT postings_account_id_tenant_id_fkey FOREIGN KEY (account_id, tenant_id) REFERENCES ledger.accounts(id, tenant_id);

ALTER TABLE ONLY ledger.postings
    ADD CONSTRAINT postings_journal_id_tenant_id_fkey FOREIGN KEY (journal_id, tenant_id) REFERENCES ledger.journals(id, tenant_id);

ALTER TABLE ledger.accounts ENABLE ROW LEVEL SECURITY;

ALTER TABLE ledger.journals ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_scope ON ledger.accounts USING (((tenant_id = core.tenant()) AND core.can_see('ledger'::text, owner_user_id, NULL::uuid))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('ledger'::text, owner_user_id, NULL::uuid)));

CREATE POLICY p_scope ON ledger.journals USING (((tenant_id = core.tenant()) AND core.can_see('ledger'::text, NULL::uuid, NULL::uuid))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('ledger'::text, NULL::uuid, NULL::uuid)));

CREATE POLICY p_scope ON ledger.postings USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM ledger.accounts p
  WHERE (p.id = postings.account_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM ledger.accounts p
  WHERE (p.id = postings.account_id)))));

ALTER TABLE ledger.postings ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA ledger TO barista_app;

GRANT SELECT,INSERT ON TABLE ledger.accounts TO barista_app;

GRANT SELECT,INSERT ON TABLE ledger.journals TO barista_app;

GRANT SELECT,INSERT ON TABLE ledger.postings TO barista_app;

GRANT USAGE ON SEQUENCE ledger.postings_id_seq TO barista_app;
