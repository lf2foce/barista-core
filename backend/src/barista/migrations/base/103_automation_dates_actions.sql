-- module: flow
-- 103 (ADR 0246): LUẬT TỰ ĐỘNG mở rộng — chỉ THÊM cột / nới ràng buộc (bản cũ còn chạy lúc chuyển vẫn đọc / ghi luật theo sự kiện như trước).
--  * Luật THEO NGÀY: thay vì cò sự kiện (``trigger``), luật bám một NGUỒN NGÀY module khai (``date_source`` — vd hạn việc, hẹn chăm sóc lại, hạn
--    khoản phải thu) + độ lệch ``offset_days`` (âm = trước ngày, dương = sau ngày). Vòng quét của flow xét luật mỗi giờ (``swept_at``).
--  * Hành động mới: ``command`` (chạy MỘT lệnh module khai an toàn cho luật — ``Module.command(auto=True)`` — bằng quyền người đặt luật) và
--    ``propose`` (thẻ đề xuất cho một người — người bấm mới chạy). ``params``: giá trị cố định cho ô của lệnh (đã kiểm theo ô lệnh khai, không mã).
--  * Đích mới ``to_owner``: người phụ trách CHÍNH bản ghi (sale giữ lead, người làm việc…). Hành động ``command`` không có đích (chạy bằng quyền
--    người đặt luật).
--  * Lần chạy theo ngày chống lặp theo (luật, bản ghi, ngày) — đổi ngày của bản ghi ⇒ luật kích lại một lần cho ngày mới. Hàng ``queued`` = đã
--    vào hàng đợi outbox (thử lại / hỏng như mọi sự kiện), chưa chạy xong.

ALTER TABLE flow.automations ALTER COLUMN trigger DROP NOT NULL;
ALTER TABLE flow.automations
    ADD COLUMN date_source TEXT CHECK (date_source ~ '^[a-z_]+\.[a-z_]+$'),
    ADD COLUMN offset_days INT NOT NULL DEFAULT 0 CHECK (offset_days BETWEEN -60 AND 365),
    ADD COLUMN command TEXT CHECK (command ~ '^[a-z_]+\.[a-z_]+$'),
    ADD COLUMN params JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(params) = 'object'),
    ADD COLUMN to_owner BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN swept_at TIMESTAMPTZ;
ALTER TABLE flow.automations ADD CONSTRAINT automations_when_check CHECK ((trigger IS NULL) <> (date_source IS NULL));
ALTER TABLE flow.automations DROP CONSTRAINT automations_action_check;
ALTER TABLE flow.automations ADD CONSTRAINT automations_action_check CHECK (action IN ('notify', 'task', 'command', 'propose'));
ALTER TABLE flow.automations ADD CONSTRAINT automations_action_command_check CHECK ((action IN ('command', 'propose')) = (command IS NOT NULL));
ALTER TABLE flow.automations DROP CONSTRAINT automations_check;
ALTER TABLE flow.automations ADD CONSTRAINT automations_target_check CHECK (
    CASE WHEN action = 'command' THEN team_id IS NULL AND user_id IS NULL AND NOT to_owner
         ELSE num_nonnulls(team_id, user_id) + to_owner::int = 1 END);
CREATE INDEX idx_automations_dated ON flow.automations (tenant_id) WHERE active AND date_source IS NOT NULL;

ALTER TABLE flow.automation_runs ALTER COLUMN event_id DROP NOT NULL;
ALTER TABLE flow.automation_runs ADD COLUMN ref TEXT CHECK (length(ref) BETWEEN 1 AND 100), ADD COLUMN on_date DATE;
ALTER TABLE flow.automation_runs ADD CONSTRAINT automation_runs_what_check
    CHECK ((event_id IS NOT NULL AND ref IS NULL AND on_date IS NULL) OR (event_id IS NULL AND ref IS NOT NULL AND on_date IS NOT NULL));
CREATE UNIQUE INDEX automation_runs_record_day ON flow.automation_runs (automation_id, ref, on_date) WHERE ref IS NOT NULL;
ALTER TABLE flow.automation_runs DROP CONSTRAINT automation_runs_status_check;
ALTER TABLE flow.automation_runs ADD CONSTRAINT automation_runs_status_check CHECK (status IN ('done', 'skipped', 'failed', 'trial', 'queued'));
GRANT UPDATE ON flow.automation_runs TO barista_app;
