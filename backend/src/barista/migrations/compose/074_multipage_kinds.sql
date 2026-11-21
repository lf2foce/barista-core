-- module: compose — ADR 0252: hai loại ấn phẩm NHIỀU TRANG CỐ ĐỊNH (`memo` = Hồ sơ chào bán 10 trang, `teaser` = Tóm tắt 4 trang; mẫu là dữ liệu ở layout.TEMPLATES).
-- Chỉ NỚI ràng buộc loại bản soạn; bản đang chạy chỉ ghi các loại cũ ⇒ không cần cửa sổ bảo trì.
ALTER TABLE compose.sessions DROP CONSTRAINT sessions_kind_check;
ALTER TABLE compose.sessions ADD CONSTRAINT sessions_kind_check CHECK (kind IN ('document', 'brochure', 'slides', 'flyer', 'post', 'memo', 'teaser'));
