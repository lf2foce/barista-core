-- module: flow
-- Việc nhớ NƠI NÓ SINH RA (kiểu source / source_ref, không khoá ngoại chéo module): tin nhắn trong trao đổi ("Tạo việc" trên một tin — ADR 0115,
-- 0117), hay sau này một chỗ khác. Khác ``subject_*`` (đối tượng nghiệp vụ việc nói về, bộ kiểm đọc): nguồn chỉ để dẫn ngược lại + đánh dấu
-- "Đã tạo việc" trên tin. Ai thấy dấu đó = ai thấy VIỆC (RLS của flow.tasks lúc đọc) — không chép quyền sang đâu.
ALTER TABLE flow.tasks ADD COLUMN source_kind text, ADD COLUMN source_ref text,
    ADD CONSTRAINT tasks_source_pair CHECK ((source_kind IS NULL) = (source_ref IS NULL));
CREATE INDEX idx_flow_tasks_source ON flow.tasks USING btree (tenant_id, source_kind, source_ref) WHERE source_ref IS NOT NULL;
-- Việc do MODULE sinh: một nguồn chỉ MỘT việc đang mở — ép ở DB (hai lượt bấm cùng lúc không thành hai việc; như uq_flow_tasks_overdue). Việc tạo từ
-- tin nhắn không bị ràng buộc này: một tin có thể sinh nhiều việc cho nhiều người.
CREATE UNIQUE INDEX uq_flow_tasks_source_open ON flow.tasks USING btree (tenant_id, source_kind, source_ref)
    WHERE origin = 'module' AND status IN ('todo', 'doing', 'blocked');

-- 0117 (ghi chú 07/11): việc do MODULE sinh ra (vd việc thu một đợt tiền, gắn sẵn bộ kiểm của chính module đó) có nguồn gốc riêng — không lẫn
-- vào việc người / trợ lý tạo khi đo "việc mới có gắn đối tượng không".
ALTER TABLE flow.tasks DROP CONSTRAINT tasks_origin_check,
    ADD CONSTRAINT tasks_origin_check CHECK (origin = ANY (ARRAY['manual'::text, 'stage'::text, 'returned'::text, 'assistant'::text, 'overdue'::text,
                                                                'module'::text]));
