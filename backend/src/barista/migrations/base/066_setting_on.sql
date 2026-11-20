-- module: core — lý do: 0152 — chính sách RLS của gói đọc CÔNG TẮC công ty (0128) qua MỘT hàm lõi thay vì SQL của module đọc thẳng core.settings
-- (bất biến 3). Chưa có dòng ⇒ mặc định do bên gọi nêu (= mặc định khai ở Module.setting). Hàm mới, không đổi gì đang chạy ⇒ không cần bảo trì.
CREATE FUNCTION core.setting_on(p_key text, p_default boolean) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT coalesce((SELECT s.enabled FROM core.settings s WHERE s.tenant_id = core.tenant() AND s.key = p_key), p_default) $$;
COMMENT ON FUNCTION core.setting_on(text, boolean) IS 'Công tắc công ty (0128) cho chính sách RLS — gọi dạng (SELECT core.setting_on(…)) để tính một lần mỗi câu (0145)';
