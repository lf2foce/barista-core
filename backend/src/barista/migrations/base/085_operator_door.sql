-- module: core
-- 085 (ADR 0206, quyết định chủ sản phẩm 12/11/2026): CỬA NHÀ VẬN HÀNH (``python -m barista.operator``).
--  * Tài nguyên ``core.operator`` (đặt quyền / tạo vai / cài app / quyết phiếu đổi quyền bằng cửa nhà vận hành) — như ``core.ai_policy`` (082): KHÔNG
--    cấp được cho vai nào bằng phiên của khách. Trigger ``core.grants_operator_only`` chặn cả hai tài nguyên.
--  * App từ xa do nhà vận hành cài không có NGƯỜI bảo lãnh: quyền app lúc chạy = vai app ∩ quyền hiện tại của một VAI (``sponsor_role`` — vd vai
--    tích hợp chỉ dùng làm khuôn), thay cho quyền hiện tại của người cài (``installed_by``). Đúng một trong hai (CHECK).
-- CHỈ THÊM / NỚI: cột mới cho phép trống, bỏ NOT NULL của ``installed_by`` (bản cũ chạy song song đọc app bảo lãnh bằng vai ⇒ nạp người bảo lãnh
-- NULL ⇒ từ chối app — đóng khi không rõ), CHECK + khoá ngoại NOT VALID (dòng cũ hiển nhiên thoả: có người cài, chưa có vai), thay thân trigger
-- (cùng tên, cùng chữ ký) ⇒ không cần dừng dịch vụ.

SET LOCAL lock_timeout = '10s';

CREATE OR REPLACE FUNCTION core.grants_operator_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- phiên app (không phải chủ bảng — migration / lệnh vận hành chạy bằng kết nối chủ) không chạy bằng quyền hệ thống ⇒ từ chối
    IF NEW.resource IN ('core.ai_policy', 'core.operator') AND NOT core.is_system()
       AND current_user <> (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'core.grants'::regclass) THEN
        RAISE EXCEPTION '% chỉ nhà vận hành cấp (ADR 0193 / 0206)', NEW.resource USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

ALTER TABLE core.remote_apps ADD COLUMN sponsor_role text;
ALTER TABLE core.remote_apps ALTER COLUMN installed_by DROP NOT NULL;
ALTER TABLE core.remote_apps ADD CONSTRAINT remote_apps_one_sponsor CHECK ((installed_by IS NULL) <> (sponsor_role IS NULL)) NOT VALID;
ALTER TABLE core.remote_apps ADD CONSTRAINT remote_apps_sponsor_role_fkey FOREIGN KEY (tenant_id, sponsor_role) REFERENCES core.roles (tenant_id, key)
    NOT VALID;
COMMENT ON COLUMN core.remote_apps.installed_by IS 'Người bảo lãnh (app cài trong app, qua duyệt): quyền app ≤ quyền hiện tại của người này. NULL ⇔ sponsor_role';
COMMENT ON COLUMN core.remote_apps.sponsor_role IS 'Vai bảo lãnh (app nhà vận hành cài — 0206): quyền app ≤ quyền hiện tại của vai này. NULL ⇔ installed_by';
