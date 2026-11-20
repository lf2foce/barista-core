-- module: training
-- BẢN GỐC (baseline 05/11/2026, quyết định 0085) — schema cuối của chuỗi migration 001–186 cho module này (năng lực chung — training).
-- Sinh bằng pg_dump từ DB sạch; lịch sử từng bước: docs/migrations-history.md. KHÔNG sửa sau khi đã phát hành cho khách thật —
-- thay đổi tiếp theo là migration mới (001…).
SET LOCAL check_function_bodies = false;
SELECT pg_catalog.set_config('search_path', '', true);

CREATE SCHEMA training;

CREATE TABLE training.answers (
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    assignment_id uuid NOT NULL,
    question_id uuid NOT NULL,
    choice integer NOT NULL,
    answered_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT answers_choice_check CHECK ((choice >= 0))
);

ALTER TABLE ONLY training.answers FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE training.answers IS 'parent=training.assignments';

CREATE TABLE training.assignments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    course_id uuid NOT NULL,
    user_id uuid NOT NULL,
    team_id uuid,
    due_on date,
    status text DEFAULT 'assigned'::text NOT NULL,
    best_pct integer,
    attempts integer DEFAULT 0 NOT NULL,
    assigned_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    passed_at timestamp with time zone,
    CONSTRAINT assignments_best_pct_check CHECK (((best_pct >= 0) AND (best_pct <= 100))),
    CONSTRAINT assignments_check CHECK (((status = 'passed'::text) = (passed_at IS NOT NULL))),
    CONSTRAINT assignments_status_check CHECK ((status = ANY (ARRAY['assigned'::text, 'passed'::text, 'failed'::text])))
);

ALTER TABLE ONLY training.assignments FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE training.assignments IS 'resource=training';

CREATE TABLE training.attempts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    assignment_id uuid NOT NULL,
    correct integer NOT NULL,
    total integer NOT NULL,
    score_pct integer NOT NULL,
    passed boolean NOT NULL,
    wrong jsonb DEFAULT '[]'::jsonb NOT NULL,
    submitted_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attempts_score_pct_check CHECK (((score_pct >= 0) AND (score_pct <= 100))),
    CONSTRAINT attempts_total_check CHECK ((total > 0))
);

ALTER TABLE ONLY training.attempts FORCE ROW LEVEL SECURITY;

COMMENT ON TABLE training.attempts IS 'parent=training.assignments';

CREATE TABLE training.courses (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    code text NOT NULL,
    title text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    pass_pct integer DEFAULT 80 NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT courses_code_check CHECK ((code ~ '^[A-Z0-9][A-Z0-9-]{1,30}$'::text)),
    CONSTRAINT courses_pass_pct_check CHECK (((pass_pct >= 1) AND (pass_pct <= 100))),
    CONSTRAINT courses_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'published'::text, 'archived'::text]))),
    CONSTRAINT courses_title_check CHECK ((btrim(title) <> ''::text))
);

ALTER TABLE ONLY training.courses FORCE ROW LEVEL SECURITY;

CREATE TABLE training.lessons (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    course_id uuid NOT NULL,
    seq integer NOT NULL,
    title text NOT NULL,
    document_id uuid,
    note text DEFAULT ''::text NOT NULL,
    CONSTRAINT lessons_title_check CHECK ((btrim(title) <> ''::text))
);

ALTER TABLE ONLY training.lessons FORCE ROW LEVEL SECURITY;

CREATE TABLE training.questions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id uuid DEFAULT core.tenant() NOT NULL,
    course_id uuid NOT NULL,
    prompt text NOT NULL,
    choices text[] NOT NULL,
    answer integer NOT NULL,
    explanation text DEFAULT ''::text NOT NULL,
    quote text DEFAULT ''::text NOT NULL,
    document_id uuid,
    cite text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    source text DEFAULT 'manual'::text NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT questions_check CHECK (((answer >= 0) AND (answer < cardinality(choices)))),
    CONSTRAINT questions_choices_check CHECK (((cardinality(choices) >= 2) AND (cardinality(choices) <= 6))),
    CONSTRAINT questions_prompt_check CHECK ((btrim(prompt) <> ''::text)),
    CONSTRAINT questions_source_check CHECK ((source = ANY (ARRAY['manual'::text, 'ai'::text]))),
    CONSTRAINT questions_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'retired'::text])))
);

ALTER TABLE ONLY training.questions FORCE ROW LEVEL SECURITY;

ALTER TABLE ONLY training.answers
    ADD CONSTRAINT answers_pkey PRIMARY KEY (assignment_id, question_id);

ALTER TABLE ONLY training.assignments
    ADD CONSTRAINT assignments_pkey PRIMARY KEY (id);

ALTER TABLE ONLY training.assignments
    ADD CONSTRAINT assignments_tenant_id_course_id_user_id_key UNIQUE (tenant_id, course_id, user_id);

ALTER TABLE ONLY training.attempts
    ADD CONSTRAINT attempts_pkey PRIMARY KEY (id);

ALTER TABLE ONLY training.courses
    ADD CONSTRAINT courses_pkey PRIMARY KEY (id);

ALTER TABLE ONLY training.courses
    ADD CONSTRAINT courses_tenant_id_code_key UNIQUE (tenant_id, code);

ALTER TABLE ONLY training.lessons
    ADD CONSTRAINT lessons_course_id_seq_key UNIQUE (course_id, seq);

ALTER TABLE ONLY training.lessons
    ADD CONSTRAINT lessons_pkey PRIMARY KEY (id);

ALTER TABLE ONLY training.questions
    ADD CONSTRAINT questions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY training.answers
    ADD CONSTRAINT answers_assignment_id_fkey FOREIGN KEY (assignment_id) REFERENCES training.assignments(id) ON DELETE CASCADE;

ALTER TABLE ONLY training.answers
    ADD CONSTRAINT answers_question_id_fkey FOREIGN KEY (question_id) REFERENCES training.questions(id);

ALTER TABLE ONLY training.answers
    ADD CONSTRAINT answers_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY training.assignments
    ADD CONSTRAINT assignments_assigned_by_fkey FOREIGN KEY (assigned_by) REFERENCES core.users(id);

ALTER TABLE ONLY training.assignments
    ADD CONSTRAINT assignments_course_id_fkey FOREIGN KEY (course_id) REFERENCES training.courses(id);

ALTER TABLE ONLY training.assignments
    ADD CONSTRAINT assignments_team_id_fkey FOREIGN KEY (team_id) REFERENCES core.teams(id);

ALTER TABLE ONLY training.assignments
    ADD CONSTRAINT assignments_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY training.assignments
    ADD CONSTRAINT assignments_user_id_fkey FOREIGN KEY (user_id) REFERENCES core.users(id);

ALTER TABLE ONLY training.attempts
    ADD CONSTRAINT attempts_assignment_id_fkey FOREIGN KEY (assignment_id) REFERENCES training.assignments(id) ON DELETE CASCADE;

ALTER TABLE ONLY training.attempts
    ADD CONSTRAINT attempts_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY training.courses
    ADD CONSTRAINT courses_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY training.courses
    ADD CONSTRAINT courses_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY training.lessons
    ADD CONSTRAINT lessons_course_id_fkey FOREIGN KEY (course_id) REFERENCES training.courses(id) ON DELETE CASCADE;

ALTER TABLE ONLY training.lessons
    ADD CONSTRAINT lessons_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE ONLY training.questions
    ADD CONSTRAINT questions_course_id_fkey FOREIGN KEY (course_id) REFERENCES training.courses(id) ON DELETE CASCADE;

ALTER TABLE ONLY training.questions
    ADD CONSTRAINT questions_created_by_fkey FOREIGN KEY (created_by) REFERENCES core.users(id);

ALTER TABLE ONLY training.questions
    ADD CONSTRAINT questions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES core.tenants(id) ON DELETE CASCADE;

ALTER TABLE training.answers ENABLE ROW LEVEL SECURITY;

ALTER TABLE training.assignments ENABLE ROW LEVEL SECURITY;

ALTER TABLE training.attempts ENABLE ROW LEVEL SECURITY;

ALTER TABLE training.courses ENABLE ROW LEVEL SECURITY;

ALTER TABLE training.lessons ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_no_link ON training.courses AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON training.lessons AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_no_link ON training.questions AS RESTRICTIVE FOR SELECT USING ((core.link_ref() IS NULL));

CREATE POLICY p_scope ON training.answers USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM training.assignments p
  WHERE (p.id = answers.assignment_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM training.assignments p
  WHERE (p.id = answers.assignment_id)))));

CREATE POLICY p_scope ON training.assignments USING (((tenant_id = core.tenant()) AND core.can_see('training'::text, user_id, team_id))) WITH CHECK (((tenant_id = core.tenant()) AND core.can_see('training'::text, user_id, team_id)));

CREATE POLICY p_scope ON training.attempts USING (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM training.assignments p
  WHERE (p.id = attempts.assignment_id))))) WITH CHECK (((tenant_id = core.tenant()) AND (EXISTS ( SELECT 1
   FROM training.assignments p
  WHERE (p.id = attempts.assignment_id)))));

CREATE POLICY p_tenant ON training.courses USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON training.lessons USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

CREATE POLICY p_tenant ON training.questions USING ((tenant_id = core.tenant())) WITH CHECK ((tenant_id = core.tenant()));

ALTER TABLE training.questions ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA training TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE training.answers TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE training.assignments TO barista_app;

GRANT SELECT,INSERT ON TABLE training.attempts TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE training.courses TO barista_app;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE training.lessons TO barista_app;

GRANT SELECT,INSERT,UPDATE ON TABLE training.questions TO barista_app;
