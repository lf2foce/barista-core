-- module: word
-- 002 (0251 lát 2 — màn Soạn thảo): CUỘC TRÒ CHUYỆN với agent gắn theo VĂN BẢN — mở lại văn bản là thấy lại đúng phần đã nhắn (như phiên của Soạn với AI).
-- Danh sách lượt [{role: "user"|"agent", text, note?, at}], giữ tối đa 100 lượt gần nhất (code cắt). Cùng RLS chủ văn bản của bảng (chỉ người tạo đọc / ghi).
-- CHỈ THÊM: cột có mặc định hằng (Postgres chỉ ghi siêu dữ liệu, không viết lại bảng) ⇒ không cần cửa sổ bảo trì.
ALTER TABLE word.documents ADD COLUMN chat jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(chat) = 'array');
