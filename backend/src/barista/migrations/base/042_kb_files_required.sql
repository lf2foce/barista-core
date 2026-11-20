-- module: kb — bước 2/2 của 0122 cho kb.files — bỏ bytea, file_id bắt buộc; deployment: maintenance
-- Tách từ base/042_files_required.sql (0130, ghi chú 08/11; tệp đính kèm của Trao đổi ở talk/042_attachments_file_required.sql). TRƯỚC SQL này,
-- trong cùng giao dịch, kernel/migrate.py chạy bước dữ liệu ``files.move_bytea_into_store`` (thừa hưởng từ file gốc — đẩy MỌI thân bytea còn lại
-- vào kho, thêm dòng core.files, điền file_id). Còn dòng thiếu file_id ⇒ SET NOT NULL lỗi ⇒ cả giao dịch hoàn, không mất gì. Bản cũ (đọc / ghi
-- cột data) không chạy được trên schema này ⇒ chỉ phát hành bằng bảo trì (deployment.json, runbook 11).

ALTER TABLE kb.files ALTER COLUMN file_id SET NOT NULL;
ALTER TABLE kb.files DROP CONSTRAINT files_data_check;
ALTER TABLE kb.files DROP COLUMN data;
ALTER TABLE kb.files ADD CONSTRAINT files_size_check CHECK ((size_bytes >= 0) AND (size_bytes <= 31457280));
