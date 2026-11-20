-- module: core
-- 102 (ADR 0246, api 1.69, 20/11/2026) — LỊCH SỬ THAY ĐỔI THEO TRƯỜNG: "ai đổi trường nào của đối tượng nào từ gì sang gì, lúc nào, qua lệnh nào, người /
-- trợ lý / luật tự động". Chỉ THÊM (không UPDATE / DELETE cho barista_app). Ghi bằng ``ctx.track`` trong CÙNG giao dịch với thay đổi (kernel/history.py).
-- Ai thấy dòng nào: ai THẤY ĐỐI TƯỢNG (``core.subject_visible`` ⇒ hàm của module sở hữu, như memory.items / meet.calls; vế InitPlan core.kinds_off() như
-- 058 — gói của đối tượng tắt ⇒ không thấy). Ba thủ tục secure_* không diễn đạt được "thấy theo đối tượng" nên chính sách viết ở đây, cùng khuôn 003 / 028.
-- GHI chỉ kiểm công ty: lệnh chuyển đối tượng cho người khác (giao lead đi) có thể làm chính người ghi mất quyền thấy ngay trong lệnh đó — dòng lịch sử
-- vẫn phải vào. Ghi gì do lõi quyết (chỉ trường module khai ``m.tracks``, chỉ module sở hữu đối tượng) — SQL module không chạm bảng này (test_meta).
-- Bảng mới, bản cũ không đọc ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

CREATE TABLE core.field_changes (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id    uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    at           timestamptz NOT NULL DEFAULT clock_timestamp(),
    subject_kind text NOT NULL CHECK (subject_kind ~ '^[a-z_]+\.[a-z_]+$'),
    subject_id   uuid NOT NULL,
    field        text NOT NULL CHECK (field ~ '^[a-z][a-z0-9_]*$'),
    old_value    jsonb,                                  -- dạng JSON của giá trị (mã người, ngày ISO, số đồng…); hiện ra theo khai của trường
    new_value    jsonb,
    user_id      uuid REFERENCES core.users(id),         -- người (hoặc người mà trợ lý / luật tự động làm thay); NULL = quyền hệ thống
    via          text NOT NULL DEFAULT 'human' CHECK (via IN ('human', 'agent', 'link', 'app')),
    system       boolean NOT NULL DEFAULT false,
    command      text NOT NULL DEFAULT '',               -- lệnh đã đổi (gọi lồng: lệnh trong cùng)
    trace        text NOT NULL DEFAULT ''                -- vết nơi sinh ra (chat:… trợ lý, auto:… luật tự động, job:… việc nền) — như core.audit_log
);
CREATE INDEX idx_field_changes_subject ON core.field_changes (tenant_id, subject_kind, subject_id, at DESC);

ALTER TABLE core.field_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.field_changes FORCE ROW LEVEL SECURITY;
CREATE POLICY p_read ON core.field_changes FOR SELECT
    USING (tenant_id = (SELECT core.tenant()) AND (SELECT core.link_ref()) IS NULL
           AND coalesce(NOT (subject_kind = ANY ((SELECT core.kinds_off())::text[])), true) AND core.subject_visible(subject_kind, subject_id));
CREATE POLICY p_write ON core.field_changes FOR INSERT WITH CHECK (tenant_id = (SELECT core.tenant()));
COMMENT ON TABLE core.field_changes IS 'subject=core.subject_visible';
GRANT SELECT, INSERT ON core.field_changes TO barista_app;
