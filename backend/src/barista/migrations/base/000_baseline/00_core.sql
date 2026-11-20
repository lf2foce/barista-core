-- module: core
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (lõi).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA core;

CREATE FUNCTION core.can_see(resource text, owner uuid, team uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE core.scope(resource)
        WHEN 'all'  THEN true
        WHEN 'team' THEN (owner IS NOT NULL AND owner = core.me()) OR (team IS NOT NULL AND team = ANY(core.my_teams()))
        WHEN 'own'  THEN owner IS NOT NULL AND owner = core.me()
        ELSE false
    END
$$;

CREATE FUNCTION core.ensure_user(p_email text, p_name text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'core', 'pg_temp'
    AS $_$
DECLARE v_id UUID; v_email TEXT := lower(btrim(p_email));
BEGIN
    IF core.tenant() IS NULL THEN RAISE EXCEPTION 'ensure_user cần phiên đã gắn công ty'; END IF;
    IF v_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' THEN RAISE EXCEPTION 'email không hợp lệ: %', p_email; END IF;
    INSERT INTO core.users (email, name) VALUES (v_email, left(btrim(coalesce(p_name, '')), 120)) ON CONFLICT (email) DO NOTHING;
    SELECT id INTO v_id FROM core.users WHERE email = v_email;
    RETURN v_id;
END $_$;

CREATE FUNCTION core.is_system() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT current_setting('app.system', true) = 'on' $$;

CREATE FUNCTION core.link_ref() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('app.link_ref', true), '')::uuid $$;

CREATE FUNCTION core.me() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;

CREATE FUNCTION core.my_email() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('app.email', true), '') $$;

CREATE FUNCTION core.my_teams() RETURNS uuid[]
    LANGUAGE sql STABLE
    AS $$ SELECT coalesce(nullif(current_setting('app.teams', true), '')::uuid[], '{}'::uuid[]) $$;

CREATE FUNCTION core.notify_new() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM pg_notify('barista_notifications', NEW.tenant_id::text || ':' || NEW.user_id::text);   -- chỉ mã, không nội dung
    RETURN NEW;
END $$;

CREATE FUNCTION core.scope(resource text) RETURNS text
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE WHEN current_setting('app.system', true) = 'on' THEN 'all'
                ELSE coalesce(nullif(current_setting('app.scopes', true), '')::jsonb ->> resource, 'none') END
$$;

CREATE PROCEDURE core.secure_child(IN tbl regclass, IN parent regclass, IN fk_col text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    cond TEXT := format('tenant_id = core.tenant() AND EXISTS (SELECT 1 FROM %s p WHERE p.id = %s)', parent, quote_ident(fk_col));
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format('CREATE POLICY p_scope ON %s USING (%s) WITH CHECK (%s)', tbl, cond, cond);
    EXECUTE format('COMMENT ON TABLE %s IS %L', tbl, 'parent=' || parent::text);
END $$;

CREATE PROCEDURE core.secure_table(IN tbl regclass, IN resource text, IN owner_col text DEFAULT NULL::text, IN team_col text DEFAULT NULL::text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    cond TEXT := format('tenant_id = core.tenant() AND core.can_see(%L, %s, %s)', resource,
                        coalesce(quote_ident(owner_col), 'NULL::uuid'), coalesce(quote_ident(team_col), 'NULL::uuid'));
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format('CREATE POLICY p_scope ON %s USING (%s) WITH CHECK (%s)', tbl, cond, cond);
    EXECUTE format('COMMENT ON TABLE %s IS %L', tbl, 'resource=' || resource);
END $$;

CREATE PROCEDURE core.secure_tenant(IN tbl regclass)
    LANGUAGE plpgsql
    AS $$
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format('CREATE POLICY p_tenant ON %s USING (tenant_id = core.tenant()) WITH CHECK (tenant_id = core.tenant())', tbl);
    EXECUTE format('CREATE POLICY p_no_link ON %s AS RESTRICTIVE FOR SELECT USING (core.link_ref() IS NULL)', tbl);
END $$;

CREATE FUNCTION core.subject_on(kind text) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT coalesce(kind, '') = '' OR coalesce((SELECT s.pack = 'base' OR EXISTS (SELECT 1 FROM core.tenant_packs tp
                                                                                WHERE tp.tenant_id = core.tenant() AND tp.pack = s.pack)
                                                FROM core.subject_kinds s WHERE s.kind = subject_on.kind), true)
$$;

CREATE FUNCTION core.subject_visible(kind text, id uuid) RETURNS boolean
    LANGUAGE plpgsql STABLE
    AS $_$
DECLARE
    fn TEXT;
    ok BOOLEAN;
BEGIN
    SELECT s.visible_fn INTO fn FROM core.subject_kinds s WHERE s.kind = subject_visible.kind;
    IF fn IS NULL OR NOT core.subject_on(kind) THEN
        RETURN false;
    END IF;
    EXECUTE format('SELECT %s($1)', fn) INTO ok USING id;
    RETURN coalesce(ok, false);
END $_$;

CREATE FUNCTION core.tenant() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;

CREATE FUNCTION core.today() RETURNS date
    LANGUAGE sql STABLE
    AS $$ SELECT (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date $$;

CREATE TABLE core.agent_policies (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    command text NOT NULL,
    mode text NOT NULL,
    CONSTRAINT agent_policies_mode_check CHECK ((mode = ANY (ARRAY['allow'::text, 'propose'::text, 'deny'::text])))
);

ALTER TABLE ONLY core.agent_policies FORCE ROW LEVEL SECURITY;

CREATE TABLE core.ai_usage (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    user_id uuid NOT NULL,
    team_id uuid,
    at timestamp with time zone DEFAULT now() NOT NULL,
    surface text NOT NULL,
    category text NOT NULL,
    modules text[] DEFAULT '{}'::text[] NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    requests integer DEFAULT 0 NOT NULL,
    tool_calls integer DEFAULT 0 NOT NULL,
    input_tokens bigint DEFAULT 0 NOT NULL,
    output_tokens bigint DEFAULT 0 NOT NULL,
    cache_read_tokens bigint DEFAULT 0 NOT NULL,
    cache_write_tokens bigint DEFAULT 0 NOT NULL,
    cost_usd numeric(12,6),
    ok boolean DEFAULT true NOT NULL,
    tools text[] DEFAULT '{}'::text[] NOT NULL,
    tool_errors text[] DEFAULT '{}'::text[] NOT NULL,
    duration_ms integer,
    CONSTRAINT ai_usage_category_check CHECK ((btrim(category) <> ''::text)),
    CONSTRAINT ai_usage_duration_ms_check CHECK ((duration_ms >= 0)),
    CONSTRAINT ai_usage_surface_check CHECK ((surface = ANY (ARRAY['chat'::text, 'voice'::text, 'resume'::text, 'work'::text, 'kb'::text])))
);

ALTER TABLE ONLY core.ai_usage FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE core.ai_usage IS 'resource=core.ai_usage';

CREATE SEQUENCE core.ai_usage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE core.ai_usage_id_seq OWNED BY core.ai_usage.id;

CREATE TABLE core.approval_chains (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    key text NOT NULL,
    label text NOT NULL,
    steps jsonb NOT NULL,
    CONSTRAINT approval_chains_key_check CHECK ((key ~ '^[a-z_]+$'::text)),
    CONSTRAINT approval_chains_steps_check CHECK (((jsonb_typeof(steps) = 'array'::text) AND (jsonb_array_length(steps) > 0)))
);

ALTER TABLE ONLY core.approval_chains FORCE ROW LEVEL SECURITY;

CREATE TABLE core.approval_rules (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    command text NOT NULL,
    chain text NOT NULL
);

ALTER TABLE ONLY core.approval_rules FORCE ROW LEVEL SECURITY;

CREATE TABLE core.approvals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    chain text NOT NULL,
    command text NOT NULL,
    input jsonb NOT NULL,
    run_as text NOT NULL,
    on_reject text,
    summary text NOT NULL,
    requested_by uuid NOT NULL,
    steps jsonb NOT NULL,
    current_step integer DEFAULT 0 NOT NULL,
    approver_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    participant_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    decisions jsonb DEFAULT '[]'::jsonb NOT NULL,
    result jsonb,
    error text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    basis jsonb,
    CONSTRAINT approvals_run_as_check CHECK ((run_as = ANY (ARRAY['requester'::text, 'system'::text]))),
    CONSTRAINT approvals_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'rejected'::text, 'executed'::text, 'failed'::text, 'stale'::text])))
);

ALTER TABLE ONLY core.approvals FORCE ROW LEVEL SECURITY;

CREATE TABLE core.audit_log (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    user_id uuid,
    approved_by uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    system boolean DEFAULT false NOT NULL,
    via text DEFAULT 'human'::text NOT NULL,
    command text NOT NULL,
    input jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text NOT NULL,
    error text DEFAULT ''::text NOT NULL,
    ref text DEFAULT ''::text NOT NULL,
    CONSTRAINT audit_log_status_check CHECK ((status = ANY (ARRAY['ok'::text, 'pending_approval'::text, 'proposed'::text, 'denied'::text, 'invalid'::text, 'not_found'::text, 'conflict'::text, 'error'::text]))),
    CONSTRAINT audit_log_via_check CHECK ((via = ANY (ARRAY['human'::text, 'agent'::text, 'link'::text])))
);

ALTER TABLE ONLY core.audit_log FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE core.audit_log IS 'resource=core.audit';

CREATE SEQUENCE core.audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE core.audit_log_id_seq OWNED BY core.audit_log.id;

CREATE TABLE core.chat_threads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    user_id uuid NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    turns jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY core.chat_threads FORCE ROW LEVEL SECURITY;

CREATE TABLE core.deliveries (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    event_id bigint NOT NULL,
    subscriber text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    next_at timestamp with time zone DEFAULT now() NOT NULL,
    last_error text DEFAULT ''::text NOT NULL,
    done_at timestamp with time zone,
    dismissed_by uuid,
    dismiss_reason text DEFAULT ''::text NOT NULL,
    CONSTRAINT deliveries_dismissed_check CHECK (((status = 'dismissed'::text) = ((dismissed_by IS NOT NULL) AND (btrim(dismiss_reason) <> ''::text)))),
    CONSTRAINT deliveries_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'done'::text, 'dead'::text, 'dismissed'::text])))
);

ALTER TABLE ONLY core.deliveries FORCE ROW LEVEL SECURITY;

CREATE SEQUENCE core.deliveries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE core.deliveries_id_seq OWNED BY core.deliveries.id;

CREATE TABLE core.devices (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    user_id uuid NOT NULL,
    token text NOT NULL,
    platform text DEFAULT 'ios'::text NOT NULL,
    env text DEFAULT 'production'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    disabled_at timestamp with time zone,
    CONSTRAINT devices_env_check CHECK ((env = ANY (ARRAY['sandbox'::text, 'production'::text]))),
    CONSTRAINT devices_platform_check CHECK ((platform = 'ios'::text)),
    CONSTRAINT devices_token_check CHECK ((token ~ '^[0-9a-f]{32,200}$'::text))
);

ALTER TABLE ONLY core.devices FORCE ROW LEVEL SECURITY;

CREATE TABLE core.events (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    type text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    actor uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT events_type_check CHECK ((type ~ '^[a-z_]+\.[a-z_]+$'::text))
);

ALTER TABLE ONLY core.events FORCE ROW LEVEL SECURITY;

CREATE SEQUENCE core.events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE core.events_id_seq OWNED BY core.events.id;

CREATE TABLE core.grants (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    role text NOT NULL,
    resource text NOT NULL,
    scope text NOT NULL,
    actions text[] NOT NULL,
    CONSTRAINT grants_actions_check CHECK (((actions <@ ARRAY['read'::text, 'write'::text, 'approve'::text, 'manage'::text]) AND (cardinality(actions) > 0))),
    CONSTRAINT grants_resource_check CHECK ((resource ~ '^[a-z_]+(\.[a-z_]+)*$'::text)),
    CONSTRAINT grants_scope_check CHECK ((scope = ANY (ARRAY['own'::text, 'team'::text, 'all'::text])))
);

ALTER TABLE ONLY core.grants FORCE ROW LEVEL SECURITY;

CREATE TABLE core.idempotency (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    key text NOT NULL,
    command text NOT NULL,
    input_hash text NOT NULL,
    status text NOT NULL,
    output jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT idempotency_key_check CHECK (((length(key) >= 1) AND (length(key) <= 300)))
);

ALTER TABLE ONLY core.idempotency FORCE ROW LEVEL SECURITY;

CREATE TABLE core.identities (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    provider text NOT NULL,
    subject text NOT NULL,
    user_id uuid NOT NULL,
    email_at_link text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_login_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT identities_provider_check CHECK ((provider = 'google'::text)),
    CONSTRAINT identities_subject_check CHECK ((btrim(subject) <> ''::text))
);

ALTER TABLE ONLY core.identities FORCE ROW LEVEL SECURITY;

CREATE TABLE core.identity_groups (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    group_email text NOT NULL,
    user_email text NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT identity_groups_group_email_check CHECK ((group_email = lower(group_email))),
    CONSTRAINT identity_groups_user_email_check CHECK ((user_email = lower(user_email)))
);

ALTER TABLE ONLY core.identity_groups FORCE ROW LEVEL SECURITY;

CREATE TABLE core.memberships (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    user_id uuid NOT NULL,
    role text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    provided_by uuid
);

ALTER TABLE ONLY core.memberships FORCE ROW LEVEL SECURITY;

CREATE TABLE core.notifications (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    user_id uuid NOT NULL,
    kind text NOT NULL,
    title text NOT NULL,
    body text DEFAULT ''::text NOT NULL,
    push text DEFAULT ''::text NOT NULL,
    open jsonb,
    dedupe_key text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    read_at timestamp with time zone,
    push_state text DEFAULT 'pending'::text NOT NULL,
    push_attempts integer DEFAULT 0 NOT NULL,
    CONSTRAINT notifications_kind_check CHECK ((btrim(kind) <> ''::text)),
    CONSTRAINT notifications_push_state_check CHECK ((push_state = ANY (ARRAY['pending'::text, 'sent'::text, 'skipped'::text, 'failed'::text]))),
    CONSTRAINT notifications_title_check CHECK ((btrim(title) <> ''::text))
);

ALTER TABLE ONLY core.notifications FORCE ROW LEVEL SECURITY;

CREATE SEQUENCE core.notifications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE core.notifications_id_seq OWNED BY core.notifications.id;

CREATE TABLE core.pack_installs (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    kind text NOT NULL,
    key text NOT NULL,
    pack text DEFAULT ''::text NOT NULL,
    version text,
    installed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT pack_installs_kind_check CHECK ((kind = ANY (ARRAY['role'::text, 'grant'::text, 'team'::text, 'chain'::text, 'rule'::text, 'flow'::text])))
);

ALTER TABLE ONLY core.pack_installs FORCE ROW LEVEL SECURITY;

CREATE TABLE core.proposal_runs (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    day date NOT NULL,
    people integer DEFAULT 0 NOT NULL,
    cards integer DEFAULT 0 NOT NULL,
    at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY core.proposal_runs FORCE ROW LEVEL SECURITY;

CREATE TABLE core.proposals (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    user_id uuid NOT NULL,
    day date NOT NULL,
    key text NOT NULL,
    command text NOT NULL,
    title text NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    input jsonb NOT NULL,
    basis jsonb,
    rank integer DEFAULT 500 NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    result jsonb,
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT proposals_status_check CHECK ((status = ANY (ARRAY['open'::text, 'done'::text, 'dismissed'::text, 'expired'::text])))
);

ALTER TABLE ONLY core.proposals FORCE ROW LEVEL SECURITY;

CREATE TABLE core.public_links (
    token_hash bytea NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    target text NOT NULL,
    ref_id uuid NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone,
    CONSTRAINT public_links_target_check CHECK ((target ~ '^[a-z_]+\.[a-z_]+$'::text))
);

ALTER TABLE ONLY core.public_links FORCE ROW LEVEL SECURITY;

CREATE TABLE core.roles (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    key text NOT NULL,
    label text NOT NULL,
    CONSTRAINT roles_key_check CHECK ((key ~ '^[a-z_]{2,30}$'::text)),
    CONSTRAINT roles_label_check CHECK ((btrim(label) <> ''::text))
);

ALTER TABLE ONLY core.roles FORCE ROW LEVEL SECURITY;

CREATE TABLE core.sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token_hash text NOT NULL,
    user_id uuid NOT NULL,
    provider text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    revoked_at timestamp with time zone,
    client text DEFAULT ''::text NOT NULL
);

ALTER TABLE ONLY core.sessions FORCE ROW LEVEL SECURITY;

CREATE TABLE core.subject_kinds (
    kind text NOT NULL,
    visible_fn text NOT NULL,
    pack text DEFAULT 'base'::text NOT NULL,
    CONSTRAINT subject_kinds_kind_check CHECK ((kind ~ '^[a-z_]+\.[a-z_]+$'::text)),
    CONSTRAINT subject_kinds_visible_fn_check CHECK ((visible_fn ~ '^[a-z_]+\.[a-z_]+$'::text))
);

ALTER TABLE ONLY core.subject_kinds FORCE ROW LEVEL SECURITY;

CREATE TABLE core.team_members (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    user_id uuid NOT NULL,
    team_id uuid NOT NULL,
    is_lead boolean DEFAULT false NOT NULL
);

ALTER TABLE ONLY core.team_members FORCE ROW LEVEL SECURITY;

CREATE TABLE core.teams (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    kind text NOT NULL,
    parent_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT teams_code_check CHECK ((code ~ '^[A-Za-z0-9_-]{1,30}$'::text)),
    CONSTRAINT teams_kind_check CHECK ((kind = ANY (ARRAY['company'::text, 'branch'::text, 'sales'::text, 'marketing'::text, 'accounting'::text, 'admin'::text, 'leasing'::text, 'department'::text]))),
    CONSTRAINT teams_name_check CHECK ((btrim(name) <> ''::text))
);

ALTER TABLE ONLY core.teams FORCE ROW LEVEL SECURITY;

CREATE TABLE core.tenant_entitlements (
    tenant_id uuid NOT NULL,
    pack text NOT NULL,
    granted_by text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_entitlements_granted_by_check CHECK ((btrim(granted_by) <> ''::text))
);

ALTER TABLE ONLY core.tenant_entitlements FORCE ROW LEVEL SECURITY;

CREATE TABLE core.tenant_packs (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    pack text NOT NULL,
    enabled_by uuid,
    enabled_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tenant_packs_pack_check CHECK ((pack <> 'base'::text))
);

ALTER TABLE ONLY core.tenant_packs FORCE ROW LEVEL SECURITY;

CREATE TABLE core.tenant_profile (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    about text DEFAULT ''::text NOT NULL,
    updated_by uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    ai_enabled boolean DEFAULT true NOT NULL,
    CONSTRAINT tenant_profile_about_check CHECK ((length(about) <= 600))
);

ALTER TABLE ONLY core.tenant_profile FORCE ROW LEVEL SECURITY;

CREATE TABLE core.tenants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    provisioned_by uuid,
    CONSTRAINT tenants_name_check CHECK ((btrim(name) <> ''::text))
);

ALTER TABLE ONLY core.tenants FORCE ROW LEVEL SECURITY;

CREATE TABLE core.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    email text NOT NULL,
    name text DEFAULT ''::text NOT NULL,
    phone text DEFAULT ''::text NOT NULL,
    birthday date,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_email_check CHECK (((email = lower(email)) AND (POSITION(('@'::text) IN (email)) > 1)))
);

ALTER TABLE ONLY core.users FORCE ROW LEVEL SECURITY;

ALTER TABLE ONLY core.ai_usage ALTER COLUMN id SET DEFAULT nextval('core.ai_usage_id_seq'::regclass);

ALTER TABLE ONLY core.audit_log ALTER COLUMN id SET DEFAULT nextval('core.audit_log_id_seq'::regclass);

ALTER TABLE ONLY core.deliveries ALTER COLUMN id SET DEFAULT nextval('core.deliveries_id_seq'::regclass);

ALTER TABLE ONLY core.events ALTER COLUMN id SET DEFAULT nextval('core.events_id_seq'::regclass);

ALTER TABLE ONLY core.notifications ALTER COLUMN id SET DEFAULT nextval('core.notifications_id_seq'::regclass);

ALTER TABLE ONLY core.agent_policies
    ADD CONSTRAINT agent_policies_pkey PRIMARY KEY (tenant_id, command);

ALTER TABLE ONLY core.ai_usage
    ADD CONSTRAINT ai_usage_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.approval_chains
    ADD CONSTRAINT approval_chains_pkey PRIMARY KEY (tenant_id, key);

ALTER TABLE ONLY core.approval_rules
    ADD CONSTRAINT approval_rules_pkey PRIMARY KEY (tenant_id, command);

ALTER TABLE ONLY core.approvals
    ADD CONSTRAINT approvals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.chat_threads
    ADD CONSTRAINT chat_threads_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.deliveries
    ADD CONSTRAINT deliveries_event_id_subscriber_key UNIQUE (event_id, subscriber);

ALTER TABLE ONLY core.deliveries
    ADD CONSTRAINT deliveries_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.devices
    ADD CONSTRAINT devices_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.devices
    ADD CONSTRAINT devices_tenant_id_user_id_token_key UNIQUE (tenant_id, user_id, token);

ALTER TABLE ONLY core.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.grants
    ADD CONSTRAINT grants_pkey PRIMARY KEY (tenant_id, role, resource);

ALTER TABLE ONLY core.idempotency
    ADD CONSTRAINT idempotency_pkey PRIMARY KEY (tenant_id, key);

ALTER TABLE ONLY core.identities
    ADD CONSTRAINT identities_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.identities
    ADD CONSTRAINT identities_provider_subject_key UNIQUE (provider, subject);

ALTER TABLE ONLY core.identities
    ADD CONSTRAINT identities_user_id_provider_key UNIQUE (user_id, provider);

ALTER TABLE ONLY core.identity_groups
    ADD CONSTRAINT identity_groups_pkey PRIMARY KEY (tenant_id, group_email, user_email);

ALTER TABLE ONLY core.memberships
    ADD CONSTRAINT memberships_pkey PRIMARY KEY (tenant_id, user_id);

ALTER TABLE ONLY core.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.pack_installs
    ADD CONSTRAINT pack_installs_pkey PRIMARY KEY (tenant_id, kind, key);

ALTER TABLE ONLY core.proposal_runs
    ADD CONSTRAINT proposal_runs_pkey PRIMARY KEY (tenant_id, day);

ALTER TABLE ONLY core.proposals
    ADD CONSTRAINT proposals_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.proposals
    ADD CONSTRAINT proposals_tenant_id_user_id_day_key_key UNIQUE (tenant_id, user_id, day, key);

ALTER TABLE ONLY core.public_links
    ADD CONSTRAINT public_links_pkey PRIMARY KEY (token_hash);

ALTER TABLE ONLY core.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (tenant_id, key);

ALTER TABLE ONLY core.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.sessions
    ADD CONSTRAINT sessions_token_hash_key UNIQUE (token_hash);

ALTER TABLE ONLY core.subject_kinds
    ADD CONSTRAINT subject_kinds_pkey PRIMARY KEY (kind);

ALTER TABLE ONLY core.team_members
    ADD CONSTRAINT team_members_pkey PRIMARY KEY (tenant_id, user_id);

ALTER TABLE ONLY core.teams
    ADD CONSTRAINT teams_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.teams
    ADD CONSTRAINT teams_tenant_id_code_key UNIQUE (tenant_id, code);

ALTER TABLE ONLY core.tenant_entitlements
    ADD CONSTRAINT tenant_entitlements_pkey PRIMARY KEY (tenant_id, pack);

ALTER TABLE ONLY core.tenant_packs
    ADD CONSTRAINT tenant_packs_pkey PRIMARY KEY (tenant_id, pack);

ALTER TABLE ONLY core.tenant_profile
    ADD CONSTRAINT tenant_profile_pkey PRIMARY KEY (tenant_id);

ALTER TABLE ONLY core.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);

ALTER TABLE ONLY core.users
    ADD CONSTRAINT users_email_key UNIQUE (email);

ALTER TABLE ONLY core.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

CREATE INDEX idx_ai_usage_at ON core.ai_usage USING btree (tenant_id, at DESC);

CREATE INDEX idx_approvals_pending ON core.approvals USING btree (tenant_id, status);

CREATE INDEX idx_audit_tenant_at ON core.audit_log USING btree (tenant_id, at DESC);

CREATE INDEX idx_chat_threads_user ON core.chat_threads USING btree (tenant_id, user_id, updated_at DESC);

CREATE INDEX idx_deliveries_due ON core.deliveries USING btree (next_at) WHERE (status = 'pending'::text);

CREATE INDEX idx_devices_token ON core.devices USING btree (token, last_seen_at DESC);

CREATE INDEX idx_notifications_inbox ON core.notifications USING btree (tenant_id, user_id, created_at DESC);

CREATE INDEX idx_notifications_push ON core.notifications USING btree (id) WHERE (push_state = 'pending'::text);

CREATE INDEX idx_proposals_open ON core.proposals USING btree (tenant_id, user_id, day) WHERE (status = 'open'::text);

CREATE INDEX idx_public_links_ref ON core.public_links USING btree (tenant_id, target, ref_id);

CREATE INDEX idx_sessions_user ON core.sessions USING btree (user_id) WHERE (revoked_at IS NULL);

CREATE INDEX idx_team_members_team ON core.team_members USING btree (team_id);

CREATE UNIQUE INDEX uq_notifications_dedupe ON core.notifications USING btree (tenant_id, user_id, dedupe_key) WHERE (dedupe_key IS NOT NULL);

CREATE TRIGGER trg_notifications_new AFTER INSERT ON core.notifications FOR EACH ROW EXECUTE FUNCTION core.notify_new();

ALTER TABLE ONLY core.agent_policies
    ADD CONSTRAINT agent_policies_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.ai_usage
    ADD CONSTRAINT ai_usage_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY core.ai_usage
    ADD CONSTRAINT ai_usage_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.ai_usage
    ADD CONSTRAINT ai_usage_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id);

ALTER TABLE ONLY core.approval_chains
    ADD CONSTRAINT approval_chains_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.approval_rules
    ADD CONSTRAINT approval_rules_tenant_id_chain_fkey FOREIGN KEY (tenant_id, chain) REFERENCES core.approval_chains(tenant_id, key) ON DELETE CASCADE;

ALTER TABLE ONLY core.approvals
    ADD CONSTRAINT approvals_requested_by_fkey FOREIGN KEY (requested_by) REFERENCES core.users(id);

ALTER TABLE ONLY core.approvals
    ADD CONSTRAINT approvals_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.audit_log
    ADD CONSTRAINT audit_log_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.chat_threads
    ADD CONSTRAINT chat_threads_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.chat_threads
    ADD CONSTRAINT chat_threads_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.deliveries
    ADD CONSTRAINT deliveries_dismissed_by_fkey FOREIGN KEY (dismissed_by) REFERENCES core.users(id);

ALTER TABLE ONLY core.deliveries
    ADD CONSTRAINT deliveries_event_id_fkey FOREIGN KEY (event_id) REFERENCES core.events(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.deliveries
    ADD CONSTRAINT deliveries_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.devices
    ADD CONSTRAINT devices_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.devices
    ADD CONSTRAINT devices_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.events
    ADD CONSTRAINT events_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.grants
    ADD CONSTRAINT grants_tenant_id_role_fkey FOREIGN KEY (tenant_id, role) REFERENCES core.roles(tenant_id, key) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY core.idempotency
    ADD CONSTRAINT idempotency_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.identities
    ADD CONSTRAINT identities_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.identity_groups
    ADD CONSTRAINT identity_groups_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.memberships
    ADD CONSTRAINT memberships_provided_by_fkey FOREIGN KEY (provided_by) REFERENCES core.tenants(id) ON DELETE SET NULL;

ALTER TABLE ONLY core.memberships
    ADD CONSTRAINT memberships_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.memberships
    ADD CONSTRAINT memberships_tenant_id_role_fkey FOREIGN KEY (tenant_id, role) REFERENCES core.roles(tenant_id, key) ON UPDATE CASCADE;

ALTER TABLE ONLY core.memberships
    ADD CONSTRAINT memberships_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.notifications
    ADD CONSTRAINT notifications_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.notifications
    ADD CONSTRAINT notifications_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.pack_installs
    ADD CONSTRAINT pack_installs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.proposal_runs
    ADD CONSTRAINT proposal_runs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.proposals
    ADD CONSTRAINT proposals_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.proposals
    ADD CONSTRAINT proposals_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.public_links
    ADD CONSTRAINT public_links_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY core.public_links
    ADD CONSTRAINT public_links_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.roles
    ADD CONSTRAINT roles_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.team_members
    ADD CONSTRAINT team_members_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.team_members
    ADD CONSTRAINT team_members_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.team_members
    ADD CONSTRAINT team_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.teams
    ADD CONSTRAINT teams_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES core.teams(id);

ALTER TABLE ONLY core.teams
    ADD CONSTRAINT teams_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.tenant_entitlements
    ADD CONSTRAINT tenant_entitlements_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.tenant_packs
    ADD CONSTRAINT tenant_packs_enabled_by_fkey FOREIGN KEY (enabled_by) REFERENCES core.users(id);

ALTER TABLE ONLY core.tenant_packs
    ADD CONSTRAINT tenant_packs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.tenant_profile
    ADD CONSTRAINT tenant_profile_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY core.tenant_profile
    ADD CONSTRAINT tenant_profile_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES core.users(id);

ALTER TABLE ONLY core.tenants
    ADD CONSTRAINT tenants_provisioned_by_fkey FOREIGN KEY (provisioned_by) REFERENCES core.tenants(id);

ALTER TABLE core.agent_policies ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.ai_usage ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.approval_chains ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.approval_rules ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.approvals ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.audit_log ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.chat_threads ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.deliveries ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.devices ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.events ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.grants ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.idempotency ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.identities ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.identity_groups ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.memberships ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_approval ON core.approvals USING (((tenant_id = core.tenant()) AND ((requested_by = core.me()) OR (core.me() = ANY (participant_ids)) OR (core.scope('core.approvals'::text) = 'all'::text)))) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_device_own ON core.devices USING (((tenant_id = core.tenant()) AND (user_id = core.me()))) WITH CHECK (((tenant_id = core.tenant()) AND (user_id = core.me())));

CREATE POLICY p_identity ON core.identities USING (((user_id = core.me()) OR ((core.scope('core.admin'::text) = 'all'::text) AND (EXISTS ( SELECT 1
   FROM core.memberships m
  WHERE ((m.user_id = identities.user_id) AND (m.tenant_id = core.tenant())))))));

CREATE POLICY p_mark ON core.notifications FOR UPDATE USING (((tenant_id = core.tenant()) AND (user_id = core.me()))) WITH CHECK (((tenant_id = core.tenant()) AND (user_id = core.me())));

CREATE POLICY p_member ON core.users FOR SELECT USING ((EXISTS ( SELECT 1
   FROM core.memberships m
  WHERE ((m.user_id = users.id) AND (m.tenant_id = core.tenant())))));

CREATE POLICY p_mine ON core.notifications FOR SELECT USING (((tenant_id = core.tenant()) AND (user_id = core.me())));

CREATE POLICY p_no_link ON core.agent_policies AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.approval_chains AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.approval_rules AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.grants AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.idempotency AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.identity_groups AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.memberships AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.pack_installs AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.proposal_runs AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.public_links AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.roles AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.team_members AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.teams AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.tenant_entitlements AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.tenant_profile AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON core.users AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_own ON core.chat_threads USING (((tenant_id = core.tenant()) AND (user_id = core.me()))) WITH CHECK (((tenant_id = core.tenant()) AND (user_id = core.me())));

CREATE POLICY p_own ON core.proposals USING (((tenant_id = core.tenant()) AND (user_id = core.me()))) WITH CHECK (((tenant_id = core.tenant()) AND (user_id = core.me())));

CREATE POLICY p_read ON core.ai_usage FOR SELECT USING (((tenant_id = core.tenant()) AND core.can_see('core.ai_usage'::text, user_id, team_id)));

CREATE POLICY p_read ON core.audit_log FOR SELECT USING (((tenant_id = core.tenant()) AND core.can_see('core.audit'::text, user_id, NULL::uuid)));

CREATE POLICY p_read ON core.subject_kinds FOR SELECT USING (true);

CREATE POLICY p_send ON core.notifications FOR INSERT WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM core.memberships m
  WHERE ((m.user_id = notifications.user_id) AND m.active)))));

CREATE POLICY p_session ON core.sessions USING (((user_id = core.me()) OR ((core.scope('core.admin'::text) = 'all'::text) AND (EXISTS ( SELECT 1
   FROM core.memberships m
  WHERE ((m.user_id = sessions.user_id) AND (m.tenant_id = core.tenant())))))));

CREATE POLICY p_tenant ON core.agent_policies USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.approval_chains USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.approval_rules USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.deliveries USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.events USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.grants USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.idempotency USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.identity_groups USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.memberships USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.pack_installs USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.proposal_runs USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.public_links USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.roles USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.team_members USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.teams USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.tenant_entitlements USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.tenant_packs USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.tenant_profile USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON core.tenants USING ((id = core.tenant()));

CREATE POLICY p_update ON core.users FOR UPDATE USING (((id = core.me()) OR ((core.scope('core.admin'::text) = 'all'::text) AND (EXISTS ( SELECT 1
   FROM core.memberships m
  WHERE ((m.user_id = users.id) AND (m.tenant_id = core.tenant())))))));

CREATE POLICY p_write ON core.ai_usage FOR INSERT WITH CHECK (((tenant_id = core.tenant()) AND (user_id = core.me())));

CREATE POLICY p_write ON core.audit_log FOR INSERT WITH CHECK ((tenant_id = core.tenant()));

ALTER TABLE core.pack_installs ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.proposal_runs ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.proposals ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.public_links ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.roles ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.sessions ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.subject_kinds ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.team_members ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.teams ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.tenant_entitlements ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.tenant_packs ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.tenant_profile ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.tenants ENABLE ROW LEVEL SECURITY;

ALTER TABLE core.users ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA core TO barista_app;

REVOKE ALL ON FUNCTION core.ensure_user(p_email text, p_name text) FROM PUBLIC;
GRANT ALL ON FUNCTION core.ensure_user(p_email text, p_name text) TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE core.agent_policies TO barista_app;

GRANT SELECT,INSERT ON TABLE core.ai_usage TO barista_app;

GRANT USAGE ON SEQUENCE core.ai_usage_id_seq TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE core.approval_chains TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE core.approval_rules TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE core.approvals TO barista_app;

GRANT SELECT,INSERT ON TABLE core.audit_log TO barista_app;

GRANT USAGE ON SEQUENCE core.audit_log_id_seq TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE core.chat_threads TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE core.deliveries TO barista_app;

GRANT USAGE ON SEQUENCE core.deliveries_id_seq TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE core.devices TO barista_app;

GRANT SELECT,INSERT ON TABLE core.events TO barista_app;

GRANT USAGE ON SEQUENCE core.events_id_seq TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE core.grants TO barista_app;

GRANT SELECT,INSERT ON TABLE core.idempotency TO barista_app;

GRANT SELECT,DELETE ON TABLE core.identities TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE core.identity_groups TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE core.memberships TO barista_app;

GRANT SELECT,INSERT ON TABLE core.notifications TO barista_app;

GRANT UPDATE(read_at) ON TABLE core.notifications TO barista_app;

GRANT USAGE ON SEQUENCE core.notifications_id_seq TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE core.pack_installs TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE core.proposal_runs TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE core.proposals TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE core.public_links TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE core.roles TO barista_app;

GRANT SELECT ON TABLE core.sessions TO barista_app;

GRANT UPDATE(revoked_at) ON TABLE core.sessions TO barista_app;

GRANT SELECT ON TABLE core.subject_kinds TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE core.team_members TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE core.teams TO barista_app;

GRANT SELECT ON TABLE core.tenant_entitlements TO barista_app;

GRANT SELECT,INSERT,DELETE ON TABLE core.tenant_packs TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE core.tenant_profile TO barista_app;

GRANT SELECT ON TABLE core.tenants TO barista_app;

GRANT SELECT ON TABLE core.users TO barista_app;

GRANT UPDATE(name) ON TABLE core.users TO barista_app;

GRANT UPDATE(phone) ON TABLE core.users TO barista_app;

GRANT UPDATE(birthday) ON TABLE core.users TO barista_app;
