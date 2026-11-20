-- module: core
-- 0232 (mục "Nhúng theo công ty", 16/11/2026) — HỒ SƠ NHÚNG THEO CÔNG TY: model embedding + số chiều (+ máy chủ tự dựng) là chính sách của nhà vận hành,
-- như hạng trợ lý AI (0193) và hồ sơ đọc tài liệu (base/094). Không có dòng ⇒ dùng mặc định bản cài (``settings.embed_model`` / ``embed_dims``).
--  * ``profile``: khoá model, dims, base_url, key_ref (tên mục trong llm_keys — KHÔNG lưu khoá), truncate, query_prefix, document_prefix, usd_per_mtok, min_sim.
--  * ``previous``: hồ sơ ngay trước lần đổi gần nhất — quay lại = đặt lại hồ sơ này (``embed-set --rollback``).
--  * ``model`` / ``dims``: cột sinh từ ``profile`` để vòng quét ``kb.embed_sweep`` biết model đang dùng của từng công ty bằng một câu SQL.
-- CHỈ THÊM (bảng mới) ⇒ bản cũ chạy song song không vướng.

SET LOCAL lock_timeout = '10s';

CREATE TABLE core.embed_profiles (
    tenant_id uuid DEFAULT core.tenant() NOT NULL PRIMARY KEY REFERENCES core.tenants(id) ON DELETE CASCADE,
    profile jsonb NOT NULL,
    previous jsonb,
    model text GENERATED ALWAYS AS (profile->>'model') STORED,
    dims integer GENERATED ALWAYS AS (nullif(profile->>'dims', '')::integer) STORED,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL DEFAULT ''
);
CALL core.secure_tenant('core.embed_profiles');
GRANT SELECT, INSERT, UPDATE, DELETE ON core.embed_profiles TO barista_app;
COMMENT ON TABLE core.embed_profiles IS 'Hồ sơ nhúng (model, số chiều, máy chủ) theo công ty — nhà vận hành đặt; lớp chồng lên mặc định bản cài (0232)';
