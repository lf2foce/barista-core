-- module: mkt — 0196: ĐỒNG CHI TRẢ chi phí quảng cáo. Một đội hoặc một người góp X% chi phí của các chiến dịch thuộc một kế hoạch (``plan_code``) hoặc của
-- một chiến dịch, trong một khoảng ngày; phần còn lại công ty chịu. Tỷ lệ là DỮ LIỆU có ngày hiệu lực (bất biến 5) — không có "7:3" trong code; công
-- ty khác đặt 60/40, 50/50… Khoản bên góp phải trả là SỐ TÍNH từ chi phí thật × tỷ lệ (``mkt.cost_share_statement``); ghi khoản phải thu / trừ hoa hồng
-- là việc của sổ cái / gói dùng con số đó, không ở bảng này.
-- Bảng MỚI (bản cũ không biết) ⇒ không cần dừng dịch vụ.
SET LOCAL lock_timeout = '10s';

CREATE TABLE mkt.cost_shares (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    plan_code   text NOT NULL DEFAULT '' CHECK (plan_code ~ '^[A-Za-z0-9_-]{0,40}$'),
    campaign_id uuid REFERENCES mkt.campaigns(id),
    team_id     uuid REFERENCES core.teams(id),
    user_id     uuid REFERENCES core.users(id),
    share_bps   integer NOT NULL CHECK (share_bps BETWEEN 1 AND 10000),
    valid_from  date NOT NULL,
    valid_to    date,
    note        text NOT NULL DEFAULT '',
    created_by  uuid REFERENCES core.users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cost_shares_one_scope CHECK ((plan_code <> '') <> (campaign_id IS NOT NULL)),
    CONSTRAINT cost_shares_one_party CHECK ((team_id IS NOT NULL) <> (user_id IS NOT NULL)),
    CONSTRAINT cost_shares_dates CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX idx_cost_shares_plan ON mkt.cost_shares (tenant_id, plan_code) WHERE plan_code <> '';
CREATE INDEX idx_cost_shares_campaign ON mkt.cost_shares (tenant_id, campaign_id) WHERE campaign_id IS NOT NULL;
CALL core.secure_table('mkt.cost_shares', 'mkt', 'user_id', 'team_id');
GRANT SELECT, INSERT, UPDATE ON mkt.cost_shares TO barista_app;
