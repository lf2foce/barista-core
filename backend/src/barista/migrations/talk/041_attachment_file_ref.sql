-- module: talk — 0122 bước 1/2 cho tệp đính kèm của Trao đổi: cột file_id (cho phép trống) ở talk.attachments trỏ sổ tệp core.files của lõi; deployment: maintenance
-- Tách từ base/041_files.sql (0130, ghi chú 08/11; sổ tệp ở base/041_file_store.sql — chạy trước, gói nền luôn chạy trước gói).

ALTER TABLE talk.attachments ADD COLUMN file_id uuid REFERENCES core.files(id);
CREATE UNIQUE INDEX idx_talk_attachments_file ON talk.attachments USING btree (file_id);
