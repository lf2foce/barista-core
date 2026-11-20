-- module: core
-- 0185 — NẠP LỊCH SỬ từ hệ khác: sự kiện phát ra trong một lần nạp (``executor.run(historical=True)`` / ``imports.run_import(historical=True)``) mang dấu này. Bên nghe
-- của sự kiện đó chạy cùng chế độ (``events._deliver``): không thông báo, không mở việc theo dõi, sự kiện nó phát tiếp cũng mang dấu — dữ liệu hệ cũ là tham chiếu,
-- không bao giờ khởi động nhắc việc / chuyển người / trợ lý.
-- CHỈ THÊM: cột có mặc định (bản cũ chạy song song ghi sự kiện không dấu như trước).

SET LOCAL lock_timeout = '10s';

ALTER TABLE core.events ADD COLUMN historical boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN core.events.historical IS 'Sự kiện của một lần nạp lịch sử từ hệ khác (0185): bên nghe chạy ở chế độ nạp lịch sử — không thông báo, không việc theo dõi';
