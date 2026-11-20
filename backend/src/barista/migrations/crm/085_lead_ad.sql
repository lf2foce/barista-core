-- module: crm
-- 0210: LEAD NHỚ MẪU QUẢNG CÁO đưa khách tới (không chỉ chiến dịch) — để biết MẪU nào ra khách thật (lead vào CRM, khách quan tâm, giao dịch), không
-- chỉ mẫu nào Meta báo nhiều "lead form". Mã quảng cáo do nguồn lead gửi (vd ad_id của Meta); mkt tra ra mã nội bộ qua điểm cắm crm.campaign (không
-- khoá ngoại chéo module — như campaign_id). Cột trống = không biết mẫu (nguồn cũ / nhập tay). Bản cũ chạy song song không ghi cột này ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
ALTER TABLE crm.leads ADD COLUMN ad_id uuid;
CREATE INDEX idx_leads_ad ON crm.leads (ad_id) WHERE ad_id IS NOT NULL;
COMMENT ON COLUMN crm.leads.ad_id IS 'Mẫu quảng cáo đưa khách tới (mkt.ads.id, 0210) — trống = không biết mẫu';
