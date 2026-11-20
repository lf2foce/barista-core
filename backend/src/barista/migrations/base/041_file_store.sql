-- module: core, kb — lý do: sổ tệp của lõi (0122) + cột file_id ở kb.files (bảng đang giữ thân tệp), trỏ vào sổ (khoá ngoại là cách kernel biết bảng nào dùng tệp); deployment: maintenance
-- 041 (0122): THÂN TỆP Ở KHO ĐỐI TƯỢNG, DB CHỈ GIỮ THAM CHIẾU — bước 1/2. Tách từ base/041_files.sql (0130, ghi chú 08/11; cột của Trao đổi ở
-- talk/041_attachment_file_ref.sql). Sổ ``core.files`` (kernel/files.py sở hữu, như core.public_links), quyền sao tệp một lần giữa module
-- ``core.file_copy_grants``, thời hạn lưu tệp đã release của từng công ty (dữ liệu, trống = giữ mãi), cột ``file_id`` (cho phép trống) ở kb.files.
-- Bước dữ liệu (đẩy bytea vào kho, điền file_id) chạy bằng Python ngay trước 042, trong giao dịch của 042 (kernel/migrate.py steps). Bảo trì: mã
-- mới chỉ ghi file_id ⇒ cần 042 cùng lượt; bản cũ không được chạy song song (runbook 11).

CREATE TABLE core.files (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    object_key text NOT NULL UNIQUE,
    filename text NOT NULL,
    content_type text NOT NULL,
    size_bytes bigint NOT NULL,
    sha256 text NOT NULL,
    module text NOT NULL,
    created_by uuid REFERENCES core.users(id),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    released_at timestamp with time zone,
    legal_hold boolean DEFAULT false NOT NULL,
    copied_from uuid,              -- tệp gốc của bản sao (không khoá ngoại: bản gốc bị dọn thì dấu vết nguồn vẫn còn)
    integrity_failed_at timestamp with time zone,   -- lần mở gần nhất đọc ra thân lệch sha256 / mất đối tượng (điều tra)
    CONSTRAINT files_filename_check CHECK ((btrim(filename) <> ''::text) AND (length(filename) <= 300)),
    CONSTRAINT files_size_check CHECK (size_bytes >= 0),   -- put() từ chối tệp rỗng; >= 0 cho dòng bytea cũ chuyển sang
    CONSTRAINT files_sha256_check CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT files_module_check CHECK (module ~ '^[a-z_]+$'),
    CONSTRAINT files_object_key_check CHECK (object_key ~ '^t/[0-9a-z-]{1,64}/[0-9a-f-]{36}$')
);
CALL core.secure_tenant('core.files');
CREATE INDEX idx_files_released ON core.files USING btree (tenant_id, released_at) WHERE released_at IS NOT NULL;
CREATE INDEX idx_files_copied_from ON core.files USING btree (copied_from) WHERE copied_from IS NOT NULL;

-- Quyền sao MỘT lần: module sở hữu cấp (sau khi kiểm người xem), module đích dùng — đúng tệp, đúng module, đúng người, còn hạn (dự phòng).
CREATE TABLE core.file_copy_grants (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    file_id uuid NOT NULL REFERENCES core.files(id) ON DELETE CASCADE,
    to_module text NOT NULL,
    granted_to uuid REFERENCES core.users(id) ON DELETE CASCADE,
    created_by uuid REFERENCES core.users(id),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    copy_id uuid,                  -- dòng sổ của bản sao đã tạo
    CONSTRAINT file_copy_grants_module_check CHECK (to_module ~ '^[a-z_]+$'),
    CONSTRAINT file_copy_grants_expiry_check CHECK ((expires_at > created_at) AND (expires_at <= created_at + interval '10 minutes'))
);
CALL core.secure_tenant('core.file_copy_grants');
CREATE INDEX idx_file_copy_grants_file ON core.file_copy_grants USING btree (file_id);

-- Thời hạn lưu tệp đã release (ngày) — chính sách CỦA CÔNG TY; trống = giữ mãi (không bao giờ tự xoá).
ALTER TABLE core.tenant_profile ADD COLUMN file_retention_days integer CHECK (file_retention_days > 0);

ALTER TABLE kb.files ADD COLUMN file_id uuid REFERENCES core.files(id);
CREATE UNIQUE INDEX idx_kb_files_file ON kb.files USING btree (file_id);

GRANT SELECT, INSERT ON core.files, core.file_copy_grants TO barista_app;
GRANT UPDATE (released_at) ON core.files TO barista_app;
GRANT UPDATE (used_at, copy_id) ON core.file_copy_grants TO barista_app;
