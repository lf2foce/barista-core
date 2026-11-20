-- module: schedule
-- 0240 — MÀU của loại lịch là dữ liệu công ty: màn Lịch (khối ``calendar``) tô mục theo loại; app không viết cứng màu nào cho loại nào.
-- '' = chưa chọn ⇒ server lấy màu theo thứ tự loại (bảng màu cố định của lõi). CHỈ THÊM: cột có mặc định, bản cũ chạy song song vẫn ghi được.
ALTER TABLE schedule.kinds ADD COLUMN tone TEXT NOT NULL DEFAULT ''
    CHECK (tone IN ('', 'blue', 'green', 'orange', 'red', 'purple', 'teal', 'pink', 'indigo', 'gray'));
