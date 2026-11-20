-- module: core — lý do: N11b (ADR 0146) — CLAIMS PHIÊN DO LÕI CẤP, SQL của gói (kể cả bị chèn) không đổi được công ty / người / quyền hệ thống.
-- Trước: chính sách RLS tin biến phiên app.* mà bất kỳ SQL nào chạy bằng barista_app cũng set_config được (đầu dò A — tests/test_pack_surface.py).
-- Sau: app gọi core.open_session(claims, mac) — hàm DEFINER của owner kiểm mac = HMAC-SHA256(khoá riêng, claims ‖ ':' ‖ pid) với khoá ở bảng
-- core.session_key mà barista_app không đọc được; bản đã kiểm ghi vào bảng TẠM do owner tạo (app chỉ SELECT); 8 hàm biến phiên đọc bản đó của ĐÚNG
-- giao dịch hiện tại (transaction_timestamp) và kiểm bảng do owner tạo. Không có / sai / cũ / bảng giả ⇒ NULL ⇒ đóng. set_config app.* không còn tác dụng.
-- Kiểm khi GHI (một lần mỗi lần mở / đổi phiên), đọc rẻ (một dòng bảng tạm, một lần mỗi câu nhờ dạng InitPlan của 054). Hàm giữ tên, chữ ký, STABLE.
-- BẢO TRÌ: bản app cũ còn chạy chỉ set_config ⇒ sau migration này bị đóng hoàn toàn ⇒ phải dừng dịch vụ (deployment.json).

-- Khoá riêng: chỉ owner đọc. RLS từ chối mọi dòng với app (dù có GRANT nhầm sau này).
CREATE TABLE core.session_key (
    id smallint PRIMARY KEY CHECK (id = 1),
    key bytea NOT NULL CHECK (octet_length(key) = 32),
    created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO core.session_key (id, key) VALUES (1, decode(replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', ''), 'hex'));
ALTER TABLE core.session_key ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.session_key FORCE ROW LEVEL SECURITY;
CREATE POLICY p_owner_only ON core.session_key USING (false) WITH CHECK (false);
REVOKE ALL ON core.session_key FROM PUBLIC;

-- HMAC-SHA256 bằng sha256 có sẵn (không cần pgcrypto): H((K ⊕ opad) ‖ H((K ⊕ ipad) ‖ m)), khối 64 byte.
CREATE FUNCTION core.hmac_sha256(key bytea, msg bytea) RETURNS bytea
    LANGUAGE plpgsql IMMUTABLE STRICT
    SET search_path = pg_catalog, pg_temp
    AS $$
DECLARE
    k bytea := key;
    ipad bytea := ''::bytea;
    opad bytea := ''::bytea;
    i integer;
BEGIN
    IF octet_length(k) > 64 THEN
        k := sha256(k);
    END IF;
    k := k || decode(repeat('00', 64 - octet_length(k)), 'hex');
    FOR i IN 0..63 LOOP
        ipad := ipad || set_byte('\x00'::bytea, 0, get_byte(k, i) # 54);
        opad := opad || set_byte('\x00'::bytea, 0, get_byte(k, i) # 92);
    END LOOP;
    RETURN sha256(opad || sha256(ipad || msg));
END $$;

-- MỞ / ĐỔI PHIÊN: chỉ lõi (giữ khoá trong tiến trình) gọi được; gọi lại trong cùng giao dịch (gọi lồng bằng quyền khác — executor.invoke) thì ghi đè.
-- Chữ ký phủ claims + PID backend + giờ ký (epoch giây): chữ ký của kết nối khác / quá 5 phút không dùng lại được.
-- search_path ghi pg_temp CUỐI và mọi bảng hệ thống ghi pg_catalog.*: không ghi thì pg_temp được tìm TRƯỚC cả pg_catalog ⇒ bảng tạm tên pg_class
-- của app quyết định kết quả kiểm chủ bảng (review 10/11).
CREATE FUNCTION core.open_session(claims text, mac text, ts bigint) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = pg_catalog, core, pg_temp
    AS $$
DECLARE
    secret bytea;   -- không đặt tên "k": trùng cột k của bảng tạm trong INSERT … ON CONFLICT (k)
    want text;
    j jsonb;
    existing regclass := to_regclass('pg_temp.barista_claims');
BEGIN
    SELECT key INTO secret FROM core.session_key WHERE id = 1;
    IF secret IS NULL THEN
        RAISE EXCEPTION 'core.session_key trống — chạy lại migration 057' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF ts IS NULL OR abs(extract(epoch FROM clock_timestamp()) - ts) > 300 THEN
        RAISE EXCEPTION 'claims phiên quá hạn ký (lệch hơn 5 phút)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    want := encode(core.hmac_sha256(secret, convert_to(claims || ':' || pg_backend_pid()::text || ':' || ts::text, 'UTF8')), 'hex');
    -- so sánh qua băm: thời gian không phụ thuộc vị trí byte khác đầu tiên
    IF mac IS NULL OR sha256(convert_to(want, 'UTF8')) <> sha256(convert_to(mac, 'UTF8')) THEN
        RAISE EXCEPTION 'claims phiên không do lõi cấp (chữ ký sai)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    j := claims::jsonb;
    IF existing IS NOT NULL AND (SELECT relowner FROM pg_catalog.pg_class WHERE oid = existing)
                              IS DISTINCT FROM (SELECT relowner FROM pg_catalog.pg_class WHERE oid = 'core.session_key'::regclass) THEN
        -- bảng tạm cùng tên do app tự tạo (giả claims): không ghi vào đó, không dùng nó
        RAISE EXCEPTION 'bảng claims phiên không do lõi tạo' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF existing IS NULL THEN
        CREATE TEMP TABLE barista_claims (k smallint PRIMARY KEY, xact timestamptz NOT NULL, claims jsonb NOT NULL) ON COMMIT PRESERVE ROWS;
        EXECUTE format('GRANT SELECT ON pg_temp.barista_claims TO %I', session_user);   -- app đọc, không ghi / xoá (bảng của owner)
    END IF;
    INSERT INTO pg_temp.barista_claims (k, xact, claims) VALUES (1, transaction_timestamp(), j)
    ON CONFLICT (k) DO UPDATE SET xact = EXCLUDED.xact, claims = EXCLUDED.claims;
END $$;
REVOKE ALL ON FUNCTION core.open_session(text, text, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.open_session(text, text, bigint) TO barista_app;

-- App KHÔNG được tạo bảng tạm: bảng claims giả không tạo nổi (kể cả sau DISCARD TEMP), nên hàm đọc không phải kiểm chủ bảng mỗi lần gọi.
-- Hàm DEFINER chạy bằng owner (chủ DB) nên vẫn tạo được. Mã app không dùng bảng tạm (kiểm 10/11: không có CREATE TEMP trong src/).
DO $$ BEGIN
    EXECUTE format('REVOKE TEMP ON DATABASE %I FROM PUBLIC, barista_app', current_database());
END $$;

-- BẢN ĐÃ KIỂM của giao dịch hiện tại — không có / giao dịch khác ⇒ NULL ⇒ mọi hàm dưới trả "không có ai" ⇒ đóng.
-- Hàm này bị gọi TỪNG DÒNG qua các hàm subject_visible_* (đo 10/11: ~91.000 lần / lượt talk.inbox) nên giữ đúng một truy vấn SPI;
-- không kiểm chủ bảng ở đây — bất biến "chỉ owner tạo được bảng tạm" do REVOKE TEMP ở trên và kiểm lúc mở phiên giữ.
-- KHÔNG đặt SET search_path (mỗi lần gọi phải lưu/khôi phục GUC: đo 3,96µs → 2,25µs khi bỏ); thay vào đó mọi tên đều ghi đủ schema và toán tử ghi
-- OPERATOR(pg_catalog.=) nên search_path của người gọi không đổi được nghĩa câu lệnh.
CREATE FUNCTION core.claims() RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    c pg_catalog.jsonb;
BEGIN
    IF pg_catalog.to_regclass('pg_temp.barista_claims') IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT t.claims INTO c FROM pg_temp.barista_claims t
    WHERE t.k OPERATOR(pg_catalog.=) 1 AND t.xact OPERATOR(pg_catalog.=) pg_catalog.transaction_timestamp();
    RETURN c;
END $$;

-- Tám hàm biến phiên: giữ tên / chữ ký / STABLE (chính sách 054 gọi dạng (SELECT …) — một lần mỗi câu). Thân đọc bản đã kiểm, không đọc app.*.
CREATE OR REPLACE FUNCTION core.tenant() RETURNS uuid LANGUAGE sql STABLE
    AS $$ SELECT nullif(core.claims() ->> 'tenant_id', '')::uuid $$;
CREATE OR REPLACE FUNCTION core.me() RETURNS uuid LANGUAGE sql STABLE
    AS $$ SELECT nullif(core.claims() ->> 'user_id', '')::uuid $$;
CREATE OR REPLACE FUNCTION core.my_email() RETURNS text LANGUAGE sql STABLE
    AS $$ SELECT nullif(core.claims() ->> 'email', '') $$;
CREATE OR REPLACE FUNCTION core.is_system() RETURNS boolean LANGUAGE sql STABLE
    AS $$ SELECT coalesce(core.claims() ->> 'system', 'off') = 'on' $$;
CREATE OR REPLACE FUNCTION core.link_ref() RETURNS uuid LANGUAGE sql STABLE
    AS $$ SELECT nullif(core.claims() ->> 'link_ref', '')::uuid $$;
CREATE OR REPLACE FUNCTION core.my_teams() RETURNS uuid[] LANGUAGE sql STABLE
    AS $$ SELECT coalesce((SELECT array_agg(x::uuid) FROM jsonb_array_elements_text(coalesce(core.claims() -> 'teams', '[]'::jsonb)) AS t(x)), '{}'::uuid[]) $$;
CREATE OR REPLACE FUNCTION core.my_units() RETURNS uuid[] LANGUAGE sql STABLE
    AS $$ SELECT coalesce((SELECT array_agg(x::uuid) FROM jsonb_array_elements_text(coalesce(core.claims() -> 'member_teams', '[]'::jsonb)) AS t(x)), '{}'::uuid[]) $$;
-- một lần claims() cho người thường (coalesce dừng ở vế đầu); quyền hệ thống (scopes rỗng) rơi xuống vế sau ⇒ 'all'
CREATE OR REPLACE FUNCTION core.scope(resource text) RETURNS text LANGUAGE sql STABLE
    AS $$
    SELECT coalesce(core.claims() -> 'scopes' ->> resource,
                    CASE WHEN coalesce(core.claims() ->> 'system', 'off') = 'on' THEN 'all' ELSE 'none' END)
$$;
