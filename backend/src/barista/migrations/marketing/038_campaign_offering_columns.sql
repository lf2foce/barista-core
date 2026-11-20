-- module: mkt — đổi tên cột "thứ khách quan tâm" của chiến dịch (project_* → offering_*, 0110 / 0124, bất biến 11). Tách từ
-- crm/038_rename_offering_columns.sql (0130, ghi chú 08/11; gói marketing cần gói crm nên chạy sau). Bảo trì (deployment.json): bản cũ chạy
-- song song sẽ vỡ ngay vì cột đổi tên.

ALTER TABLE mkt.campaigns RENAME COLUMN project_id TO offering_id;
ALTER TABLE mkt.campaigns RENAME COLUMN project_code TO offering_code;
ALTER TABLE mkt.campaigns RENAME CONSTRAINT campaigns_project_code_not_null TO campaigns_offering_code_not_null;
