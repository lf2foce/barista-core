-- module: core — lý do: hiệu năng sau N11b (ADR 0146, perf doc §11). "Gói của loại đối tượng có bật không" tính MỘT LẦN mỗi câu lệnh ở chính sách
-- (InitPlan `(SELECT core.kinds_off())`) thay vì `core.subject_on(kind)` chạy từng dòng (mỗi lần một EXISTS core.tenant_packs qua RLS, 2–3 lần đọc
-- claims). Luật không đổi: thấy luồng/cuộc gọi/yêu cầu/ký ức gắn đối tượng ⇔ gói của đối tượng bật VÀ thấy đối tượng. Không bảo trì: chính sách mới
-- và hàm mới tương đương về dòng, app cũ chạy song song được.

-- Loại đối tượng mà gói của nó ĐANG TẮT ở công ty hiện tại. Loại thuộc gói nền và loại lạ (không trong sổ) không nằm đây ⇒ coi là bật (như core.subject_on).
CREATE FUNCTION core.kinds_off() RETURNS text[]
    LANGUAGE sql STABLE
    AS $$ SELECT coalesce(array_agg(s.kind), '{}'::text[]) FROM core.subject_kinds s
          WHERE s.pack <> 'base' AND NOT EXISTS (SELECT 1 FROM core.tenant_packs tp WHERE tp.tenant_id = core.tenant() AND tp.pack = s.pack) $$;

-- core.subject_visible chỉ còn hỏi gói sở hữu (inline được vào chính sách); phần "gói bật" chuyển ra chính sách dưới dạng InitPlan.
CREATE OR REPLACE FUNCTION core.compile_subject_visible() RETURNS void
    LANGUAGE plpgsql
    SET check_function_bodies = off
    AS $fn$
DECLARE
    arms text;
BEGIN
    SELECT string_agg(format('WHEN %L THEN coalesce(%s(id), false)', s.kind, s.visible_fn), ' ' ORDER BY s.kind) INTO arms
    FROM core.subject_kinds s;
    EXECUTE format('CREATE OR REPLACE FUNCTION core.subject_visible(kind text, id uuid) RETURNS boolean LANGUAGE sql STABLE AS %L',
                   CASE WHEN arms IS NULL THEN 'SELECT false' ELSE format('SELECT CASE kind %s ELSE false END', arms) END);
END $fn$;
SELECT core.compile_subject_visible();

-- Mọi chính sách gọi core.subject_visible(kind, id) thêm vế InitPlan "loại không thuộc gói đang tắt" (NULL kind ⇒ bật, như coalesce(kind,'')='' cũ).
-- Là HÀM chạy lại được (chỉ đụng chữ chưa có vế kinds_off): bản cài mới chạy migration nền TRƯỚC migration của gói nên chính sách của gói (talk.threads,
-- meet.calls…) sinh ra SAU 058 ⇒ mỗi gói gọi lại hàm này ở migration riêng (059). Thiếu bước đó là mất chặn "gói tắt" ở chính sách đó (test_rls_initplan bắt).
CREATE FUNCTION core.kinds_off_policies() RETURNS void
    LANGUAGE plpgsql
    AS $fn$
DECLARE
    r record;
    pat constant text := 'core\.subject_visible\(([a-z_.]+), ([a-z_.]+)\)';
    rep constant text := '(coalesce(NOT (\1 = ANY ((SELECT core.kinds_off())::text[])), true) AND core.subject_visible(\1, \2))';
    q text;
    w text;
BEGIN
    FOR r IN SELECT p.polname, p.polrelid,
                    pg_get_expr(p.polqual, p.polrelid) AS qual, pg_get_expr(p.polwithcheck, p.polrelid) AS wc
             FROM pg_policy p
             WHERE (pg_get_expr(p.polqual, p.polrelid) ~ 'core\.subject_visible\(' AND pg_get_expr(p.polqual, p.polrelid) !~ 'kinds_off')
                OR (pg_get_expr(p.polwithcheck, p.polrelid) ~ 'core\.subject_visible\(' AND pg_get_expr(p.polwithcheck, p.polrelid) !~ 'kinds_off')
    LOOP
        q := CASE WHEN r.qual ~ 'core\.subject_visible\(' AND r.qual !~ 'kinds_off' THEN regexp_replace(r.qual, pat, rep, 'g') ELSE r.qual END;
        w := CASE WHEN r.wc ~ 'core\.subject_visible\(' AND r.wc !~ 'kinds_off' THEN regexp_replace(r.wc, pat, rep, 'g') ELSE r.wc END;
        IF q IS NOT NULL AND w IS NOT NULL THEN
            EXECUTE format('ALTER POLICY %I ON %s USING (%s) WITH CHECK (%s)', r.polname, r.polrelid::regclass, q, w);
        ELSIF q IS NOT NULL THEN
            EXECUTE format('ALTER POLICY %I ON %s USING (%s)', r.polname, r.polrelid::regclass, q);
        ELSE
            EXECUTE format('ALTER POLICY %I ON %s WITH CHECK (%s)', r.polname, r.polrelid::regclass, w);
        END IF;
    END LOOP;
    -- không còn chính sách nào gọi subject_visible mà thiếu vế kinds_off (cả USING lẫn WITH CHECK)
    IF EXISTS (SELECT 1 FROM pg_policy p
               WHERE (pg_get_expr(p.polqual, p.polrelid) ~ 'core\.subject_visible\(' AND pg_get_expr(p.polqual, p.polrelid) !~ 'kinds_off')
                  OR (pg_get_expr(p.polwithcheck, p.polrelid) ~ 'core\.subject_visible\(' AND pg_get_expr(p.polwithcheck, p.polrelid) !~ 'kinds_off')) THEN
        RAISE EXCEPTION 'còn chính sách gọi core.subject_visible chưa có vế kinds_off';
    END IF;
END $fn$;
SELECT core.kinds_off_policies();
