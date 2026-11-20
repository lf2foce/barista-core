-- module: flow
-- 089 (ADR 0222, barista-docs): DÒNG THỜI GIAN CỦA KẾ HOẠCH — việc có thể có NGÀY BẮT ĐẦU, NHÓM (giai đoạn) và PHỤ THUỘC (chờ việc khác trong CÙNG kế hoạch).
-- CHỈ THÊM cột tuỳ chọn có mặc định ⇒ bản cũ chạy song song không đọc / ghi gì ở đây (INSERT không nêu cột ⇒ mặc định), không cần dừng dịch vụ.
--   start_on   : NULL = bắt đầu từ ngày tạo việc (thanh vẽ từ ngày tạo tới hạn). Không sau hạn.
--   phase      : nhãn nhóm tự do ("Chuẩn bị", "Chạy") — trống = nhóm theo người làm. Không bảng riêng: mốc kế hoạch vẫn là flow.plans.due_on.
--   depends_on : mã các việc phải xong TRƯỚC (cùng kế hoạch — code kiểm, không chéo kế hoạch ở bản đầu; chu trình bị chặn ở code, dưới khoá kế hoạch).
--                Mảng, không bảng con: việc chỉ đọc trong ngữ cảnh kế hoạch, quyền thấy = quyền thấy việc (RLS sẵn của flow.tasks).

SET LOCAL lock_timeout = '10s';

ALTER TABLE flow.tasks
    ADD COLUMN start_on    date,
    ADD COLUMN phase       text   NOT NULL DEFAULT '',
    ADD COLUMN depends_on  uuid[] NOT NULL DEFAULT '{}';

ALTER TABLE flow.tasks
    ADD CONSTRAINT tasks_start_before_due CHECK (start_on IS NULL OR due_on IS NULL OR start_on <= due_on),
    ADD CONSTRAINT tasks_phase_len CHECK (char_length(phase) <= 60),
    ADD CONSTRAINT tasks_depends_len CHECK (cardinality(depends_on) <= 20);
