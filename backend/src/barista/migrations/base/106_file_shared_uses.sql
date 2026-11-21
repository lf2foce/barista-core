-- module: core
-- 106 (ADR 0249 lát 1, api 1.72, 21/11/2026) — DÙNG CHUNG THEO THAM CHIẾU (sửa luật 0122): một tệp trong sổ ``core.files`` có thể có NHIỀU chỗ dùng thuộc nhiều
-- module (tin đính kèm của trao đổi + tệp của hồ sơ…) mà không chép thân tệp. Quyền "module đích được lấy tệp" vẫn là MỘT lần cấp ở ``core.file_copy_grants``
-- (module đang giữ tệp cấp sau khi kiểm người xem thấy chỗ dùng của mình — kernel/files.py ``grant``); module đích dùng quyền đó bằng ``copy`` (bản độc lập,
-- như trước) HOẶC ``adopt`` (giữ CÙNG tệp — cột mới ``by_reference``). Module "giữ" tệp = module đã tạo (``core.files.module``) hoặc đã lấy theo tham chiếu;
-- mở được khi người xem thấy một chỗ dùng của chính module đó (RLS) — biết ``file_id`` hay tự chèn khoá ngoại vẫn không đủ.
-- Cột mới có mặc định, dòng cũ đều là bản sao ⇒ false đúng nghĩa; bản cũ không đọc / ghi cột ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

ALTER TABLE core.file_copy_grants ADD COLUMN by_reference boolean NOT NULL DEFAULT false;
ALTER TABLE core.file_copy_grants ADD CONSTRAINT file_copy_grants_reference_check CHECK (NOT by_reference OR (used_at IS NOT NULL AND copy_id IS NULL));
CREATE INDEX idx_file_copy_grants_reference ON core.file_copy_grants USING btree (file_id, to_module) WHERE by_reference;
GRANT UPDATE (by_reference) ON core.file_copy_grants TO barista_app;
