-- module: core
-- 0169: MÚI GIỜ CÔNG TY. Lưu mốc vẫn là timestamptz (UTC bên trong); NGÀY / GIỜ của nghiệp vụ (hôm nay, ranh ngày, giờ làm việc, kỳ báo cáo, hiển thị)
-- theo múi giờ của công ty — dữ liệu, mặc định Asia/Ho_Chi_Minh (mọi công ty đang có giữ nguyên hành vi). Lõi (db.tenant_tx) đặt TimeZone của
-- GIAO DỊCH theo cột này; core.tz() / core.today() đọc múi của phiên. CHỈ THÊM (cột có mặc định, hàm mới, thay thân hai hàm cùng chữ ký, cùng
-- kết quả cho múi Việt Nam) ⇒ bản cũ chạy song song lúc chuyển vẫn đúng, không cần cửa sổ bảo trì.

-- Tên IANA Postgres biết (pg_timezone_names) — không nhận viết tắt / độ lệch kiểu POSIX ("UTC+7" là UTC−7). Python kiểm thêm (clock.named).
CREATE FUNCTION core.valid_tz(p_name text) RETURNS boolean LANGUAGE sql STABLE
    AS $$ SELECT p_name IS NOT NULL AND EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names WHERE name = p_name) $$;

ALTER TABLE core.tenants ADD COLUMN timezone text NOT NULL DEFAULT 'Asia/Ho_Chi_Minh'
    CONSTRAINT tenants_timezone_check CHECK (core.valid_tz(timezone));
COMMENT ON COLUMN core.tenants.timezone IS 'Múi giờ công ty (0169, tên IANA): ngày / giờ nghiệp vụ và hiển thị; lưu mốc vẫn là timestamptz';
-- quản trị công ty đổi múi của CHÍNH công ty mình (RLS p_tenant: id = core.tenant()); chỉ cột này
GRANT UPDATE (timezone) ON core.tenants TO barista_app;

-- Múi giờ công ty của phiên: db.tenant_tx đặt TimeZone = core.tenants.timezone cho từng giao dịch (SET LOCAL); ngoài phiên công ty = múi mặc định
-- của bản cài (tham số kết nối). Thân là biểu thức đơn ⇒ Postgres nhúng thẳng vào câu (không tra bảng mỗi dòng).
CREATE FUNCTION core.tz() RETURNS text LANGUAGE sql STABLE
    AS $$ SELECT pg_catalog.current_setting('TimeZone') $$;
COMMENT ON FUNCTION core.tz() IS 'Múi giờ công ty của phiên (0169) — viết (x AT TIME ZONE core.tz()) thay cho tên múi cứng';

-- Câu XUYÊN công ty (vòng quét chọn công ty có việc — chạy bằng owner, không có phiên công ty): múi của TỪNG dòng theo mã công ty.
CREATE FUNCTION core.tz_of(p_tenant uuid) RETURNS text LANGUAGE sql STABLE
    AS $$ SELECT coalesce((SELECT t.timezone FROM core.tenants t WHERE t.id = p_tenant), pg_catalog.current_setting('TimeZone')) $$;
COMMENT ON FUNCTION core.tz_of(uuid) IS 'Múi giờ của một công ty (0169) cho câu xuyên công ty — vd sweep=: (now() AT TIME ZONE core.tz_of(tenant_id))::date';

CREATE FUNCTION core.today_of(p_tenant uuid) RETURNS date LANGUAGE sql STABLE
    AS $$ SELECT (now() AT TIME ZONE core.tz_of(p_tenant))::date $$;
COMMENT ON FUNCTION core.today_of(uuid) IS 'Hôm nay của một công ty (0169) cho câu xuyên công ty (sweep=) — trong phiên công ty dùng core.today()';

CREATE OR REPLACE FUNCTION core.today() RETURNS date LANGUAGE sql STABLE
    AS $$ SELECT (now() AT TIME ZONE core.tz())::date $$;
