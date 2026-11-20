-- module: talk — bước 2/2 của 0122 cho talk.attachments — bỏ bytea, file_id bắt buộc; deployment: maintenance
-- Tách từ base/042_files_required.sql (0130, ghi chú 08/11). Bước dữ liệu ``files.move_bytea_into_store`` chạy lại ngay trước SQL này (thừa hưởng từ
-- file gốc; chỉ làm phần chưa làm). Còn dòng thiếu file_id ⇒ SET NOT NULL lỗi ⇒ cả giao dịch hoàn.

ALTER TABLE talk.attachments ALTER COLUMN file_id SET NOT NULL;
ALTER TABLE talk.attachments DROP CONSTRAINT attachments_size_check;
ALTER TABLE talk.attachments DROP COLUMN data;
ALTER TABLE talk.attachments ADD CONSTRAINT attachments_size_check CHECK ((size_bytes > 0) AND (size_bytes <= 20971520));
