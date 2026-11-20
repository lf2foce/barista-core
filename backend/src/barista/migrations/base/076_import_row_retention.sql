-- module: core
-- 0174 §4 — GIỮ NỘI DUNG DÒNG CỦA NHẬT KÝ NHẬP BAO LÂU là DỮ LIỆU của từng công ty (mặc định 90 ngày; trống = giữ mãi). Quá hạn ⇒ vòng quét
-- ``core.purge_import_rows`` xoá NỘI DUNG dòng (``import_run_rows.data`` — thứ có thể chứa dữ liệu cá nhân), giữ mãi lần nhập (file, người, lúc, tổng /
-- vào / lỗi) + số dòng / kết quả / lỗi của từng dòng. Không bao giờ chạm nhật ký thao tác (``core.audit_log``).
-- CHỈ THÊM: cột có mặc định / cho phép trống + quyền ghi đúng cột nội dung ⇒ bản cũ chạy song song không đọc cột mới, không bảo trì.

SET LOCAL lock_timeout = '10s';

ALTER TABLE core.tenants ADD COLUMN import_rows_keep_days integer DEFAULT 90
    CHECK (import_rows_keep_days IS NULL OR import_rows_keep_days BETWEEN 1 AND 3650);
COMMENT ON COLUMN core.tenants.import_rows_keep_days IS 'Số ngày giữ nội dung dòng của nhật ký nhập (0174); NULL = giữ mãi. Số đếm / kết quả / lỗi giữ mãi';
GRANT UPDATE (import_rows_keep_days) ON core.tenants TO barista_app;

ALTER TABLE core.import_runs ADD COLUMN rows_purged_at timestamptz;
COMMENT ON COLUMN core.import_runs.rows_purged_at IS 'Lúc nội dung dòng của lần nhập bị xoá theo hạn giữ của công ty (0174) — số đếm / lỗi vẫn còn';
CREATE INDEX idx_import_runs_unpurged ON core.import_runs (tenant_id, started_at) WHERE rows_purged_at IS NULL;
GRANT UPDATE (data) ON core.import_run_rows TO barista_app;
