-- module: kb — dấu SHA-256 của file gốc để biết file vừa thả vào kho đã có chưa (0225); cột thêm, trống được — bản cũ vẫn chạy (không cần bảo trì)
-- Tệp ghi lúc nào cũng đã có sha256 ở sổ tệp (core.files — 0122 §7); chép sang đây để kiểm trùng bằng một chỉ mục thay vì băm lại từng tệp.
ALTER TABLE kb.files ADD COLUMN sha256 text;
UPDATE kb.files k SET sha256 = f.sha256 FROM core.files f WHERE f.id = k.file_id;
CREATE INDEX files_sha256_idx ON kb.files (tenant_id, sha256) WHERE sha256 IS NOT NULL;
