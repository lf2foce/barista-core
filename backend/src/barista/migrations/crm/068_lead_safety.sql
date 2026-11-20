-- module: leadflow, crm — lý do: 0153 — lead không có ngõ cụt, một nguồn "ai giữ lead", chặng là dữ liệu công ty.
-- CHỈ THÊM: cột có mặc định, bảng mới, ràng buộc khoá ngoại MATCH SIMPLE (cột mới trống ⇒ không kiểm), trigger chỉ chạm cột mới
-- ⇒ bản cũ chạy song song lúc chuyển vẫn đúng (nó không đọc / ghi cột mới), không cần cửa sổ bảo trì.

-- ── 1. Hàng chờ có TUỔI và có CHỦ: lead nằm hàng chờ quá N phút làm việc ⇒ báo người sở hữu hàng chờ, lặp lại tới khi có người nhận ──
ALTER TABLE leadflow.rules
    ADD COLUMN queue_escalate_min integer NOT NULL DEFAULT 240,
    ADD CONSTRAINT rules_queue_escalate_min_check CHECK (queue_escalate_min BETWEEN 1 AND 10080);
COMMENT ON COLUMN leadflow.rules.queue_escalate_min IS
    'Lead nằm hàng chờ (kể cả hết số lần thu hồi) quá ngần này phút làm việc ⇒ báo trưởng đội sở hữu hàng chờ, lặp lại mỗi chừng ấy phút (0153) — không có giá trị "tắt"';

ALTER TABLE leadflow.lead_state
    ADD COLUMN queued_at timestamptz,             -- vào hàng chờ lúc nào (tuổi hàng chờ; updated_at đổi theo mọi lần ghi nên không dùng được)
    ADD COLUMN queue_team_id uuid,                -- đội sở hữu hàng chờ (NULL = hàng chờ cả công ty — quản lý cả công ty sở hữu)
    ADD COLUMN queue_escalated_at timestamptz,
    ADD COLUMN queue_escalations integer NOT NULL DEFAULT 0,
    ADD COLUMN checked_at timestamptz,            -- vòng quét đã xét lead này lúc nào — quét xoay vòng, không lead nào bị bỏ quên vì đứng sau 500 lead cũ
    ADD COLUMN via_team_id uuid,                  -- người giữ được chọn VÌ thuộc đội này (luật theo đội / đội trực) — rời đội ⇒ chuyển lead
    ADD COLUMN via_members boolean NOT NULL DEFAULT false;   -- người giữ được chọn VÌ là thành viên [[offering|l]] — rời [[offering|l]] ⇒ chuyển lead
CREATE INDEX idx_lead_state_sweep ON leadflow.lead_state USING btree (tenant_id, checked_at NULLS FIRST)
    WHERE (status = ANY (ARRAY['offered', 'waiting', 'queued', 'contacted']));

-- ── 2. Chặng của lead là DỮ LIỆU công ty: mỗi chặng thuộc một giai đoạn cố định (lead / prospect / deal / lost — máy trạng thái code ép) ──
CREATE TABLE crm.stages (
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    key text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{1,39}$'),
    label text NOT NULL CHECK (btrim(label) <> ''),
    phase text NOT NULL CHECK (phase IN ('lead', 'prospect', 'deal', 'lost')),
    sort smallint NOT NULL DEFAULT 100,
    tone text NOT NULL DEFAULT '' CHECK (tone IN ('', 'blue', 'purple', 'green', 'gray', 'orange', 'red', 'teal')),
    active boolean NOT NULL DEFAULT true,
    updated_by uuid REFERENCES core.users(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key),
    UNIQUE (tenant_id, key, phase)
);
CALL core.secure_tenant('crm.stages');
GRANT SELECT, INSERT ON crm.stages TO barista_app;
GRANT UPDATE (label, sort, tone, active, updated_by, updated_at) ON crm.stages TO barista_app;   -- giai đoạn của chặng không đổi được, không xoá (chỉ tắt)
COMMENT ON TABLE crm.stages IS 'Chặng của lead do công ty đặt (0153): mỗi chặng thuộc MỘT giai đoạn; bộ mặc định đi kèm gói (Pack.records)';

ALTER TABLE crm.leads ADD COLUMN pipeline_stage text;
-- chặng phải thuộc ĐÚNG giai đoạn đang có của lead — ép ở DB (không chỉ ở code)
ALTER TABLE crm.leads ADD CONSTRAINT leads_pipeline_stage_fkey FOREIGN KEY (tenant_id, pipeline_stage, stage)
    REFERENCES crm.stages (tenant_id, key, phase);

-- Giai đoạn đổi bởi đường KHÁC (tạo giao dịch, giao dịch huỷ, khách quay lại…) mà không nêu chặng ⇒ chặng về chặng đầu của giai đoạn mới
-- (chặng cũ không còn đúng nghĩa). Lead mới không nêu chặng ⇒ chặng đầu của giai đoạn. Công ty chưa có chặng ⇒ để trống (hiện tên giai đoạn).
CREATE FUNCTION crm.pipeline_follows_phase() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF (TG_OP = 'INSERT' AND NEW.pipeline_stage IS NULL)
       OR (TG_OP = 'UPDATE' AND NEW.stage IS DISTINCT FROM OLD.stage AND NEW.pipeline_stage IS NOT DISTINCT FROM OLD.pipeline_stage) THEN
        NEW.pipeline_stage := (SELECT s.key FROM crm.stages s WHERE s.tenant_id = NEW.tenant_id AND s.phase = NEW.stage AND s.active
                               ORDER BY s.sort, s.key LIMIT 1);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER t_pipeline_follows_phase BEFORE INSERT OR UPDATE OF stage, pipeline_stage ON crm.leads
    FOR EACH ROW EXECUTE FUNCTION crm.pipeline_follows_phase();

ALTER TABLE crm.stage_events DROP CONSTRAINT stage_events_kind_check;
ALTER TABLE crm.stage_events ADD CONSTRAINT stage_events_kind_check CHECK (kind = ANY (ARRAY['created', 'stage', 'assigned', 'pipeline']));
