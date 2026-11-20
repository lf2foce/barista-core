-- module: core — lý do: 0143 THANG TIN CẬY của trợ lý. Mức tự chủ theo lệnh là chính sách công ty (core.agent_policies đã có — tới nay chỉ siết);
-- nới trên trần ("allow" cho lệnh khai promotable) cần: thành tích đề xuất đủ ngưỡng CÔNG TY đặt (core.agent_trust), trần số lần / ngày và tiền
-- (cột mới trên chính sách), căn cứ lúc đặt (basis), ai đặt. Bộ đếm chạy thẳng theo ngày (core.agent_autoruns) để executor kiểm trần trong cùng
-- giao dịch, không đọc nhật ký (RLS nhật ký theo người xem). Chỉ thêm cột có mặc định + bảng mới ⇒ bản cũ chạy song song không ảnh hưởng.
ALTER TABLE core.agent_policies
    ADD COLUMN daily_cap integer CHECK (daily_cap IS NULL OR daily_cap >= 1),
    ADD COLUMN money_cap_vnd bigint CHECK (money_cap_vnd IS NULL OR money_cap_vnd >= 0),
    ADD COLUMN basis jsonb,
    ADD COLUMN set_by uuid REFERENCES core.users(id),
    ADD COLUMN set_at timestamptz NOT NULL DEFAULT now();

CREATE TABLE core.agent_trust (
    tenant_id uuid PRIMARY KEY DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    min_proposals integer NOT NULL DEFAULT 20 CHECK (min_proposals >= 1),
    min_approved_pct integer NOT NULL DEFAULT 95 CHECK (min_approved_pct BETWEEN 0 AND 100),
    window_days integer NOT NULL DEFAULT 30 CHECK (window_days BETWEEN 7 AND 365),
    max_errors_7d integer NOT NULL DEFAULT 3 CHECK (max_errors_7d >= 1),
    updated_by uuid REFERENCES core.users(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CALL core.secure_tenant('core.agent_trust');
GRANT SELECT, INSERT, UPDATE ON core.agent_trust TO barista_app;

CREATE TABLE core.agent_autoruns (
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    command text NOT NULL,
    day date NOT NULL DEFAULT core.today(),
    runs integer NOT NULL DEFAULT 0,
    money_vnd bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, command, day)
);
CALL core.secure_tenant('core.agent_autoruns');
GRANT SELECT, INSERT, UPDATE ON core.agent_autoruns TO barista_app;

-- Luật duyệt cho hai lệnh mới ở MỌI công ty đang có chuỗi "access_change" (deploy chỉ chạy migration — cài mặc định không tự chạy lại; như mig 020).
-- Công ty đã cố ý bỏ luật (có nhật ký cài) giữ nguyên; lệnh tự từ chối khi không có luật (fail closed — admin._rule_required).
INSERT INTO core.approval_rules (tenant_id, command, chain)
SELECT c.tenant_id, x.command, 'access_change' FROM core.approval_chains c
CROSS JOIN (VALUES ('admin.set_agent_policy'), ('admin.set_agent_trust')) AS x(command)
WHERE c.key = 'access_change'
  AND NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = c.tenant_id AND i.kind = 'rule' AND i.key = x.command)
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs (tenant_id, kind, key, pack, version)
SELECT c.tenant_id, 'rule', x.command, 'base', NULL FROM core.approval_chains c
CROSS JOIN (VALUES ('admin.set_agent_policy'), ('admin.set_agent_trust')) AS x(command)
WHERE c.key = 'access_change' ON CONFLICT DO NOTHING;
