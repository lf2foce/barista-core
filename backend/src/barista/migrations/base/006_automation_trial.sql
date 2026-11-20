-- module: flow
-- 006 (0090 bổ sung): luật tự động hoá mới đặt chạy ở chế độ CHẠY THỬ — khớp sự kiện thì chỉ GHI "sẽ báo ai / sẽ tạo việc gì", không gửi, không
-- tạo; quản trị xem các lần khớp rồi mới bật chạy thật (luật bật lên không "bắn" ngay vào người nhận). Chỉ THÊM / nới ràng buộc.

ALTER TABLE flow.automations ADD COLUMN trial BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE flow.automation_runs DROP CONSTRAINT automation_runs_status_check;
ALTER TABLE flow.automation_runs ADD CONSTRAINT automation_runs_status_check CHECK (status IN ('done', 'skipped', 'failed', 'trial'));
