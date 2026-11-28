-- module: leadflow, crm — lý do: ADR 0264 — chia lead theo BẬC: bậc + hạn giữ là dữ liệu của luật, mời nhiều người (ai nhận trước giữ), sale trả lead,
-- công ty khai cặp chuyển chặng được phép.
--   leadflow.steps            bậc của một luật (seq 1..9): giữ tối đa N ngày (làm việc / lịch), đẩy cả lead đã liên hệ không, mời bao nhiêu người cùng lúc;
--   leadflow.members          + step (NULL = mọi bậc), + team_id (chi nhánh của thành viên);
--   leadflow.lead_state       + step_seq / step_since / hold_until (bậc hiện tại, vào bậc lúc nào, hết hạn giữ lúc nào — tính lúc nhận);
--   leadflow.invites          lời mời nhận lead (chưa phải người giữ) — người được mời THẤY lead qua chính sách đọc riêng trên crm.leads;
--   leadflow.rules            + release_free (trả lead không tính vào lượt đã nhận khi chia theo trọng số), + max_open (trần lead đang giữ chưa chốt / người);
--   leadflow.sla_events       + loại 'stepped', 'invited', 'claimed', 'released';
--   crm.stage_moves           cặp chặng được phép chuyển (+ vai). Công ty không khai dòng nào ⇒ như cũ.
-- CHỈ THÊM: cột có mặc định / cho phép NULL, bảng mới, CHECK nới thêm giá trị, chính sách đọc MỞ thêm cho người được mời (bản cũ không ghi lời mời nào
-- ⇒ không ai thấy thêm gì). Bản cũ chạy song song không đọc / ghi phần mới ⇒ không cần dừng dịch vụ.
SET LOCAL lock_timeout = '10s';

-- ── 1. Bậc của luật ──
CREATE TABLE leadflow.steps (
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    rule_id uuid NOT NULL REFERENCES leadflow.rules(id) ON DELETE CASCADE,
    seq smallint NOT NULL CHECK (seq BETWEEN 1 AND 9),
    label text NOT NULL CHECK (btrim(label) <> ''),
    after_days smallint NOT NULL DEFAULT 0 CHECK (after_days BETWEEN 0 AND 365),
    day_kind text NOT NULL DEFAULT 'working' CHECK (day_kind IN ('working', 'calendar')),
    include_contacted boolean NOT NULL DEFAULT true,
    invite_n smallint NOT NULL DEFAULT 1 CHECK (invite_n BETWEEN 1 AND 3),
    invite_min integer NOT NULL DEFAULT 30 CHECK (invite_min BETWEEN 1 AND 1440),
    updated_by uuid REFERENCES core.users(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, rule_id, seq)
);
CALL core.secure_tenant('leadflow.steps');
GRANT SELECT, INSERT, UPDATE, DELETE ON leadflow.steps TO barista_app;   -- sửa / bỏ bậc chỉ khi luật CHƯA chia lead nào (kiểm ở lệnh) — luật đã chạy giữ nguyên
COMMENT ON TABLE leadflow.steps IS
    'Bậc của luật chia (ADR 0264): lead ở bậc quá after_days (0 = không hạn) chưa chốt ⇒ sang bậc kế, không lùi. invite_n > 1 ⇒ mời nhiều người, ai nhận trước giữ';

ALTER TABLE leadflow.members
    ADD COLUMN step smallint CHECK (step BETWEEN 1 AND 9),
    ADD COLUMN team_id uuid REFERENCES core.teams(id) ON DELETE SET NULL;
COMMENT ON COLUMN leadflow.members.step IS 'Bậc thành viên nhận lead (ADR 0264) — NULL = mọi bậc. Chỉ áp cho luật có bậc chọn người nhận "Thành viên"';
COMMENT ON COLUMN leadflow.members.team_id IS 'Chi nhánh / đội của thành viên trong [[offering|l]] này (ADR 0264) — dữ liệu hiện trên danh sách thành viên';

ALTER TABLE leadflow.lead_state
    ADD COLUMN step_seq smallint,
    ADD COLUMN step_since timestamptz,
    ADD COLUMN hold_until timestamptz;
COMMENT ON COLUMN leadflow.lead_state.hold_until IS 'Hết hạn giữ của bậc hiện tại (ADR 0264) — vòng quét đẩy sang bậc kế. NULL = luật không có bậc / bậc không hạn';
CREATE INDEX idx_lead_state_hold ON leadflow.lead_state (tenant_id, hold_until) WHERE hold_until IS NOT NULL AND imported_at IS NULL;

ALTER TABLE leadflow.rules
    ADD COLUMN release_free boolean NOT NULL DEFAULT false,
    ADD COLUMN max_open integer NOT NULL DEFAULT 0 CHECK (max_open BETWEEN 0 AND 500);
COMMENT ON COLUMN leadflow.rules.max_open IS 'Trần lead ĐANG GIỮ chưa chốt mỗi người (ADR 0264) — 0 = không trần. Khác cap_uncalled (chỉ đếm lead chưa gọi)';

-- ── 2. Mời nhiều người — ai nhận trước giữ ──
CREATE TABLE leadflow.invites (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    lead_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES core.users(id),
    step_seq smallint NOT NULL,
    round smallint NOT NULL DEFAULT 1,
    at timestamptz NOT NULL DEFAULT now(),
    until timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'claimed', 'closed', 'expired')),
    closed_at timestamptz
);
CALL core.secure_tenant('leadflow.invites');
-- người phạm vi "của mình" chỉ thấy lời mời của chính mình; quản lý (đội / cả công ty) và quyền hệ thống thấy cả công ty
CREATE POLICY p_own_invites ON leadflow.invites AS RESTRICTIVE
    USING (user_id = (SELECT core.me()) OR (SELECT core.scope('leadflow')) IN ('team', 'all'))
    WITH CHECK (user_id = (SELECT core.me()) OR (SELECT core.scope('leadflow')) IN ('team', 'all'));
CREATE INDEX idx_invites_lead ON leadflow.invites (lead_id, status);
CREATE INDEX idx_invites_open ON leadflow.invites (tenant_id, user_id) WHERE status = 'open';
GRANT SELECT, INSERT ON leadflow.invites TO barista_app;
GRANT UPDATE (status, closed_at) ON leadflow.invites TO barista_app;
COMMENT ON TABLE leadflow.invites IS 'Lời mời nhận lead (ADR 0264): chưa phải người giữ; người đầu tiên bấm nhận thành người giữ, lời mời còn lại đóng';

-- Hợp đồng DB của leadflow: lead người xem đang được MỜI nhận (lời mời còn mở, chưa hết hạn). Chính sách gọi dạng (SELECT …) — một lần mỗi câu (0145).
CREATE FUNCTION leadflow.invited_leads() RETURNS uuid[]
    LANGUAGE sql STABLE
    AS $$ SELECT coalesce(array_agg(i.lead_id), '{}'::uuid[]) FROM leadflow.invites i
          WHERE i.tenant_id = core.tenant() AND i.user_id = core.me() AND i.status = 'open' AND i.until > now() $$;
COMMENT ON FUNCTION leadflow.invited_leads() IS 'Hợp đồng DB của leadflow (ADR 0264): mã lead người xem đang được mời nhận — chính sách đọc riêng của crm.leads';
CREATE POLICY p_invited_read ON crm.leads FOR SELECT
    USING (tenant_id = (SELECT core.tenant()) AND id = ANY ((SELECT leadflow.invited_leads())::uuid[]));

ALTER TABLE leadflow.sla_events DROP CONSTRAINT sla_events_kind_check;
ALTER TABLE leadflow.sla_events ADD CONSTRAINT sla_events_kind_check CHECK (kind = ANY (ARRAY[
    'assigned', 'queued', 'first_call_met', 'first_call_breached', 'recalled', 'followup_breached',
    'reminded', 'escalated', 'accepted', 'accept_missed', 'returned', 'reopened', 'handed_over',
    'stepped', 'invited', 'claimed', 'released']));

-- ── 3. Cặp chặng được phép chuyển ──
CREATE TABLE crm.stage_moves (
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    from_stage text NOT NULL CHECK (from_stage ~ '^[a-z][a-z0-9_]{1,39}$'),
    to_stage text NOT NULL CHECK (to_stage ~ '^[a-z][a-z0-9_]{1,39}$'),
    roles text[] NOT NULL DEFAULT '{}'::text[],
    active boolean NOT NULL DEFAULT true,
    updated_by uuid REFERENCES core.users(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, from_stage, to_stage),
    CHECK (from_stage <> to_stage)
);
CALL core.secure_tenant('crm.stage_moves');
GRANT SELECT, INSERT ON crm.stage_moves TO barista_app;
GRANT UPDATE (roles, active, updated_by, updated_at) ON crm.stage_moves TO barista_app;
COMMENT ON TABLE crm.stage_moves IS
    'Chuyển chặng được phép (ADR 0264): công ty khai ⇒ crm.set_stage chỉ cho cặp đã khai (roles rỗng = mọi vai). Không dòng nào đang dùng ⇒ như cũ';

-- khoá ngoại mới mang cả tenant_id (ADR 0258)
CALL core.tenant_fks(ARRAY['leadflow', 'crm']);
