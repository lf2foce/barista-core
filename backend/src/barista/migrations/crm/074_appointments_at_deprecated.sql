-- module: crm
-- 0158 (dọn nợ): giờ của hẹn khách nằm ở lịch chung (schedule.events, nguồn 'crm.appointment'); crm.appointments.at chỉ còn là BẢN CHÉP cho bản cũ chạy song song
-- lúc chuyển và cho vòng quét crm.heal_appointments vá hẹn do bản cũ ghi. Từ bản này code mới KHÔNG ghi cột (đặt hẹn để rỗng, dời / nghe lịch dời không cập nhật)
-- ⇒ cho phép rỗng và ghi chú ngừng dùng. Chỉ nới ràng buộc + ghi chú: bản cũ vẫn ghi ``at`` bình thường ⇒ không bảo trì. Cột và hai chỉ mục theo ``at`` còn nguyên
-- (xoá ở bản sau khi không còn bản cũ nào chạy và cửa sổ vá 30 ngày đã qua).
SET LOCAL lock_timeout = '10s';
ALTER TABLE crm.appointments ALTER COLUMN at DROP NOT NULL;
COMMENT ON COLUMN crm.appointments.at IS 'NGỪNG DÙNG (0158, crm/074): giờ hẹn ở lịch chung (schedule.events nguồn crm.appointment). Code mới không ghi / không đọc; chỉ dòng do bản cũ ghi còn giá trị, cho vòng quét crm.heal_appointments. Sẽ xoá khi hết bản cũ';
