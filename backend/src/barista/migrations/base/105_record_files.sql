-- module: core
-- 105 (ADR 0249 lát 1, api 1.72, 21/11/2026) — TỆP CỦA HỒ SƠ: một tệp gắn vào một đối tượng nghiệp vụ (loại, mã) — hợp đồng của giao dịch, CCCD của
-- khách, chứng từ của một bộ hồ sơ. Một dòng = MỘT CHỖ DÙNG của một tệp trong sổ ``core.files`` (thân tệp ở kho đối tượng — 0122; cùng một tệp có thể
-- là tin đính kèm của trao đổi VÀ tệp của hồ sơ — không chép, ``kernel/files.py``). Module / gói khai loại đối tượng nào nhận tệp (``Module.record_files``);
-- lõi giữ bảng, lệnh ``core.attach_file`` / ``core.remove_file`` và khối "Tệp" trên thẻ (kernel/record_files.py) — SQL module không chạm bảng này.
-- Ai thấy dòng nào: ai THẤY ĐỐI TƯỢNG (``core.subject_visible`` ⇒ hàm của module sở hữu, như core.field_changes / talk / meet) — đổi người phụ trách là người
-- cũ mất tệp ngay; không chép quyền sang đây. Vế InitPlan core.kinds_off() như 058 (gói của đối tượng tắt ⇒ không thấy).
-- CHỈ THÊM: không UPDATE / DELETE cho barista_app. GỠ = thêm một dòng ``core.record_file_removals`` (ai gỡ, lúc nào); dòng đã gỡ KHÔNG còn hiện với ai
-- qua RLS ⇒ cũng không còn là chỗ dùng mở được tệp (kernel/files.py hỏi đúng RLS này). Bảng gỡ chỉ theo công ty (secure_tenant) — chính sách của bảng
-- tệp đọc nó, nếu nó lại theo bảng tệp thì hai chính sách gọi vòng nhau. Đánh dấu gỡ bằng UPDATE không được: dòng mới phải còn qua chính sách đọc
-- (Postgres áp chính sách SELECT cho dòng sau UPDATE) mà dòng đã gỡ thì cố ý không còn hiện. Tệp không bị dọn khi còn dòng trỏ tới (kể cả đã gỡ).
-- Ba thủ tục secure_* không diễn đạt được "thấy theo đối tượng" nên chính sách viết ở đây, cùng khuôn 102.
-- Bảng mới, bản cũ không đọc / ghi ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

CREATE TABLE core.record_files (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    subject_kind text NOT NULL CHECK (subject_kind ~ '^[a-z_]+\.[a-z_]+$'),
    subject_id   uuid NOT NULL,
    file_id      uuid NOT NULL REFERENCES core.files(id),     -- KHÔNG cascade: khoá ngoại là cách kernel biết tệp còn chỗ dùng (0122)
    label        text NOT NULL DEFAULT '' CHECK (length(label) <= 80),   -- vai của tệp trên hồ sơ ("Hợp đồng", "CCCD"…); trống = chưa đặt
    added_by     uuid REFERENCES core.users(id),
    added_at     timestamptz NOT NULL DEFAULT now(),
    via          text NOT NULL DEFAULT 'upload' CHECK (via IN ('upload', 'reference'))   -- tải lên thẳng / lấy từ chỗ dùng khác (tin đính kèm…)
);
CREATE INDEX idx_record_files_subject ON core.record_files (tenant_id, subject_kind, subject_id, added_at DESC);
CREATE INDEX idx_record_files_file ON core.record_files (file_id);

CREATE TABLE core.record_file_removals (
    record_file_id uuid PRIMARY KEY REFERENCES core.record_files(id) ON DELETE CASCADE,
    tenant_id      uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    removed_by     uuid REFERENCES core.users(id),
    removed_at     timestamptz NOT NULL DEFAULT now()
);
CALL core.secure_tenant('core.record_file_removals');

ALTER TABLE core.record_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.record_files FORCE ROW LEVEL SECURITY;
CREATE POLICY p_read ON core.record_files FOR SELECT
    USING (tenant_id = (SELECT core.tenant()) AND (SELECT core.link_ref()) IS NULL
           AND NOT EXISTS (SELECT 1 FROM core.record_file_removals x WHERE x.record_file_id = record_files.id)
           AND coalesce(NOT (subject_kind = ANY ((SELECT core.kinds_off())::text[])), true) AND core.subject_visible(subject_kind, subject_id));
-- GẮN: chỉ người đang thấy hồ sơ (lệnh kiểm trước; Postgres giữ lần nữa)
CREATE POLICY p_add ON core.record_files FOR INSERT
    WITH CHECK (tenant_id = (SELECT core.tenant()) AND (SELECT core.link_ref()) IS NULL
                AND coalesce(NOT (subject_kind = ANY ((SELECT core.kinds_off())::text[])), true) AND core.subject_visible(subject_kind, subject_id));
COMMENT ON TABLE core.record_files IS 'subject=core.subject_visible';
GRANT SELECT, INSERT ON core.record_files, core.record_file_removals TO barista_app;
