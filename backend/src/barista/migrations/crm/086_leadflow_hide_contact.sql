-- module: leadflow
-- 0214: CHE LIÊN HỆ TỚI KHI BẤM NHẬN. Luật chia có bước bấm nhận (accept_min > 0) được bật thêm "che SĐT / liên hệ": lead đang chờ nhận thì mọi người
-- (kể cả người được chia) chỉ thấy số đã che 4 chữ số cuối — bấm Nhận là cam kết, cam kết rồi mới thấy số. Một cột trên luật (chính sách là dữ liệu),
-- lead mang theo trong bản chụp luật lúc chia. Bản cũ chạy song song không đọc / ghi cột này (mặc định false = như cũ) ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
ALTER TABLE leadflow.rules ADD COLUMN hide_contact boolean NOT NULL DEFAULT false;
