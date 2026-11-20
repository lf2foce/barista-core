-- module: leadflow
-- 0204: CHIA LEAD THEO KẾ HOẠCH CHIẾN DỊCH. Một kế hoạch (mã ``mkt.campaigns.plan_code``, vd gói đồng chi trả với một đội — 0196) có luật chia riêng và
-- danh sách NGƯỜI THAM GIA riêng (chỉ người cùng góp chi phí mới nhận lead của kế hoạch). Ba cột, mặc định trống = như cũ:
--   rules.plan_code      luật cho một kế hoạch (trống = mọi kế hoạch); luật theo kế hoạch thắng luật theo [[offering|l]] / nguồn;
--   members.plan_code    người tham gia kế hoạch (trống = thành viên [[offering|l]] như cũ); khoá chính thêm cột này;
--   lead_state.plan_code kế hoạch của lead lúc vào — vòng quét chia lại / tính hạn theo đúng luật đó (lead đỗ / lead cũ: trống ⇒ luật như cũ).
-- CẦN DỪNG DỊCH VỤ (deployment.json): đổi khoá duy nhất của rules / members ⇒ câu ON CONFLICT của bản cũ chạy song song không còn khớp ràng buộc.
SET LOCAL lock_timeout = '10s';

ALTER TABLE leadflow.rules ADD COLUMN plan_code text NOT NULL DEFAULT '' CHECK (plan_code ~ '^[A-Z0-9_-]{0,40}$');
ALTER TABLE leadflow.rules DROP CONSTRAINT rules_scope_day;
ALTER TABLE leadflow.rules ADD CONSTRAINT rules_scope_day UNIQUE (tenant_id, offering_code, source, plan_code, effective_from);

ALTER TABLE leadflow.members ADD COLUMN plan_code text NOT NULL DEFAULT '' CHECK (plan_code ~ '^[A-Z0-9_-]{0,40}$');
ALTER TABLE leadflow.members DROP CONSTRAINT members_pkey;
ALTER TABLE leadflow.members ADD PRIMARY KEY (tenant_id, offering_code, plan_code, user_id);

ALTER TABLE leadflow.lead_state ADD COLUMN plan_code text NOT NULL DEFAULT '';
