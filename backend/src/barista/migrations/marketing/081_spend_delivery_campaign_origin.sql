-- module: mkt — 0195: (1) chi phí theo ngày mang số giao nhận (lượt hiển thị, lượt nhấp ⇒ CTR / CPC / CPM) và TIỀN GỐC của nền tảng (tài khoản quảng cáo
-- tính USD: tiền gốc có xu, VND là số quy đổi ⇒ giữ tiền gốc + tỷ giá + nguồn tỷ giá để khớp tuyệt đối, VND làm tròn MỘT lần ở dòng chiến dịch × ngày);
-- (2) chiến dịch giữ TÊN GỐC (tên lần đầu hệ biết, không đổi khi đổi tên — nền tảng cho đổi tên + tái dùng chiến dịch) và MÃ KẾ HOẠCH (nhóm các
-- chiến dịch cùng một kế hoạch: người chạy, [[offering|l]], đội — một kế hoạch có thể nhiều chiến dịch trên nền tảng).
-- Chỉ THÊM cột cho phép trống / có mặc định ⇒ bản cũ chạy song song không vỡ ⇒ không cần dừng dịch vụ.
SET LOCAL lock_timeout = '10s';

ALTER TABLE mkt.spend
    ADD COLUMN impressions bigint CHECK (impressions >= 0),
    ADD COLUMN clicks bigint CHECK (clicks >= 0),
    ADD COLUMN amount_native numeric(20, 4) CHECK (amount_native >= 0),
    ADD COLUMN currency_native text CHECK (currency_native ~ '^[A-Z]{3}$'),
    ADD COLUMN fx_rate numeric(20, 6) CHECK (fx_rate > 0),
    ADD COLUMN fx_source text NOT NULL DEFAULT '',
    ADD CONSTRAINT spend_native_complete CHECK ((amount_native IS NULL) = (currency_native IS NULL));   -- lượt nhấp (mọi loại) có thể > lượt hiển thị: không ép

ALTER TABLE mkt.campaigns
    ADD COLUMN original_name text CHECK (original_name IS NULL OR btrim(original_name) <> ''),
    ADD COLUMN plan_code text NOT NULL DEFAULT '' CHECK (plan_code ~ '^[A-Za-z0-9_-]{0,40}$');
CREATE INDEX idx_campaigns_plan ON mkt.campaigns (tenant_id, plan_code) WHERE plan_code <> '';
