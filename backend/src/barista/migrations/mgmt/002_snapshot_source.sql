-- module: mgmt
-- 002 (0213, rà của Codex 14/11): bản chốt ghi "số lấy theo cách nào" — phiên bản bề mặt lõi (`barista.api`) + phiên bản từng gói lúc chụp, truy vấn +
-- đầu vào của trang, mốc lấy số (giờ bắt đầu giao dịch chốt), trang bị bỏ qua (gói tắt…) và lý do. Chỉ thêm cột có mặc định ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
ALTER TABLE mgmt.snapshots ADD COLUMN source JSONB NOT NULL DEFAULT '{}'::jsonb;
COMMENT ON COLUMN mgmt.snapshots.source IS
    'Nguồn gốc bản chụp: {api, packs: {gói: phiên bản}, query, input, as_of, skipped: [trang (lý do)]} — đủ để biết số lấy theo cách nào';
