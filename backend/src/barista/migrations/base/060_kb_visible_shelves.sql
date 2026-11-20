-- module: kb — lý do: hiệu năng (perf doc §13). Luật "người này đọc được kệ nào" tính MỘT LẦN mỗi câu lệnh (InitPlan `(SELECT kb.visible_shelves())`)
-- thay vì `kb.shelf_visible(shelf)` chạy cho từng tài liệu / đoạn tài liệu — mỗi lần một truy vấn đệ quy cây đội (đo 10/11: ~6.200 lần mỗi lượt
-- kb.search trong khi công ty có 3 kệ). Luật không đổi: thấy tài liệu ⇔ (quyền hệ thống hoặc đọc được kệ của nó) và các vế khác giữ nguyên.
-- Không bảo trì: tên hàm cũ giữ nguyên, chính sách mới cho đúng các dòng như cũ.

-- MỘT nơi định nghĩa luật đọc kệ: kệ mở cho mọi người, có tên người, có vai của người, hoặc gắn một đội mà người là thành viên (kể cả đội con).
CREATE FUNCTION kb.visible_shelves() RETURNS text[]
    LANGUAGE sql STABLE
    AS $$
    SELECT coalesce(array_agg(s.key), '{}'::text[]) FROM kb.shelves s
    WHERE s.tenant_id = core.tenant() AND (
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
            SELECT 1 FROM up WHERE up.id = ANY (s.team_ids)))
$$;
REVOKE ALL ON FUNCTION kb.visible_shelves() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kb.visible_shelves() TO barista_app;

-- Hàm cũ (mã Python của gói kb gọi trên kb.shelves, vài dòng) chỉ còn là "k thuộc tập" — không còn bản thứ hai của luật.
CREATE OR REPLACE FUNCTION kb.shelf_visible(k text) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT coalesce(k = ANY (kb.visible_shelves()), false) $$;

-- Chính sách gọi kb.shelf_visible(cột) theo dòng ⇒ InitPlan một lần mỗi câu. Chạy lại không sao (chỉ đụng chữ còn gọi hàm theo dòng).
DO $$
DECLARE
    r record;
    pat constant text := 'kb\.shelf_visible\(([a-z_.]+)\)';
    rep constant text := 'coalesce(\1 = ANY ((SELECT kb.visible_shelves())::text[]), false)';
    q text;
    w text;
BEGIN
    FOR r IN SELECT p.polname, p.polrelid, pg_get_expr(p.polqual, p.polrelid) AS qual, pg_get_expr(p.polwithcheck, p.polrelid) AS wc
             FROM pg_policy p
             WHERE pg_get_expr(p.polqual, p.polrelid) ~ 'kb\.shelf_visible\(' OR pg_get_expr(p.polwithcheck, p.polrelid) ~ 'kb\.shelf_visible\('
    LOOP
        q := regexp_replace(r.qual, pat, rep, 'g');
        w := regexp_replace(r.wc, pat, rep, 'g');
        IF q IS NOT NULL AND w IS NOT NULL THEN
            EXECUTE format('ALTER POLICY %I ON %s USING (%s) WITH CHECK (%s)', r.polname, r.polrelid::regclass, q, w);
        ELSIF q IS NOT NULL THEN
            EXECUTE format('ALTER POLICY %I ON %s USING (%s)', r.polname, r.polrelid::regclass, q);
        ELSE
            EXECUTE format('ALTER POLICY %I ON %s WITH CHECK (%s)', r.polname, r.polrelid::regclass, w);
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_policy p WHERE pg_get_expr(p.polqual, p.polrelid) ~ 'kb\.shelf_visible\('
                                           OR pg_get_expr(p.polwithcheck, p.polrelid) ~ 'kb\.shelf_visible\(') THEN
        RAISE EXCEPTION 'còn chính sách gọi kb.shelf_visible theo dòng';
    END IF;
END $$;
