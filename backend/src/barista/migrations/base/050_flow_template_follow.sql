-- module: flow
-- 0142: mẫu quy trình quyết hồ sơ nào TRỢ LÝ CẦM nhiều ngày (mở hồ sơ theo mẫu có cờ này ⇒ một việc theo dõi loại "case" cho người phụ trách).
-- Chỉ thêm cột có mặc định — bản cũ chạy song song không đọc cột, không cần cửa sổ bảo trì.
ALTER TABLE flow.templates ADD COLUMN follow boolean NOT NULL DEFAULT false;
