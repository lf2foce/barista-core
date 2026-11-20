-- module: leadflow
-- 0168 — LỊCH SỬ CHIA LEAD NẠP TỪ HỆ KHÁC: bảng RIÊNG (không trộn vào leadflow.sla_events — sổ đó là việc Barista tự làm theo luật của nó; mọi đọc
-- đang dùng nó để chấm điểm, xoay vòng, sức khoẻ). Mỗi dòng là một KHOẢNG GIỮ lead (ai giữ từ lúc nào tới lúc nào, vì sao bắt đầu / kết thúc) hoặc một
-- sự kiện lẻ không gắn được vào khoảng nào. Dấu nguồn ``source = 'import'`` + hệ nguồn + mã dòng nguồn (chống lặp khi nạp lại).
-- Trạng thái hiện tại của lead nạp vào leadflow.lead_state như thường, đánh dấu ``imported_at`` (NULL = Barista tự chia).
-- CHỈ THÊM: bảng mới, cột cho phép NULL ⇒ bản cũ chạy song song không đọc / ghi phần mới, không cần cửa sổ bảo trì.

SET LOCAL lock_timeout = '10s';

CREATE TABLE leadflow.history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    lead_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('held', 'recalled', 'assigned', 'handed_over', 'queued', 'first_call_met', 'first_call_breached',
                                       'followup_breached')),
    user_id uuid REFERENCES core.users(id),          -- người giữ (khoảng giữ) / người bị thu hồi / người được giao
    user_ref text NOT NULL DEFAULT '',               -- mã người ở hệ nguồn khi không khớp được thành viên (người đã rời, không email) — không mất dòng
    at timestamptz NOT NULL,
    until timestamptz,                               -- khoảng giữ: lúc thôi giữ; NULL = đang giữ lúc nạp
    started text NOT NULL DEFAULT '' CHECK (started IN ('', 'rule', 'designated', 'manual', 'retry')),
    ended text NOT NULL DEFAULT '' CHECK (ended IN ('', 'recalled', 'handed_over', 'ended')),
    source text NOT NULL DEFAULT 'import' CHECK (source = 'import'),
    origin text NOT NULL CHECK (origin ~ '^[a-z][a-z0-9_]{1,39}$'),   -- hệ nguồn (mã do người nạp đặt)
    ext_ref text NOT NULL CHECK (btrim(ext_ref) <> ''),              -- mã dòng ở hệ nguồn
    import_ref text NOT NULL DEFAULT '',             -- lô nhập ở hệ nguồn đã sinh ra lần giao này (nối sang core.import_runs.ext_ref)
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_by uuid REFERENCES core.users(id),
    imported_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, origin, ext_ref),
    CHECK (until IS NULL OR until >= at),
    CHECK (kind = 'held' OR (until IS NULL AND started = '' AND ended = ''))
);
CALL core.secure_child('leadflow.history', 'crm.leads', 'lead_id');
CREATE INDEX idx_history_lead ON leadflow.history (lead_id, at);
CREATE INDEX idx_history_user ON leadflow.history (tenant_id, user_id, at DESC) WHERE user_id IS NOT NULL;
GRANT SELECT, INSERT ON leadflow.history TO barista_app;
COMMENT ON COLUMN leadflow.history.kind IS 'held = một khoảng giữ lead (at → until); còn lại là sự kiện lẻ không gắn được vào khoảng giữ nào (0168)';

ALTER TABLE leadflow.lead_state ADD COLUMN imported_at timestamptz;
COMMENT ON COLUMN leadflow.lead_state.imported_at IS 'Trạng thái nạp từ hệ khác lúc này (0168) — NULL = Barista tự chia. Vòng quét xử lý như mọi lead';
