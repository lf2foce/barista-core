-- module: crm
-- ADR 0246 — LEAD NẰM IM QUÁ LÂU Ở MỘT CHẶNG: mỗi chặng có thể đặt số ngày tối đa lead được nằm yên (không chuyển chặng, không chăm sóc). Quá ngưỡng ⇒ thẻ / danh sách
-- hiện "nằm im N ngày", Báo cáo chạy song song có phát hiện. NULL = chặng không đặt ngưỡng (không báo). Ngưỡng là dữ liệu công ty, sửa ở Chặng của lead.
-- CHỈ THÊM: cột cho phép NULL ⇒ bản cũ chạy song song không đọc / ghi, không bảo trì.

SET LOCAL lock_timeout = '10s';

ALTER TABLE crm.stages ADD COLUMN idle_days smallint CHECK (idle_days BETWEEN 1 AND 365);
GRANT UPDATE (idle_days) ON crm.stages TO barista_app;
COMMENT ON COLUMN crm.stages.idle_days IS
    'Lead nằm ở chặng này quá ngần này ngày mà không chuyển chặng, không chăm sóc ⇒ "nằm im" (ADR 0246). NULL = không đặt ngưỡng';
