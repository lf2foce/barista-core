-- module: schedule
-- Rà 23/11/2026: cột "Đã dùng" ở màn Loại lịch đếm dưới RLS ⇒ chỉ thấy lịch người xem được đọc; hẹn khách của sale lưu riêng tư nên quản trị thấy 0 và dễ tắt nhầm
-- một loại đang dùng hằng ngày. Đếm là thông tin cấu hình (bao nhiêu lịch mang loại này), không lộ nội dung ⇒ hàm đếm trong CÔNG TY hiện tại, bỏ qua RLS.
SET LOCAL lock_timeout = '10s';
CREATE FUNCTION schedule.kind_usage() RETURNS TABLE (kind TEXT, used BIGINT) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT e.kind, count(*) FROM schedule.events e WHERE e.tenant_id = core.tenant() AND e.status <> 'cancelled' GROUP BY e.kind
$$;
REVOKE ALL ON FUNCTION schedule.kind_usage() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION schedule.kind_usage() TO barista_app;
