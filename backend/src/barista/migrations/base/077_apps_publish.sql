-- module: apps
-- 077 (0176): ĐƯA DASHBOARD VÀO "BÁO CÁO QUẢN TRỊ" — chia ĐỊNH NGHĨA app cho cả công ty (không chia ảnh chụp số): người xem mở bằng quyền CHÍNH MÌNH.
-- published = đang hiện trong menu báo cáo quản trị; unpublished_at = lúc gỡ (người đã ghim thấy "đã gỡ", không mất ghim lặng lẽ);
-- removed_at = chủ xoá app ĐÃ TỪNG chia (giữ dòng làm dấu "đã gỡ" cho người đã ghim; app chưa từng chia vẫn xoá hẳn).
-- Đọc: chủ thấy app của mình (như cũ) + MỌI NGƯỜI trong công ty đọc được ĐỊNH NGHĨA app đang chia / vừa gỡ (để menu ráp + mở bằng quyền người xem);
-- ghi / xoá: CHỈ chủ (chính sách cũ p_own giữ nguyên cho mọi lệnh). Chỉ THÊM — bản cũ chạy song song không đọc cột mới.
ALTER TABLE apps.items ADD COLUMN published BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE apps.items ADD COLUMN published_by UUID REFERENCES core.users(id);
ALTER TABLE apps.items ADD COLUMN published_at TIMESTAMPTZ;
ALTER TABLE apps.items ADD COLUMN unpublished_at TIMESTAMPTZ;
ALTER TABLE apps.items ADD COLUMN removed_at TIMESTAMPTZ;
CREATE POLICY p_shared_read ON apps.items FOR SELECT
    USING (tenant_id = (SELECT core.tenant()) AND (published OR unpublished_at IS NOT NULL));
CREATE INDEX idx_apps_published ON apps.items (tenant_id) WHERE published OR unpublished_at IS NOT NULL;
-- Lịch sử phiên bản đọc theo app (secure_child), nhưng GHI / XOÁ phiên bản chỉ chủ app — người xem dashboard đang chia không thêm được phiên bản.
CREATE POLICY p_owner_insert ON apps.versions AS RESTRICTIVE FOR INSERT
    WITH CHECK (EXISTS (SELECT 1 FROM apps.items i WHERE i.id = app_id AND i.owner_id = (SELECT core.me())));
CREATE POLICY p_owner_delete ON apps.versions AS RESTRICTIVE FOR DELETE
    USING (EXISTS (SELECT 1 FROM apps.items i WHERE i.id = app_id AND i.owner_id = (SELECT core.me())));
