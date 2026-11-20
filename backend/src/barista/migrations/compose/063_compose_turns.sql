-- module: compose — lý do: "Soạn với AI" thành một cuộc trò chuyện (như ChatGPT): mỗi bản soạn giữ các lượt người nói (yêu cầu đầu, lời sửa)
-- và kết quả từng lượt (xong / chưa soạn được + lý do + các bước đã chạy) để mở lại thấy đúng mạch. Một cột jsonb trên bảng sẵn có — RLS của
-- compose.sessions (chỉ chủ bản soạn) áp nguyên. CHỈ THÊM cột có mặc định: bản cũ còn chạy lúc chuyển không đọc cột này ⇒ không cần dừng dịch vụ.
-- Số 063 (không 062): nhánh khác đang giữ 062 của gói nền.
ALTER TABLE compose.sessions ADD COLUMN turns jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(turns) = 'array');
