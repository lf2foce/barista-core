-- module: schedule
-- 0179 — LỊCH NẠP TỪ HỆ KHÁC giữ giờ gốc và người làm: cùng bảng schedule.events (một lịch cho mọi thứ chiếm thời gian — 0158), đánh dấu nguồn bằng
-- ``source = 'import.<hệ nguồn>'`` + ``source_ref`` = mã dòng nguồn (chỉ mục duy nhất đã có chặn nạp trùng), không giữ chỗ nguồn lực, không mời, không báo.
-- Người làm không khớp thành viên ⇒ ``author_ref`` (mã người ở hệ nguồn); người tham dự không khớp ⇒ ``attendee_refs``. Không dòng nào bị bỏ vì thiếu người.
-- CHỈ THÊM: cột có mặc định / cho phép trống, CHECK chỉ ràng buộc dòng nạp (mọi dòng đang có không có tiền tố 'import.') ⇒ bản cũ chạy song song vẫn ghi
-- được, không cần cửa sổ bảo trì.

SET LOCAL lock_timeout = '10s';

ALTER TABLE schedule.events ADD COLUMN author_ref text NOT NULL DEFAULT '',
                            ADD COLUMN attendee_refs text[] NOT NULL DEFAULT '{}',
                            ADD COLUMN imported_by uuid REFERENCES core.users(id),
                            ADD COLUMN imported_at timestamptz,
                            ADD CONSTRAINT events_import_marked CHECK ((source LIKE 'import.%') = (imported_at IS NOT NULL));
COMMENT ON COLUMN schedule.events.author_ref IS 'Mã người làm ở hệ nguồn khi không khớp thành viên — 0179';
COMMENT ON COLUMN schedule.events.imported_at IS 'Lúc nạp từ hệ khác (source = import.<hệ nguồn>); giờ của lịch là giờ GỐC — 0179';
