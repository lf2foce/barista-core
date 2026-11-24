-- module: hr
-- Rà schema 24/11/2026 (điểm 2): hai luật của hồ sơ làm việc do trigger TỰ TRUY VẤN giữ (hr.check_employment — 022) — hai giao dịch ghi cùng lúc đều không thấy
-- dòng của nhau nên cả hai lọt: (a) một người hai hồ sơ chồng ngày trong cùng pháp nhân; (b) một mã nhân viên thuộc hai người trong cùng pháp nhân (dễ gặp
-- nhất khi máy tự đặt "mã kế tiếp" cho hai người mới cùng lúc). Từ bản này DB giữ bằng ràng buộc loại trừ, như phân công (hr/068) và lịch sử đội (base/112).
-- Pháp nhân trống tính là một "pháp nhân chưa xác định" (như IS NOT DISTINCT FROM của trigger cũ). Dữ liệu đang có vi phạm ⇒ migration dừng (dữ liệu phải xem).
-- Lệnh ghi hồ sơ vẫn báo lỗi dễ hiểu trước khi tới DB; chỉ siết ràng buộc ⇒ không cần bảo trì.
SET LOCAL lock_timeout = '10s';
DROP TRIGGER employment_valid ON hr.employments;
DROP FUNCTION hr.check_employment();
ALTER TABLE hr.employments
    ADD CONSTRAINT employments_one_per_day EXCLUDE USING gist (
        tenant_id WITH =, party_id WITH =, (coalesce(entity_id, '00000000-0000-0000-0000-000000000000'::uuid)) WITH =,
        daterange(started_on, ended_on, '[]') WITH &&),
    ADD CONSTRAINT employments_code_one_person EXCLUDE USING gist (
        tenant_id WITH =, (coalesce(entity_id, '00000000-0000-0000-0000-000000000000'::uuid)) WITH =, employee_code WITH =, party_id WITH <>);
