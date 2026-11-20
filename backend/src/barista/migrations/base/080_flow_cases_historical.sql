-- module: flow
-- 0185 — hồ sơ tự mở cho đối tượng NẠP TỪ HỆ KHÁC (giao dịch cũ…) là tham chiếu: vòng quét "hồ sơ kẹt chặng" và màn Cần xử lý không xét (không việc "hồ sơ kẹt",
-- không thông báo cho hàng nghìn giao dịch cũ sau khi nạp một tuần).
-- CHỈ THÊM: cột có mặc định.

SET LOCAL lock_timeout = '10s';

ALTER TABLE flow.cases ADD COLUMN historical boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN flow.cases.historical IS 'Hồ sơ mở trong một lần nạp lịch sử từ hệ khác (0185) — tham chiếu, không báo kẹt chặng';
