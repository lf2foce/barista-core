-- module: core
-- 0168 §2 — NHẬT KÝ NHẬP FILE: ai nhập file nào, lúc nào, bằng lệnh gì, bao nhiêu dòng vào / lỗi, dòng nào lỗi vì sao. Trước đây kết quả từng dòng
-- chỉ trả về màn của người nhập (nhật ký thao tác có từng lệnh nhưng không gom theo lần nhập, không có số dòng Excel). Một bảng lần nhập + một bảng
-- dòng; lần nhập của HỆ KHÁC (nạp lịch sử) cùng chỗ, đánh dấu ``source = 'import'`` + hệ nguồn + mã lô nguồn (chống lặp).
-- CHỈ THÊM: hai bảng mới ⇒ bản cũ chạy song song không biết tới chúng (lần nhập trên bản cũ không vào nhật ký — chấp nhận), không bảo trì.

CREATE TABLE core.import_runs (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    command text NOT NULL CHECK (btrim(command) <> ''),   -- lệnh Barista đã chạy từng dòng; lần nhập của hệ khác: loại nhập ở hệ đó
    file_name text NOT NULL DEFAULT '',
    file_sha256 text NOT NULL DEFAULT '' CHECK (file_sha256 = '' OR file_sha256 ~ '^[0-9a-f]{64}$'),
    total integer NOT NULL DEFAULT 0 CHECK (total >= 0),
    ok integer NOT NULL DEFAULT 0 CHECK (ok >= 0),
    failed integer NOT NULL DEFAULT 0 CHECK (failed >= 0),
    by_user uuid REFERENCES core.users(id),
    by_ref text NOT NULL DEFAULT '',                       -- mã người ở hệ nguồn khi không khớp được thành viên
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    source text NOT NULL DEFAULT 'live' CHECK (source IN ('live', 'import')),
    origin text NOT NULL DEFAULT '',
    ext_ref text NOT NULL DEFAULT '',
    data jsonb NOT NULL DEFAULT '{}'::jsonb,               -- số hệ nguồn tự khai (tổng / thành công / lỗi, trạng thái lô…) để đối chiếu
    CHECK ((source = 'live') = (origin = '' AND ext_ref = '')),
    CHECK (origin = '' OR origin ~ '^[a-z][a-z0-9_]{1,39}$')
);
CALL core.secure_tenant('core.import_runs');
CREATE UNIQUE INDEX uq_import_runs_ext ON core.import_runs (tenant_id, origin, ext_ref) WHERE source = 'import';
CREATE INDEX idx_import_runs_at ON core.import_runs (tenant_id, started_at DESC);
GRANT SELECT, INSERT, UPDATE ON core.import_runs TO barista_app;
COMMENT ON TABLE core.import_runs IS 'Lần nhập file (0168): lệnh, file, người, số dòng vào / lỗi — lần nhập của hệ khác đánh dấu source=import';

CREATE TABLE core.import_run_rows (
    run_id uuid NOT NULL REFERENCES core.import_runs(id) ON DELETE CASCADE,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    line integer NOT NULL CHECK (line >= 0),
    status text NOT NULL CHECK (status IN ('ok', 'pending_approval', 'proposed', 'invalid', 'conflict', 'denied', 'not_found', 'error', 'failed')),
    error text NOT NULL DEFAULT '',
    data jsonb NOT NULL DEFAULT '{}'::jsonb,               -- dòng như người nhập gửi (đã khớp cột) — cùng nội dung nhật ký thao tác đang giữ cho lệnh
    ref text NOT NULL DEFAULT '',                          -- khoá chống lặp của dòng (Barista) / mã bản ghi dòng đã sinh ra ở hệ nguồn
    ext_ref text NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, line)
);
CALL core.secure_tenant('core.import_run_rows');
CREATE INDEX idx_import_run_rows_bad ON core.import_run_rows (run_id) WHERE status NOT IN ('ok', 'pending_approval');
GRANT SELECT, INSERT ON core.import_run_rows TO barista_app;
COMMENT ON TABLE core.import_run_rows IS 'Từng dòng của một lần nhập (0168): kết quả + lỗi + dòng như đã gửi';
