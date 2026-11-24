-- module: core, admin, apps, asks, connectors, flow, kb, memory, papers, work — lý do: ADR 0258 — một thủ tục của lõi siết khoá ngoại theo công ty cho mọi schema của gói nền
-- ADR 0258: khoá ngoại giữa hai bảng CÙNG mang ``tenant_id`` phải mang cả ``tenant_id`` — ``FOREIGN KEY (tenant_id, x) REFERENCES đích (tenant_id, id)``.
-- Trước bản này phần lớn chỉ trỏ theo ``id``: RLS chặn ĐỌC chéo công ty, nhưng DB không chặn một dòng của công ty A trỏ sang dòng của công ty B khi mã ghi nhầm
-- (kết nối owner, bước nhập, lệnh hệ thống đều bỏ qua RLS). Từ bản này DB tự giữ: dòng con và dòng cha luôn cùng công ty.
-- ``core.tenant_fks(schemas)``: với mỗi khoá ngoại MỘT cột của bảng trong các schema đã nêu, mà cả bảng nguồn lẫn bảng đích có ``tenant_id`` —
--   1. bảo đảm đích có ràng buộc duy nhất (tenant_id, cột đích) (thêm nếu thiếu);
--   2. thay khoá ngoại bằng bản kép, GIỮ tên, hành vi khi xoá / sửa và tính trì hoãn. ``ON DELETE SET NULL / SET DEFAULT`` chỉ đặt lại cột tham chiếu
--      (``SET NULL (cột)``), không đụng ``tenant_id``.
-- Dữ liệu đang có mà con và cha khác công ty ⇒ bước 2 báo lỗi, migration dừng: đó là dữ liệu hỏng phải xem, không tự sửa.
-- Chạy lại không sao (khoá đã kép thì bỏ qua). Chỉ SIẾT ràng buộc: bản cũ chạy song song vẫn ghi được dữ liệu đúng ⇒ không cần bảo trì.
-- Mỗi gói gọi cho schema của mình ở migration của gói (``CALL core.tenant_fks(ARRAY['<schema>'])``); bảng mới về sau khai thẳng khoá kép — tests/test_meta.py chặn.
SET LOCAL lock_timeout = '10s';

CREATE PROCEDURE core.tenant_fks(schemas TEXT[]) LANGUAGE plpgsql AS $$
DECLARE
    r RECORD;
    acts CONSTANT JSONB := '{"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE"}';
    on_delete TEXT;
BEGIN
    FOR r IN
        SELECT c.conname, c.conrelid::regclass AS src, c.confrelid::regclass AS dst, c.confrelid, sa.attname AS col, da.attname AS dcol,
               c.confdeltype, c.confupdtype, c.condeferrable, c.condeferred
        FROM pg_constraint c
        JOIN pg_class rel ON rel.oid = c.conrelid JOIN pg_namespace n ON n.oid = rel.relnamespace
        JOIN pg_attribute sa ON sa.attrelid = c.conrelid AND sa.attnum = c.conkey[1]
        JOIN pg_attribute da ON da.attrelid = c.confrelid AND da.attnum = c.confkey[1]
        WHERE c.contype = 'f' AND n.nspname = ANY(schemas) AND array_length(c.conkey, 1) = 1 AND sa.attname <> 'tenant_id'
          AND c.confrelid <> 'core.tenants'::regclass
          AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.conrelid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
          AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.confrelid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
        ORDER BY 2, 1
    LOOP
        IF r.confupdtype NOT IN ('a', 'r', 'c') THEN
            RAISE EXCEPTION 'Khoá ngoại % của %: ON UPDATE SET NULL / SET DEFAULT không chuyển tự động được — khai lại bằng tay', r.conname, r.src;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = r.confrelid AND i.indisunique AND i.indpred IS NULL AND i.indnkeyatts = 2
                         AND (SELECT array_agg(a.attname::text ORDER BY a.attname) FROM pg_attribute a
                              WHERE a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey::int2[])) = (SELECT array_agg(x ORDER BY x) FROM unnest(ARRAY['tenant_id', r.dcol::text]) x)) THEN
            EXECUTE format('ALTER TABLE %s ADD UNIQUE (tenant_id, %I)', r.dst, r.dcol);
        END IF;
        on_delete := CASE r.confdeltype WHEN 'n' THEN format('SET NULL (%I)', r.col) WHEN 'd' THEN format('SET DEFAULT (%I)', r.col) ELSE acts ->> r.confdeltype::text END;
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.src, r.conname);
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (tenant_id, %I) REFERENCES %s (tenant_id, %I) ON DELETE %s ON UPDATE %s %s',
                       r.src, r.conname, r.col, r.dst, r.dcol, on_delete, acts ->> r.confupdtype::text,
                       CASE WHEN r.condeferrable THEN 'DEFERRABLE ' || CASE WHEN r.condeferred THEN 'INITIALLY DEFERRED' ELSE 'INITIALLY IMMEDIATE' END ELSE '' END);
    END LOOP;
END $$;

CALL core.tenant_fks(ARRAY['core', 'admin', 'apps', 'asks', 'connectors', 'flow', 'kb', 'memory', 'papers', 'work', 'ledger']);
