-- module: kb, talk — lý do: bước 2/2 của 0122 cho hai bảng đang giữ thân tệp — bỏ bytea, file_id bắt buộc; deployment: maintenance
-- 042 (0122): TRƯỚC SQL này, trong cùng giao dịch, kernel/migrate.py chạy bước dữ liệu ``files.move_bytea_into_store`` (đẩy từng thân bytea vào
-- kho, thêm dòng core.files, điền file_id). Còn dòng thiếu file_id ⇒ SET NOT NULL lỗi ⇒ cả giao dịch hoàn, không mất gì. Bản cũ (đọc / ghi
-- cột data) không chạy được trên schema này ⇒ chỉ phát hành bằng bảo trì (deployment.json, runbook 11).

ALTER TABLE kb.files ALTER COLUMN file_id SET NOT NULL;
ALTER TABLE kb.files DROP CONSTRAINT files_data_check;
ALTER TABLE kb.files DROP COLUMN data;
ALTER TABLE kb.files ADD CONSTRAINT files_size_check CHECK ((size_bytes >= 0) AND (size_bytes <= 31457280));

ALTER TABLE talk.attachments ALTER COLUMN file_id SET NOT NULL;
ALTER TABLE talk.attachments DROP CONSTRAINT attachments_size_check;
ALTER TABLE talk.attachments DROP COLUMN data;
ALTER TABLE talk.attachments ADD CONSTRAINT attachments_size_check CHECK ((size_bytes > 0) AND (size_bytes <= 20971520));
