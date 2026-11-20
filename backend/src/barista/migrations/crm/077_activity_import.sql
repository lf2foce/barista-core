-- module: crm
-- 0174 — CHĂM SÓC NẠP TỪ HỆ KHÁC giữ giờ gốc và người làm: cùng bảng crm.activities (một định nghĩa "đã liên hệ" cho hạn gọi lead, thẻ buổi sáng,
-- độ nóng khách — chăm sóc là MỘT khái niệm, không như lịch sử chia lead của 0168), đánh dấu nguồn ``source = 'import'`` + hệ nguồn + mã dòng nguồn
-- (nạp lại không nhân đôi). Người làm không khớp thành viên ⇒ ``author_ref`` (mã người ở hệ nguồn), không bỏ dòng.
-- CHỈ THÊM: cột có mặc định / cho phép trống, CHECK chỉ ràng buộc dòng nguồn 'import' (mọi dòng đang có là 'live'), chỉ mục một phần ⇒ bản cũ chạy song
-- song vẫn ghi được (giá trị mặc định), không cần cửa sổ bảo trì.

SET LOCAL lock_timeout = '10s';

ALTER TABLE crm.activities ADD COLUMN source text NOT NULL DEFAULT 'live' CHECK (source IN ('live', 'import')),
                           ADD COLUMN origin text NOT NULL DEFAULT '',
                           ADD COLUMN ext_ref text NOT NULL DEFAULT '',
                           ADD COLUMN author_ref text NOT NULL DEFAULT '',
                           ADD COLUMN data jsonb NOT NULL DEFAULT '{}'::jsonb,
                           ADD COLUMN imported_by uuid REFERENCES core.users(id),
                           ADD COLUMN imported_at timestamptz,
                           ADD CONSTRAINT activities_import_marked CHECK (
                               (source = 'live' AND origin = '' AND ext_ref = '' AND imported_at IS NULL)
                               OR (source = 'import' AND origin ~ '^[a-z][a-z0-9_]{1,39}$' AND btrim(ext_ref) <> '' AND imported_at IS NOT NULL));
CREATE UNIQUE INDEX uq_activities_import ON crm.activities (tenant_id, origin, ext_ref) WHERE source = 'import';
COMMENT ON COLUMN crm.activities.source IS 'live = ghi trong Barista (giờ ghi); import = nạp từ hệ khác giữ giờ gốc + người làm (0174)';
COMMENT ON COLUMN crm.activities.author_ref IS 'Mã người làm ở hệ nguồn khi không khớp thành viên (by_user trống) — 0174';
