-- module: leadflow, crm — lý do: 0152 — chia lead hơn hệ cũ: bằng chứng liên hệ là luật, nhắc trước hạn + báo trưởng nhóm + ân hạn trước thu hồi,
-- lịch nghỉ lễ trong đồng hồ hạn, trực ngoài giờ, bước nhận lead, khách quay lại là sự kiện, người lọc lead, thành viên [[offering|l]] có hạng +
-- trọng số, công tắc "chỉ thành viên" (chính sách hạn chế trên crm.leads).
-- CHỈ THÊM: cột mới có mặc định giữ đúng hành vi cũ (bằng chứng = mọi liên hệ, không nhắc, không ân hạn, ngoài giờ = hạn bắt đầu giờ mở kế tiếp,
-- không bước nhận, người nhận theo đội); CHECK nới thêm giá trị; bảng mới; chính sách hạn chế chỉ chặn khi công ty BẬT công tắc (mặc định tắt)
-- ⇒ bản cũ chạy song song lúc chuyển vẫn đúng, không cần cửa sổ bảo trì.

-- ── Luật: thêm cột (mỗi luật vẫn là một dòng có ngày hiệu lực; lead đã chia giữ bản chụp) ──
ALTER TABLE leadflow.rules
    ADD COLUMN calendar text NOT NULL DEFAULT '',
    ADD COLUMN contact_proof text[] NOT NULL DEFAULT ARRAY['any']::text[],
    ADD COLUMN min_talk_s integer NOT NULL DEFAULT 0,
    ADD COLUMN remind_before_min integer NOT NULL DEFAULT 0,
    ADD COLUMN escalate boolean NOT NULL DEFAULT false,
    ADD COLUMN grace_min integer NOT NULL DEFAULT 0,
    ADD COLUMN off_hours text NOT NULL DEFAULT 'defer',
    ADD COLUMN duty_team_id uuid,
    ADD COLUMN duty_days smallint[] NOT NULL DEFAULT ARRAY[1, 2, 3, 4, 5, 6, 7]::smallint[],
    ADD COLUMN duty_open_min integer NOT NULL DEFAULT 1080,
    ADD COLUMN duty_close_min integer NOT NULL DEFAULT 1320,
    ADD COLUMN accept_min integer NOT NULL DEFAULT 0,
    ADD COLUMN receivers text NOT NULL DEFAULT 'team',
    ADD COLUMN reopen_lost boolean NOT NULL DEFAULT true,
    ADD CONSTRAINT rules_contact_proof_check CHECK (cardinality(contact_proof) > 0
        AND contact_proof <@ ARRAY['any', 'call', 'connected', 'meeting', 'message', 'pbx']::text[]),
    ADD CONSTRAINT rules_min_talk_s_check CHECK (min_talk_s BETWEEN 0 AND 3600),
    ADD CONSTRAINT rules_remind_before_min_check CHECK (remind_before_min BETWEEN 0 AND 1440),
    ADD CONSTRAINT rules_grace_min_check CHECK (grace_min BETWEEN 0 AND 1440),
    ADD CONSTRAINT rules_off_hours_check CHECK (off_hours IN ('defer', 'duty') AND (off_hours = 'defer' OR duty_team_id IS NOT NULL)),
    ADD CONSTRAINT rules_duty_hours_check CHECK (duty_open_min BETWEEN 0 AND 1439 AND duty_close_min BETWEEN 1 AND 1440 AND duty_open_min < duty_close_min
        AND cardinality(duty_days) > 0),
    ADD CONSTRAINT rules_accept_min_check CHECK (accept_min BETWEEN 0 AND 240),
    ADD CONSTRAINT rules_receivers_check CHECK (receivers IN ('team', 'members'));
COMMENT ON COLUMN leadflow.rules.contact_proof IS 'Thế nào là ĐÃ LIÊN HỆ (0152): any · call · connected · meeting · message · pbx (cuộc gọi tổng đài nghe máy ≥ min_talk_s)';

-- ── Trạng thái lead: bước nhận, nhắc, ân hạn, trực ngoài giờ, người lọc lead, lượt quay lại ──
ALTER TABLE leadflow.lead_state DROP CONSTRAINT lead_state_status_check;
ALTER TABLE leadflow.lead_state ADD CONSTRAINT lead_state_status_check
    CHECK (status = ANY (ARRAY['offered', 'waiting', 'queued', 'contacted', 'closed']));
ALTER TABLE leadflow.lead_state
    ADD COLUMN accept_due timestamptz,
    ADD COLUMN accepted_at timestamptz,
    ADD COLUMN remind_at timestamptz,
    ADD COLUMN reminded_at timestamptz,
    ADD COLUMN breached_at timestamptz,
    ADD COLUMN recall_at timestamptz,
    ADD COLUMN followup_reminded timestamptz,
    ADD COLUMN duty boolean NOT NULL DEFAULT false,
    ADD COLUMN qualified_by uuid REFERENCES core.users(id),
    ADD COLUMN returns integer NOT NULL DEFAULT 0,
    ADD COLUMN returned_at timestamptz;
DROP INDEX leadflow.idx_lead_state_open;
CREATE INDEX idx_lead_state_open ON leadflow.lead_state USING btree (tenant_id, status, first_call_due)
    WHERE (status = ANY (ARRAY['offered', 'waiting', 'queued', 'contacted']));
CREATE INDEX idx_lead_state_holder ON leadflow.lead_state USING btree (tenant_id, holder_id) WHERE holder_id IS NOT NULL;

ALTER TABLE leadflow.sla_events DROP CONSTRAINT sla_events_kind_check;
ALTER TABLE leadflow.sla_events ADD CONSTRAINT sla_events_kind_check CHECK (kind = ANY (ARRAY[
    'assigned', 'queued', 'first_call_met', 'first_call_breached', 'recalled', 'followup_breached',
    'reminded', 'escalated', 'accepted', 'accept_missed', 'returned', 'reopened', 'handed_over']));
CREATE INDEX idx_sla_events_user ON leadflow.sla_events USING btree (tenant_id, user_id, at DESC) WHERE user_id IS NOT NULL;

-- ── Thành viên [[offering|l]] (ai bán dự án / khoá học / gói nào): hạng (1 nhận trước) + trọng số trong hạng — dữ liệu quản lý thấy và sửa ──
CREATE TABLE leadflow.members (
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    offering_code text NOT NULL CHECK (btrim(offering_code) <> ''),
    user_id uuid NOT NULL REFERENCES core.users(id),
    tier smallint NOT NULL DEFAULT 1 CHECK (tier BETWEEN 1 AND 9),
    weight smallint NOT NULL DEFAULT 1 CHECK (weight BETWEEN 1 AND 100),
    active boolean NOT NULL DEFAULT true,
    note text NOT NULL DEFAULT '',
    updated_by uuid REFERENCES core.users(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, offering_code, user_id)
);
CREATE INDEX idx_members_user ON leadflow.members (tenant_id, user_id) WHERE active;
CALL core.secure_tenant('leadflow.members');
GRANT SELECT, INSERT, UPDATE ON leadflow.members TO barista_app;
COMMENT ON TABLE leadflow.members IS 'Ai nhận lead / bán thứ gì (offering — dự án, khoá học…) (0152): hạng + trọng số — luật chia "theo thành viên" và công tắc chỉ thành viên đọc bảng này';

-- ── Công tắc "chỉ thành viên" (leadflow.members_only, mặc định tắt): người NHẬN lead (phạm vi chia lead = của mình) chỉ thấy / giữ lead của
-- [[offering|l]] mình là thành viên. Hàm tính một lần mỗi câu (gọi dạng (SELECT …) — 0145); quyền hệ thống / quản lý (phạm vi đội, cả công ty) không bị chặn.
CREATE FUNCTION leadflow.members_only() RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT core.scope('leadflow') = 'own' AND core.setting_on('leadflow.members_only', false) $$;
CREATE FUNCTION leadflow.my_offerings() RETURNS text[]
    LANGUAGE sql STABLE
    AS $$ SELECT coalesce(array_agg(m.offering_code), '{}'::text[]) FROM leadflow.members m
          WHERE m.tenant_id = core.tenant() AND m.user_id = core.me() AND m.active $$;
COMMENT ON FUNCTION leadflow.members_only() IS 'Hợp đồng DB của leadflow (0152): người xem đang bị giới hạn theo thành viên offering không — gói khác dùng trong chính sách hạn chế';
COMMENT ON FUNCTION leadflow.my_offerings() IS 'Hợp đồng DB của leadflow (0152): mã offering người xem là thành viên';

CREATE POLICY p_members_only ON crm.leads AS RESTRICTIVE
    USING (NOT (SELECT leadflow.members_only()) OR offering_code = '' OR offering_code = ANY ((SELECT leadflow.my_offerings())::text[]))
    WITH CHECK (NOT (SELECT leadflow.members_only()) OR offering_code = '' OR offering_code = ANY ((SELECT leadflow.my_offerings())::text[]));
