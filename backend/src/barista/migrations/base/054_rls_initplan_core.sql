-- module: core, flow — lý do: N11 (flow: đường tắt F3 cho chính sách flow.tasks) (đo 08/11, ghi chú nội bộ): ở quy mô khách đầu tiên, chính sách RLS tính lại biến phiên ở TỪNG DÒNG
-- (core.can_see / core.scope parse lại JSON quyền mỗi dòng; core.tenant() / core.me() cũng vậy) và gọi hàm không inline được
-- (core.subject_visible plpgsql + EXECUTE động; flow.case_on DEFINER) cho từng dòng ⇒ nhiều màn 1–17 giây.
-- CHỈ ĐỔI CÁCH VIẾT, KHÔNG ĐỔI NGHĨA chính sách nào (đối chiếu số dòng thấy được trước / sau: tài liệu đo §7):
--   F2. Biến phiên thành InitPlan ``(SELECT core.x())`` ⇒ tính MỘT lần mỗi câu lệnh. Biến phiên đặt trước câu và không đổi trong câu, nên một lần
--       là đúng. core.can_see(...) được viết thẳng thành CASE trong chữ chính sách (gói trong hàm thì thân có sublink ⇒ không inline được).
--       core.rls_initplan(text) là phép viết lại thuần chữ; core.initplan_policies(schemas) áp cho mọi chính sách của các schema đó;
--       secure_table / secure_child / secure_tenant từ nay sinh thẳng dạng mới. Gói nào có bảng ⇒ migration 054 của gói gọi
--       core.initplan_policies cho schema của mình (bản cài mới: gói chạy SAU nền; bản nâng cấp: chạy lại không sao — chữ đã đúng thì bỏ qua).
--       tests/test_rls_initplan.py chặn chính sách nào còn gọi biến phiên trần.
--   F1. core.subject_visible thành hàm SQL inline được: CASE theo loại, sinh từ core.subject_kinds (kernel không nhắc tên module — 0069);
--       bảng đổi ⇒ trigger sinh lại. Cùng nghĩa: loại tắt / không đăng ký / hàm trả NULL ⇒ false.
--   F3. flow.tasks p_pack_on: thêm đường tắt tính MỘT lần mỗi câu — công ty không có hồ sơ nào thuộc loại đối tượng đang tắt ⇒ mọi hồ sơ đều bật,
--       không cần gọi flow.case_on (DEFINER, không inline được) cho từng việc. flow.cases_all_on() là ĐÚNG phép kiểm của case_on gộp trên mọi hồ sơ
--       của công ty (cũng DEFINER như case_on, vì hồ sơ người xem không thấy vẫn quyết việc của nó có bị ẩn hay không) — không mở thêm dòng nào.
-- Không cần bảo trì: nghĩa chính sách giữ nguyên ⇒ bản cũ chạy song song thấy đúng như trước. ALTER POLICY giữ khoá ACCESS EXCLUSIVE từng bảng tới
-- cuối giao dịch ⇒ lock_timeout: đụng câu dài thì hỏng nhanh (phát hành tự quay lui, chạy lại) thay vì xếp hàng chặn mọi request.
SET LOCAL lock_timeout = '10s';

-- ── F2: viết lại chữ chính sách ──
CREATE FUNCTION core.rls_initplan(e text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $fn$
DECLARE
    can CONSTANT text := $r$core\.can_see\('([a-z0-9_.]+)'(?:::text)?, ([a-z0-9_.]+|NULL::uuid), ([a-z0-9_.]+|NULL::uuid)\)$r$;
    m text[];
    own text;
    tm text;
    un text;
BEGIN
    IF e IS NULL THEN
        RETURN NULL;
    END IF;
    LOOP
        m := regexp_match(e, '(' || can || ')');
        EXIT WHEN m IS NULL;
        own := CASE WHEN m[3] = 'NULL::uuid' THEN 'false' ELSE format('(%1$s IS NOT NULL AND %1$s = (SELECT core.me()))', m[3]) END;
        tm := CASE WHEN m[4] = 'NULL::uuid' THEN 'false'
                   ELSE format('(%1$s IS NOT NULL AND %1$s = ANY ((SELECT core.my_teams())::uuid[]))', m[4]) END;
        un := CASE WHEN m[4] = 'NULL::uuid' THEN 'false'
                   ELSE format('(%1$s IS NOT NULL AND %1$s = ANY ((SELECT core.my_units())::uuid[]))', m[4]) END;
        e := replace(e, m[1], format('(CASE (SELECT core.scope(%L)) WHEN ''all'' THEN true WHEN ''team'' THEN (%s OR %s) '
                                     'WHEN ''unit'' THEN (%s OR %s) WHEN ''own'' THEN %s ELSE false END)', m[2], own, tm, own, un, own));
    END LOOP;
    -- gọi trần (chưa nằm trong SELECT) ⇒ InitPlan; mảng đội ép kiểu để "= ANY ((SELECT …)::uuid[])" là biểu thức mảng, không phải subquery
    e := regexp_replace(e, $r$(?<!SELECT )core\.scope\(('[a-z0-9_.]+'(?:::text)?)\)$r$, '(SELECT core.scope(\1))', 'g');
    e := regexp_replace(e, $r$(?<!SELECT )core\.(my_teams|my_units)\(\)$r$, '(SELECT core.\1())::uuid[]', 'g');
    e := regexp_replace(e, $r$(?<!SELECT )core\.(tenant|me|link_ref|is_system|my_email)\(\)$r$, '(SELECT core.\1())', 'g');
    RETURN e;
END $fn$;

CREATE PROCEDURE core.initplan_policies(IN schemas text[])
    LANGUAGE plpgsql
    AS $fn$
DECLARE
    r record;
    q text;
    w text;
BEGIN
    FOR r IN SELECT p.polname, format('%I.%I', n.nspname, c.relname) AS tbl,
                    pg_get_expr(p.polqual, p.polrelid) AS q, pg_get_expr(p.polwithcheck, p.polrelid) AS w
             FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = ANY (schemas) ORDER BY 2, 1
    LOOP
        q := core.rls_initplan(r.q);
        w := core.rls_initplan(r.w);
        IF q IS DISTINCT FROM r.q OR w IS DISTINCT FROM r.w THEN
            EXECUTE format('ALTER POLICY %I ON %s', r.polname, r.tbl)
                 || coalesce(' USING (' || q || ')', '') || coalesce(' WITH CHECK (' || w || ')', '');
        END IF;
    END LOOP;
END $fn$;

CREATE OR REPLACE PROCEDURE core.secure_child(IN tbl regclass, IN parent regclass, IN fk_col text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    cond TEXT := core.rls_initplan(format('tenant_id = core.tenant() AND EXISTS (SELECT 1 FROM %s p WHERE p.id = %s)', parent, quote_ident(fk_col)));
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format('CREATE POLICY p_scope ON %s USING (%s) WITH CHECK (%s)', tbl, cond, cond);
    EXECUTE format('COMMENT ON TABLE %s IS %L', tbl, 'parent=' || parent::text);
END $$;

CREATE OR REPLACE PROCEDURE core.secure_table(IN tbl regclass, IN resource text, IN owner_col text DEFAULT NULL::text, IN team_col text DEFAULT NULL::text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    cond TEXT := core.rls_initplan(format('tenant_id = core.tenant() AND core.can_see(%L, %s, %s)', resource,
                                          coalesce(quote_ident(owner_col), 'NULL::uuid'), coalesce(quote_ident(team_col), 'NULL::uuid')));
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format('CREATE POLICY p_scope ON %s USING (%s) WITH CHECK (%s)', tbl, cond, cond);
    EXECUTE format('COMMENT ON TABLE %s IS %L', tbl, 'resource=' || resource);
END $$;

CREATE OR REPLACE PROCEDURE core.secure_tenant(IN tbl regclass)
    LANGUAGE plpgsql
    AS $$
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format('CREATE POLICY p_tenant ON %s USING (tenant_id = (SELECT core.tenant())) WITH CHECK (tenant_id = (SELECT core.tenant()))', tbl);
    EXECUTE format('CREATE POLICY p_no_link ON %s AS RESTRICTIVE FOR SELECT USING ((SELECT core.link_ref()) IS NULL)', tbl);
END $$;

-- ── F1: core.subject_visible inline được, sinh từ sổ loại đối tượng ──
-- Thân KHÔNG kiểm lúc tạo (check_function_bodies off): gói có thể đăng ký loại trước khi tạo hàm thấy của nó trong cùng migration.
CREATE FUNCTION core.compile_subject_visible() RETURNS void
    LANGUAGE plpgsql
    SET check_function_bodies = off
    AS $fn$
DECLARE
    arms text;
BEGIN
    SELECT string_agg(format('WHEN %L THEN coalesce(%s(id), false)', s.kind, s.visible_fn), ' ' ORDER BY s.kind) INTO arms
    FROM core.subject_kinds s;
    EXECUTE format('CREATE OR REPLACE FUNCTION core.subject_visible(kind text, id uuid) RETURNS boolean LANGUAGE sql STABLE AS %L',
                   CASE WHEN arms IS NULL THEN 'SELECT false'
                        ELSE format('SELECT core.subject_on(kind) AND CASE kind %s ELSE false END', arms) END);
END $fn$;

CREATE FUNCTION core.subject_kinds_changed() RETURNS trigger
    LANGUAGE plpgsql
    AS $fn$
BEGIN
    PERFORM core.compile_subject_visible();
    RETURN NULL;
END $fn$;

-- plpgsql ⇒ sql: đổi ngôn ngữ cần DROP; chính sách đang dùng hàm ⇒ không DROP được ⇒ CREATE OR REPLACE giữ nguyên chữ ký (sql thay plpgsql được).
SELECT core.compile_subject_visible();
CREATE TRIGGER t_compile AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON core.subject_kinds
    FOR EACH STATEMENT EXECUTE FUNCTION core.subject_kinds_changed();

-- ── F3: đường tắt flow.case_on ──
CREATE FUNCTION flow.cases_all_on() RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$ SELECT NOT EXISTS (SELECT 1 FROM core.subject_kinds s
                             WHERE NOT core.subject_on(s.kind)
                               AND EXISTS (SELECT 1 FROM flow.cases c WHERE c.tenant_id = core.tenant() AND c.subject_kind = s.kind)) $$;
REVOKE ALL ON FUNCTION flow.cases_all_on() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION flow.cases_all_on() TO barista_app;

ALTER POLICY p_pack_on ON flow.tasks USING ((case_id IS NULL) OR (SELECT flow.cases_all_on()) OR flow.case_on(case_id));

CALL core.initplan_policies(ARRAY['apps', 'asks', 'compose', 'core', 'flow', 'kb', 'ledger', 'memory', 'papers', 'work']);
