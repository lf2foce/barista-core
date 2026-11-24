-- module: core
-- ADR 0260: bộ đếm LƯỢT ĐỌC (0163, ``core.usage_reads``) đếm theo GÓI — không biết màn nào được mở. Quyết định bỏ / gộp / viết giải thích cho màn phải theo số dùng
-- thật ⇒ thêm TÊN TRUY VẤN vào khoá đếm (mỗi màn mở bằng một truy vấn). Một nguồn: báo cáo theo gói đang chạy vẫn đọc cùng bảng (gói = cột ``pack`` giữ nguyên;
-- cộng theo gói không đổi); dòng cũ mang ``query = ''`` (không rõ màn). Không ghi nội dung hay tham số — chỉ tên truy vấn.
-- Đổi khoá chính ⇒ câu ghi của BẢN CŨ chạy song song (``ON CONFLICT`` theo khoá cũ) lỗi trong lúc chuyển bản: bộ đếm nuốt lỗi, bỏ lô (kernel/usage.py) — mất vài
-- phút số đếm, truy vấn của người dùng không ảnh hưởng ⇒ không cần bảo trì.
SET LOCAL lock_timeout = '10s';
ALTER TABLE core.usage_reads ADD COLUMN query text NOT NULL DEFAULT '' CHECK (length(query) <= 80);
ALTER TABLE core.usage_reads DROP CONSTRAINT usage_reads_pkey;
ALTER TABLE core.usage_reads ADD PRIMARY KEY (tenant_id, day, pack, user_id, via, query);
