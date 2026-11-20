-- module: acct
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (năng lực chung — finance).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA acct;

CREATE TABLE acct.account_map (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    ledger_prefix text NOT NULL,
    misa_account text NOT NULL,
    label text DEFAULT ''::text NOT NULL,
    CONSTRAINT account_map_misa_account_check CHECK ((misa_account ~ '^[0-9]{3,8}$'::text))
);

ALTER TABLE ONLY acct.account_map FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE acct.account_map IS 'resource=acct';

CREATE TABLE acct.payments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    receivable_id uuid NOT NULL,
    line_id uuid NOT NULL,
    amount_vnd bigint NOT NULL,
    fee_vnd bigint DEFAULT 0 NOT NULL,
    received_on date NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT payments_amount_vnd_check CHECK ((amount_vnd > 0)),
    CONSTRAINT payments_check CHECK (((fee_vnd >= 0) AND (fee_vnd <= amount_vnd)))
);

ALTER TABLE ONLY acct.payments FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE acct.payments IS 'parent=acct.receivables';

CREATE TABLE acct.payouts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    user_id uuid NOT NULL,
    amount_vnd bigint NOT NULL,
    status text DEFAULT 'pending_transfer'::text NOT NULL,
    bank_ref text DEFAULT ''::text NOT NULL,
    transferred_on date,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source text DEFAULT ''::text NOT NULL,
    source_ref text DEFAULT ''::text NOT NULL,
    label text DEFAULT ''::text NOT NULL,
    CONSTRAINT payouts_amount_vnd_check CHECK ((amount_vnd > 0)),
    CONSTRAINT payouts_source_check CHECK (((source ~ '^[a-z_]+\.[a-z_]+$'::text) AND (btrim(source_ref) <> ''::text) AND (btrim(label) <> ''::text))),
    CONSTRAINT payouts_status_check CHECK ((status = ANY (ARRAY['pending_transfer'::text, 'transferred'::text])))
);

ALTER TABLE ONLY acct.payouts FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE acct.payouts IS 'resource=acct';

CREATE TABLE acct.receivables (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    code text NOT NULL,
    source text NOT NULL,
    source_ref text NOT NULL,
    label text NOT NULL,
    party text DEFAULT ''::text NOT NULL,
    nature text NOT NULL,
    amount_vnd bigint NOT NULL,
    fee_vnd bigint DEFAULT 0 NOT NULL,
    revenue_account text DEFAULT ''::text NOT NULL,
    payee_key text DEFAULT ''::text NOT NULL,
    payee_name text DEFAULT ''::text NOT NULL,
    match_hints text[] DEFAULT '{}'::text[] NOT NULL,
    paid_vnd bigint DEFAULT 0 NOT NULL,
    issued_on date DEFAULT core.today() NOT NULL,
    due_on date NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    owner_id uuid,
    team_id uuid,
    followed_at timestamp with time zone,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    receivable_account text DEFAULT 'receivable:customers'::text NOT NULL,
    match_keys text[] DEFAULT '{}'::text[] NOT NULL,
    recognized_key text DEFAULT ''::text NOT NULL,
    CONSTRAINT receivables_amount_vnd_check CHECK ((amount_vnd > 0)),
    CONSTRAINT receivables_check CHECK (((fee_vnd >= 0) AND (fee_vnd <= amount_vnd))),
    CONSTRAINT receivables_check1 CHECK (((paid_vnd >= 0) AND (paid_vnd <= amount_vnd))),
    CONSTRAINT receivables_check2 CHECK (((nature <> 'revenue'::text) OR ((revenue_account <> ''::text) AND (fee_vnd = amount_vnd) AND (payee_key = ''::text)))),
    CONSTRAINT receivables_check3 CHECK (((nature <> 'pass_through'::text) OR ((payee_key <> ''::text) AND ((fee_vnd = 0) OR (revenue_account <> ''::text))))),
    CONSTRAINT receivables_check4 CHECK (((nature <> 'deposit'::text) OR ((payee_key <> ''::text) AND (fee_vnd = 0)))),
    CONSTRAINT receivables_code_check CHECK ((code ~ '^[0-9A-Z][0-9A-Z-]{3,40}$'::text)),
    CONSTRAINT receivables_label_check CHECK ((btrim(label) <> ''::text)),
    CONSTRAINT receivables_nature_check CHECK ((nature = ANY (ARRAY['revenue'::text, 'pass_through'::text, 'deposit'::text]))),
    CONSTRAINT receivables_payee_key_check CHECK ((payee_key ~ '^[a-z0-9_-]*$'::text)),
    CONSTRAINT receivables_receivable_account_check CHECK ((receivable_account ~ '^receivable:[a-z_]+$'::text)),
    CONSTRAINT receivables_revenue_account_check CHECK (((revenue_account = ''::text) OR (revenue_account ~ '^revenue:[a-z_]+$'::text))),
    CONSTRAINT receivables_source_check CHECK ((source ~ '^[a-z_]+\.[a-z_]+$'::text)),
    CONSTRAINT receivables_source_ref_check CHECK ((btrim(source_ref) <> ''::text)),
    CONSTRAINT receivables_status_check CHECK ((status = ANY (ARRAY['open'::text, 'partial'::text, 'paid'::text, 'void'::text])))
);

ALTER TABLE ONLY acct.receivables FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE acct.receivables IS 'resource=acct';

CREATE TABLE acct.remittances (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    account text NOT NULL,
    payee_name text NOT NULL,
    payee_bank text DEFAULT ''::text NOT NULL,
    amount_vnd bigint NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    source text DEFAULT ''::text NOT NULL,
    source_ref text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'requested'::text NOT NULL,
    approval_id uuid,
    bank_ref text DEFAULT ''::text NOT NULL,
    transferred_on date,
    requested_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_at timestamp with time zone,
    CONSTRAINT remittances_account_check CHECK ((account ~ '^(held|deposit):[a-z0-9_-]+$'::text)),
    CONSTRAINT remittances_amount_vnd_check CHECK ((amount_vnd > 0)),
    CONSTRAINT remittances_payee_name_check CHECK ((btrim(payee_name) <> ''::text)),
    CONSTRAINT remittances_status_check CHECK ((status = ANY (ARRAY['requested'::text, 'pending_transfer'::text, 'transferred'::text, 'rejected'::text])))
);

ALTER TABLE ONLY acct.remittances FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE acct.remittances IS 'resource=acct';

CREATE TABLE acct.statement_lines (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    received_on date NOT NULL,
    amount_vnd bigint NOT NULL,
    memo text DEFAULT ''::text NOT NULL,
    payer text DEFAULT ''::text NOT NULL,
    bank_ref text DEFAULT ''::text NOT NULL,
    source text DEFAULT 'excel'::text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    decided_by uuid,
    decided_at timestamp with time zone,
    allocated_vnd bigint DEFAULT 0 NOT NULL,
    accepted_at timestamp with time zone,
    CONSTRAINT statement_lines_allocated_check CHECK (((allocated_vnd >= 0) AND (allocated_vnd <= amount_vnd))),
    CONSTRAINT statement_lines_amount_vnd_check CHECK ((amount_vnd > 0)),
    CONSTRAINT statement_lines_check CHECK (((status = 'applied'::text) = (allocated_vnd = amount_vnd))),
    CONSTRAINT statement_lines_source_check CHECK ((source = ANY (ARRAY['excel'::text, 'image'::text, 'manual'::text]))),
    CONSTRAINT statement_lines_status_check CHECK ((status = ANY (ARRAY['open'::text, 'applied'::text, 'ignored'::text])))
);

ALTER TABLE ONLY acct.statement_lines FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE acct.statement_lines IS 'resource=acct';

ALTER TABLE ONLY acct.account_map
    ADD CONSTRAINT account_map_pkey PRIMARY KEY (tenant_id, ledger_prefix);

ALTER TABLE ONLY acct.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY acct.payouts
    ADD CONSTRAINT payouts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY acct.receivables
    ADD CONSTRAINT receivables_pkey PRIMARY KEY (id);

ALTER TABLE ONLY acct.receivables
    ADD CONSTRAINT receivables_tenant_id_code_key UNIQUE (tenant_id, code);

ALTER TABLE ONLY acct.receivables
    ADD CONSTRAINT receivables_tenant_id_source_source_ref_key UNIQUE (tenant_id, source, source_ref);

ALTER TABLE ONLY acct.remittances
    ADD CONSTRAINT remittances_pkey PRIMARY KEY (id);

ALTER TABLE ONLY acct.statement_lines
    ADD CONSTRAINT statement_lines_pkey PRIMARY KEY (id);

CREATE INDEX idx_payments_receivable ON acct.payments USING btree (receivable_id);

CREATE INDEX idx_receivables_open ON acct.receivables USING btree (tenant_id, due_on) WHERE (status = ANY (ARRAY['open'::text, 'partial'::text]));

CREATE INDEX idx_statement_lines_open ON acct.statement_lines USING btree (tenant_id, received_on) WHERE (status = 'open'::text);

CREATE INDEX idx_statement_lines_partial ON acct.statement_lines USING btree (tenant_id) WHERE ((status = 'open'::text) AND (allocated_vnd > 0));

CREATE UNIQUE INDEX uq_payouts_source ON acct.payouts USING btree (tenant_id, source, source_ref) WHERE (source <> ''::text);

CREATE UNIQUE INDEX uq_statement_lines_bank_ref ON acct.statement_lines USING btree (tenant_id, bank_ref) WHERE (bank_ref <> ''::text);

ALTER TABLE ONLY acct.account_map
    ADD CONSTRAINT account_map_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY acct.payments
    ADD CONSTRAINT payments_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY acct.payments
    ADD CONSTRAINT payments_line_id_fkey FOREIGN KEY (line_id) REFERENCES acct.statement_lines(id);

ALTER TABLE ONLY acct.payments
    ADD CONSTRAINT payments_receivable_id_fkey FOREIGN KEY (receivable_id) REFERENCES acct.receivables(id);

ALTER TABLE ONLY acct.payments
    ADD CONSTRAINT payments_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY acct.payouts
    ADD CONSTRAINT payouts_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY acct.payouts
    ADD CONSTRAINT payouts_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id);

ALTER TABLE ONLY acct.receivables
    ADD CONSTRAINT receivables_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY acct.receivables
    ADD CONSTRAINT receivables_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES core.users(id);

ALTER TABLE ONLY acct.receivables
    ADD CONSTRAINT receivables_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY acct.receivables
    ADD CONSTRAINT receivables_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY acct.remittances
    ADD CONSTRAINT remittances_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES core.users(id);

ALTER TABLE ONLY acct.remittances
    ADD CONSTRAINT remittances_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY acct.statement_lines
    ADD CONSTRAINT statement_lines_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY acct.statement_lines
    ADD CONSTRAINT statement_lines_decided_by_fkey FOREIGN KEY (decided_by) REFERENCES core.users(id);

ALTER TABLE ONLY acct.statement_lines
    ADD CONSTRAINT statement_lines_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE acct.account_map ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_scope ON acct.account_map USING (((tenant_id = core.tenant()) AND core.can_see('acct'::text, NULL::uuid, NULL::uuid))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('acct'::text, NULL::uuid, NULL::uuid)));

CREATE POLICY p_scope ON acct.payments USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM acct.receivables p
  WHERE (p.id = payments.receivable_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM acct.receivables p
  WHERE (p.id = payments.receivable_id)))));

CREATE POLICY p_scope ON acct.payouts USING (((tenant_id = core.tenant()) AND core.can_see('acct'::text, user_id, NULL::uuid))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('acct'::text, user_id, NULL::uuid)));

CREATE POLICY p_scope ON acct.receivables USING (((tenant_id = core.tenant()) AND core.can_see('acct'::text, owner_id, team_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('acct'::text, owner_id, team_id)));

CREATE POLICY p_scope ON acct.remittances USING (((tenant_id = core.tenant()) AND core.can_see('acct'::text, NULL::uuid, NULL::uuid))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('acct'::text, NULL::uuid, NULL::uuid)));

CREATE POLICY p_scope ON acct.statement_lines USING (((tenant_id = core.tenant()) AND core.can_see('acct'::text, NULL::uuid, NULL::uuid))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('acct'::text, NULL::uuid, NULL::uuid)));

ALTER TABLE acct.payments ENABLE ROW LEVEL SECURITY;

ALTER TABLE acct.payouts ENABLE ROW LEVEL SECURITY;

ALTER TABLE acct.receivables ENABLE ROW LEVEL SECURITY;

ALTER TABLE acct.remittances ENABLE ROW LEVEL SECURITY;

ALTER TABLE acct.statement_lines ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA acct TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE acct.account_map TO barista_app;

GRANT SELECT,INSERT ON TABLE acct.payments TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE acct.payouts TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE acct.receivables TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE acct.remittances TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE acct.statement_lines TO barista_app;
