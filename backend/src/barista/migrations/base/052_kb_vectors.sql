-- module: kb — lý do: 0141 — tìm theo NGHĨA cho kho tài liệu: vector của từng đoạn (+ model + số chiều), gộp với tìm từ khoá bằng RRF.
-- CHỈ THÊM cột (không mặc định ⇒ không viết lại bảng) + chỉ mục ⇒ không cần cửa sổ bảo trì: bản cũ chạy song song không đọc / ghi các cột này,
-- đoạn nó ghi mang embedding NULL ⇒ vòng quét kb.embed_sweep nhúng sau.
-- Kiểu cột chọn THEO DB: có extension vector (đã cài, hoặc cài được bằng quyền đang chạy) ⇒ cột vector + HNSW; không ⇒ real[] (cosine tính bằng SQL
-- trên các đoạn người hỏi đọc được — ADR 0141 §4). Migration KHÔNG BAO GIỜ hỏng vì thiếu pgvector. Ép nhánh mảng (diễn tập DB không pgvector):
-- đặt biến phiên barista.kb_vector = 'off' cho kết nối chạy migration. Bản cài thêm pgvector về sau: python -m barista.modules.kb.vectors upgrade.
ALTER TABLE kb.chunks ADD COLUMN embed_model text, ADD COLUMN embed_dims integer, ADD COLUMN embed_tried_at timestamptz, ADD COLUMN embed_error text,
    ADD COLUMN embed_claimed_at timestamptz;   -- việc nhúng đang giữ đoạn (việc nền theo tài liệu và vòng quét không nhúng trùng); quá 5 phút = bỏ giữ

DO $$
DECLARE
    use_vector boolean := false;
    s text;
BEGIN
    IF coalesce(current_setting('barista.kb_vector', true), '') <> 'off' THEN
        IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
            use_vector := true;
        ELSIF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
            BEGIN
                CREATE EXTENSION vector SCHEMA public;   -- pgvector không "trusted": chỉ superuser / chủ DB được cấp mới tạo được
                use_vector := true;
            EXCEPTION WHEN insufficient_privilege OR feature_not_supported OR undefined_file THEN
                RAISE NOTICE 'kb: không cài được pgvector (%) — dùng real[]', SQLERRM;
            END;
        END IF;
    END IF;
    IF use_vector THEN
        SELECT n.nspname INTO s FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'vector';
        EXECUTE format('ALTER TABLE kb.chunks ADD COLUMN embedding %I.vector', s);
        -- cột không cố định số chiều (đổi model không cần migration); chỉ mục là BIỂU THỨC ép về 768 chiều trên phần embed_dims = 768
        EXECUTE format('CREATE INDEX idx_kb_chunks_hnsw ON kb.chunks USING hnsw ((embedding::%1$I.vector(768)) %1$I.vector_cosine_ops) '
                       'WHERE embed_dims = 768', s);
    ELSE
        ALTER TABLE kb.chunks ADD COLUMN embedding real[];
    END IF;
END $$;

ALTER TABLE kb.chunks ADD CONSTRAINT chunks_embedding_meta CHECK (
    (embedding IS NULL) = (embed_model IS NULL) AND (embed_model IS NULL) = (embed_dims IS NULL));
COMMENT ON COLUMN kb.chunks.embedding IS 'Vector đã chuẩn hoá (độ dài 1) của tiêu đề + mục + đoạn — 0141; kiểu vector (pgvector) hoặc real[]';
COMMENT ON COLUMN kb.chunks.embed_model IS 'Model đã nhúng (chuỗi cấu hình, vd google:gemini-embedding-001) — khác model đang dùng ⇒ nhúng lại';
-- vòng quét chỉ chạm đoạn THIẾU (chỉ mục một phần) hoặc KHÁC model đang dùng (khoảng trên chỉ mục model) — không quét mọi đoạn mỗi phút
CREATE INDEX idx_kb_chunks_unembedded ON kb.chunks (tenant_id) WHERE embedding IS NULL;
CREATE INDEX idx_kb_chunks_embed_model ON kb.chunks (embed_model, embed_dims, tenant_id) WHERE embedding IS NOT NULL;
