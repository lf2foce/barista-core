-- module: core
-- 0232 — ĐỌC TÀI LIỆU / OCR là MỘT năng lực lõi, cấu hình theo công ty × mục đích, có bộ nhớ theo băm file, đo theo mục đích.
--  * ``core.docread_profiles``: hồ sơ đọc (chuỗi bộ đọc, model, công tắc scan / bảng / ảnh, trần trang, hạn mức token tháng) — CHÍNH SÁCH CỦA NHÀ VẬN HÀNH như hạng trợ lý AI (0193):
--    khách không sửa (tài nguyên ``core.ai_policy``, lệnh ``core.set_docread_profile``). Mục đích ``*`` = mọi mục đích.
--  * ``core.docread_cache``: kết quả đọc có tốn model, khoá (băm file, cấu hình đọc) ⇒ một file + một cấu hình không trả tiền hai lần.
--  * ``core.ai_usage.surface`` nhận thêm ``docread`` (lượt đọc tài liệu, loại ``docread:<mục đích>``).
-- CHỈ THÊM / NỚI (bảng mới; ràng buộc nới thêm một giá trị) ⇒ bản cũ chạy song song không vướng.

SET LOCAL lock_timeout = '10s';

CREATE TABLE core.docread_profiles (
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    purpose text NOT NULL CHECK (purpose = '*' OR purpose ~ '^[a-z][a-z0-9_]{1,39}$'),
    profile jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, purpose)
);
CALL core.secure_tenant('core.docread_profiles');
GRANT SELECT, INSERT, UPDATE, DELETE ON core.docread_profiles TO barista_app;
COMMENT ON TABLE core.docread_profiles IS 'Hồ sơ đọc tài liệu theo công ty × mục đích (0232) — nhà vận hành đặt; lớp chồng lên mặc định bản cài';

CREATE TABLE core.docread_cache (
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    fingerprint text NOT NULL,
    doc jsonb NOT NULL,
    hits integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_hit_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, sha256, fingerprint)
);
CALL core.secure_tenant('core.docread_cache');
GRANT SELECT, INSERT, UPDATE, DELETE ON core.docread_cache TO barista_app;
CREATE INDEX idx_docread_cache_age ON core.docread_cache (last_hit_at);
COMMENT ON TABLE core.docread_cache IS 'Kết quả đọc tài liệu có tốn model theo (băm file, cấu hình đọc) — không trả tiền hai lần (0232); dọn theo last_hit_at';

ALTER TABLE core.ai_usage DROP CONSTRAINT ai_usage_surface_check;
ALTER TABLE core.ai_usage ADD CONSTRAINT ai_usage_surface_check CHECK (surface = ANY (ARRAY['chat', 'voice', 'resume', 'work', 'kb', 'docread']));
