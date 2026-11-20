-- module: memory, apps — lý do: 0140 (review an ninh 08/11) — bảng ghi LOẠI người làm (via) nhận thêm 'app' (app từ xa), để thứ app ghi không bị
-- dán nhãn "người". Mục nhớ do app ghi phải truy được nguồn như trợ lý (sources không rỗng) — chỉ người tự ghi mới được không nguồn.
-- CHỈ NỚI ràng buộc (NOT VALID — dòng cũ hiển nhiên thoả, không quét bảng); bản cũ chạy song song không ghi 'app' ⇒ không cần cửa sổ bảo trì.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conrelid::regclass AS tbl, conname FROM pg_constraint
             WHERE contype = 'c' AND conrelid IN ('memory.items'::regclass, 'apps.items'::regclass, 'apps.versions'::regclass)
               AND pg_get_constraintdef(oid) LIKE '%via%'
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
    END LOOP;
END $$;
ALTER TABLE memory.items ADD CONSTRAINT items_via_check CHECK (via IN ('human', 'agent', 'app')) NOT VALID;
ALTER TABLE memory.items ADD CONSTRAINT items_sources_check CHECK (via = 'human' OR jsonb_array_length(sources) > 0) NOT VALID;
ALTER TABLE apps.items ADD CONSTRAINT items_via_check CHECK (via IN ('human', 'agent', 'app')) NOT VALID;
ALTER TABLE apps.versions ADD CONSTRAINT versions_via_check CHECK (via IN ('human', 'agent', 'app')) NOT VALID;
