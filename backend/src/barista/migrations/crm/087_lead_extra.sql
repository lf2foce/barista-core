-- module: crm
-- 0060 lát 1: giá trị TRƯỜNG TUỲ BIẾN của lead (định nghĩa ở core.field_defs, đối tượng "lead") — {khoá: giá trị đã kiểm kiểu}. Khác ``interest`` (thuộc tính
-- nhu cầu do GÓI NGÀNH khai trong code): ``extra`` là trường do CÔNG TY tự định nghĩa. Bản cũ chạy song song không đọc / ghi cột này (mặc định {}) ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
ALTER TABLE crm.leads ADD COLUMN extra jsonb NOT NULL DEFAULT '{}'::jsonb;
