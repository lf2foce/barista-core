-- module: mkt — 0198: tầng QUẢNG CÁO dưới chiến dịch. `mkt.ads` = một quảng cáo trên nền tảng (mã nền tảng, tên, nhóm quảng cáo, nội dung: tiêu đề, thân, loại,
-- ảnh / video / đường dẫn); `mkt.ad_days` = số của một quảng cáo trong một ngày (chi phí VND CHÍNH XÁC chưa làm tròn + tiền gốc, hiển thị, nhấp, lead form, inbox).
-- Dòng chiến dịch × ngày (`mkt.spend`) của ngày có số theo quảng cáo là TỔNG của các dòng con, làm tròn VND MỘT lần — lệnh `mkt.record_ad_day` ghi cả hai trong cùng
-- giao dịch (0195: không lệch vì làm tròn từng dòng). Trống ≠ 0: lead / inbox trống = nền tảng chưa báo.
-- Quyền theo chiến dịch (RLS cha → con). Bảng MỚI ⇒ không cần dừng dịch vụ.
SET LOCAL lock_timeout = '10s';

CREATE TABLE mkt.ads (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    campaign_id   uuid NOT NULL REFERENCES mkt.campaigns(id),
    code          text NOT NULL CHECK (code ~ '^[A-Za-z0-9_-]{1,40}$'),
    name          text NOT NULL DEFAULT '',
    adset_code    text NOT NULL DEFAULT '' CHECK (adset_code ~ '^[A-Za-z0-9_-]{0,40}$'),
    adset_name    text NOT NULL DEFAULT '',
    title         text NOT NULL DEFAULT '',
    body          text NOT NULL DEFAULT '',
    media_type    text NOT NULL DEFAULT '' CHECK (media_type IN ('', 'image', 'video', 'carousel', 'other')),
    thumbnail_url text NOT NULL DEFAULT '',
    video_url     text NOT NULL DEFAULT '',
    link_url      text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code)
);
CREATE INDEX idx_ads_campaign ON mkt.ads (tenant_id, campaign_id);
CALL core.secure_child('mkt.ads', 'mkt.campaigns', 'campaign_id');
GRANT SELECT, INSERT, UPDATE ON mkt.ads TO barista_app;

CREATE TABLE mkt.ad_days (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    ad_id           uuid NOT NULL REFERENCES mkt.ads(id),
    day             date NOT NULL,
    amount_vnd      numeric(20, 4) NOT NULL DEFAULT 0 CHECK (amount_vnd >= 0),
    amount_native   numeric(20, 4) CHECK (amount_native >= 0),
    currency_native text CHECK (currency_native ~ '^[A-Z]{3}$'),
    fx_rate         numeric(20, 6) CHECK (fx_rate > 0),
    impressions     bigint CHECK (impressions >= 0),
    clicks          bigint CHECK (clicks >= 0),
    leads_platform  integer CHECK (leads_platform >= 0),
    inbox           integer CHECK (inbox >= 0),
    CONSTRAINT ad_days_native_complete CHECK ((amount_native IS NULL) = (currency_native IS NULL)),
    UNIQUE (ad_id, day)
);
CREATE INDEX idx_ad_days_day ON mkt.ad_days (tenant_id, day);
CALL core.secure_child('mkt.ad_days', 'mkt.ads', 'ad_id');
GRANT SELECT, INSERT, UPDATE ON mkt.ad_days TO barista_app;

-- Ngày có lead / inbox mà không có chi phí (quảng cáo đã tắt nhưng lead về muộn) vẫn là một dòng chiến dịch × ngày chi 0.
ALTER TABLE mkt.spend ADD COLUMN inbox integer CHECK (inbox >= 0);
