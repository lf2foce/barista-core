-- module: compose — lý do: người dùng bấm "Dừng" giữa lượt soạn của Soạn với AI ⇒ bản soạn ở trạng thái "đã dừng" (giữ phần đã có, không phải "lỗi").
-- Chỉ nới ràng buộc trạng thái thêm một giá trị: bản app cũ đang chạy lúc chuyển không ghi giá trị này nên vẫn chạy bình thường (không cần dừng dịch vụ).
ALTER TABLE compose.sessions DROP CONSTRAINT IF EXISTS sessions_status_check;   -- IF EXISTS: chạy lại an toàn
ALTER TABLE compose.sessions ADD CONSTRAINT sessions_status_check CHECK (status IN ('drafting', 'ready', 'failed', 'stopped'));
