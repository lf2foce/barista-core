-- module: compose — 0231: "Soạn với AI" là ấn phẩm MARKETING (tờ rơi, tờ giới thiệu, slide, bài đăng). Nới ràng buộc loại bản soạn để thêm
-- `flyer` (tờ rơi) và `post` (bài đăng); loại cũ giữ nguyên (bản soạn cũ + điểm cắm của gói). Chỉ NỚI ràng buộc: bản đang chạy chỉ ghi các loại cũ
-- ⇒ không cần cửa sổ bảo trì.
ALTER TABLE compose.sessions DROP CONSTRAINT sessions_kind_check;
ALTER TABLE compose.sessions ADD CONSTRAINT sessions_kind_check CHECK (kind IN ('document', 'brochure', 'slides', 'flyer', 'post'));
