-- module: talk
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (năng lực chung — talk).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA talk;

CREATE FUNCTION talk.notify_message() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM pg_notify('barista_talk', 'thread:' || NEW.thread_id::text);
    RETURN NEW;
END $$;

CREATE FUNCTION talk.subject_visible_room(id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $_$ SELECT EXISTS (SELECT 1 FROM talk.room_members m WHERE m.room_id = $1 AND m.user_id = core.me()) $_$;

CREATE TABLE talk.members (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    thread_id uuid NOT NULL,
    user_id uuid NOT NULL,
    following boolean DEFAULT true NOT NULL,
    muted boolean DEFAULT false NOT NULL,
    last_read_id bigint DEFAULT 0 NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    joined_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT members_reason_check CHECK ((reason = ANY (ARRAY[''::text, 'posted'::text, 'mentioned'::text, 'follow'::text])))
);

ALTER TABLE ONLY talk.members FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE talk.members IS 'parent=talk.threads';

CREATE TABLE talk.messages (
    id bigint NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    thread_id uuid NOT NULL,
    author_id uuid NOT NULL,
    body text NOT NULL,
    mentions uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    card jsonb,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT messages_body_check CHECK (((btrim(body) <> ''::text) AND (length(body) <= 4000)))
);

ALTER TABLE ONLY talk.messages FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE talk.messages IS 'parent=talk.threads';

CREATE SEQUENCE talk.messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE talk.messages_id_seq OWNED BY talk.messages.id;

CREATE TABLE talk.room_members (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    room_id uuid NOT NULL,
    user_id uuid NOT NULL,
    is_owner boolean DEFAULT false NOT NULL,
    added_by uuid,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY talk.room_members FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE talk.room_members IS 'resource=talk';

CREATE TABLE talk.rooms (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    name text NOT NULL,
    purpose text DEFAULT ''::text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT rooms_name_check CHECK (((btrim(name) <> ''::text) AND (length(name) <= 80))),
    CONSTRAINT rooms_purpose_check CHECK ((length(purpose) <= 300))
);

ALTER TABLE ONLY talk.rooms FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE talk.rooms IS 'resource=talk';

CREATE TABLE talk.threads (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    subject_kind text NOT NULL,
    subject_id uuid NOT NULL,
    title text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_message_at timestamp with time zone,
    last_message_id bigint,
    CONSTRAINT threads_title_check CHECK ((btrim(title) <> ''::text))
);

ALTER TABLE ONLY talk.threads FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE talk.threads IS 'resource=talk';

ALTER TABLE ONLY talk.messages ALTER COLUMN id SET DEFAULT nextval('talk.messages_id_seq'::regclass);

ALTER TABLE ONLY talk.members
    ADD CONSTRAINT members_pkey PRIMARY KEY (thread_id, user_id);

ALTER TABLE ONLY talk.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY talk.room_members
    ADD CONSTRAINT room_members_pkey PRIMARY KEY (room_id, user_id);

ALTER TABLE ONLY talk.rooms
    ADD CONSTRAINT rooms_pkey PRIMARY KEY (id);

ALTER TABLE ONLY talk.threads
    ADD CONSTRAINT threads_pkey PRIMARY KEY (id);

ALTER TABLE ONLY talk.threads
    ADD CONSTRAINT threads_tenant_id_subject_kind_subject_id_key UNIQUE (tenant_id, subject_kind, subject_id);

CREATE INDEX idx_talk_members_user ON talk.members USING btree (tenant_id, user_id) WHERE following;

CREATE INDEX idx_talk_messages_thread ON talk.messages USING btree (thread_id, id);

CREATE INDEX idx_talk_room_members_user ON talk.room_members USING btree (tenant_id, user_id);

CREATE TRIGGER trg_talk_message AFTER INSERT ON talk.messages FOR EACH ROW EXECUTE FUNCTION talk.notify_message();

ALTER TABLE ONLY talk.members
    ADD CONSTRAINT members_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY talk.members
    ADD CONSTRAINT members_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES talk.threads(id) ON DELETE CASCADE;

ALTER TABLE ONLY talk.members
    ADD CONSTRAINT members_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY talk.messages
    ADD CONSTRAINT messages_author_id_fkey FOREIGN KEY (author_id) REFERENCES core.users(id);

ALTER TABLE ONLY talk.messages
    ADD CONSTRAINT messages_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY talk.messages
    ADD CONSTRAINT messages_thread_id_fkey FOREIGN KEY (thread_id) REFERENCES talk.threads(id) ON DELETE CASCADE;

ALTER TABLE ONLY talk.room_members
    ADD CONSTRAINT room_members_added_by_fkey FOREIGN KEY (added_by) REFERENCES core.users(id);

ALTER TABLE ONLY talk.room_members
    ADD CONSTRAINT room_members_room_id_fkey FOREIGN KEY (room_id) REFERENCES talk.rooms(id) ON DELETE CASCADE;

ALTER TABLE ONLY talk.room_members
    ADD CONSTRAINT room_members_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY talk.room_members
    ADD CONSTRAINT room_members_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id) ON DELETE CASCADE;

ALTER TABLE ONLY talk.rooms
    ADD CONSTRAINT rooms_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY talk.rooms
    ADD CONSTRAINT rooms_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY talk.threads
    ADD CONSTRAINT threads_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY talk.threads
    ADD CONSTRAINT threads_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE talk.members ENABLE ROW LEVEL SECURITY;

ALTER TABLE talk.messages ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_no_link ON talk.room_members AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON talk.rooms AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_scope ON talk.members USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM talk.threads p
  WHERE (p.id = members.thread_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM talk.threads p
  WHERE (p.id = members.thread_id)))));

CREATE POLICY p_scope ON talk.messages USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM talk.threads p
  WHERE (p.id = messages.thread_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM talk.threads p
  WHERE (p.id = messages.thread_id)))));

CREATE POLICY p_subject ON talk.threads USING (((tenant_id = core.tenant()) AND core.subject_visible(subject_kind, subject_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.subject_visible(subject_kind, subject_id)));

CREATE POLICY p_tenant ON talk.room_members USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON talk.rooms USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

ALTER TABLE talk.room_members ENABLE ROW LEVEL SECURITY;

ALTER TABLE talk.rooms ENABLE ROW LEVEL SECURITY;

ALTER TABLE talk.threads ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA talk TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE talk.members TO barista_app;

GRANT UPDATE(following) ON TABLE talk.members TO barista_app;

GRANT SELECT,INSERT ON TABLE talk.messages TO barista_app;

GRANT USAGE ON SEQUENCE talk.messages_id_seq TO barista_app;

GRANT SELECT,INSERT,DELETE ON TABLE talk.room_members TO barista_app;

GRANT SELECT,INSERT ON TABLE talk.rooms TO barista_app;

GRANT UPDATE(name) ON TABLE talk.rooms TO barista_app;

GRANT UPDATE(purpose) ON TABLE talk.rooms TO barista_app;

GRANT SELECT,INSERT ON TABLE talk.threads TO barista_app;

GRANT UPDATE(title) ON TABLE talk.threads TO barista_app;

GRANT UPDATE(last_message_at) ON TABLE talk.threads TO barista_app;

GRANT UPDATE(last_message_id) ON TABLE talk.threads TO barista_app;

-- Dữ liệu tham chiếu bắt buộc: loại đối tượng của module
INSERT INTO core.subject_kinds (kind, visible_fn, pack) VALUES ('talk.room', 'talk.subject_visible_room', 'talk');
