-- module: flow
-- 119 (ADR 0265 mục 1, 4): LUẬT TỰ ĐỘNG chạy THEO LỊCH + hai hành động mới — chỉ THÊM cột / nới ràng buộc (bản cũ còn chạy lúc chuyển vẫn đọc / ghi
-- luật theo sự kiện và theo ngày như trước: dòng của nó có ``schedule`` trống, thoả ràng buộc mới).
--  * Vế KHI thứ ba ``schedule``: lịch lặp kiểu RFC 5545 (``FREQ=DAILY|WEEKLY|MONTHLY`` …) theo múi giờ CÔNG TY, giờ đồng hồ ``run_at``. ``next_run_at``
--    là lượt kế tiếp (mốc tuyệt đối) — vòng quét mỗi phút chọn luật tới hạn, đẩy ``next_run_at`` sang lượt sau trong CÙNG giao dịch giữ khoá dòng.
--  * Hành động mới: ``draft`` (nhờ trợ lý soạn — trợ lý không người, bộ công cụ hẹp ``tools`` khai cho luật; kết quả là thẻ đề xuất / bản nháp tới
--    người nhận, không bao giờ tự chạy lệnh ghi) và ``digest`` (gửi bản tóm tắt phát hiện của bộ kiểm cho người nhận).
--  * Lần chạy theo lịch chống lặp theo (luật, lượt) — ``slot_at``. Lần chạy chờ trợ lý soạn mang ``job`` (việc đã chốt lúc luật khớp) và ``lease_until``
--    (lượt soạn đang giữ; tiến trình chết ⇒ hết thuê, lượt sau làm lại — thẻ và thông báo có khoá chống lặp).
SET LOCAL lock_timeout = '10s';

ALTER TABLE flow.automations
    ADD COLUMN schedule TEXT CHECK (schedule ~ '^FREQ=(DAILY|WEEKLY|MONTHLY)(;[A-Z]+=[A-Z0-9,-]+)*$'),
    ADD COLUMN run_at TIME,
    ADD COLUMN next_run_at TIMESTAMPTZ,
    ADD COLUMN tools JSONB NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(tools) = 'array');
ALTER TABLE flow.automations DROP CONSTRAINT automations_when_check;
ALTER TABLE flow.automations ADD CONSTRAINT automations_when_check CHECK (num_nonnulls(trigger, date_source, schedule) = 1);
ALTER TABLE flow.automations ADD CONSTRAINT automations_schedule_pair_check CHECK ((schedule IS NULL) = (run_at IS NULL) AND (schedule IS NOT NULL OR next_run_at IS NULL));
ALTER TABLE flow.automations DROP CONSTRAINT automations_action_check;
ALTER TABLE flow.automations ADD CONSTRAINT automations_action_check CHECK (action IN ('notify', 'task', 'command', 'propose', 'draft', 'digest'));
CREATE INDEX idx_automations_scheduled ON flow.automations (next_run_at) WHERE active AND schedule IS NOT NULL;

ALTER TABLE flow.automation_runs
    ADD COLUMN slot_at TIMESTAMPTZ,
    ADD COLUMN job JSONB CHECK (job IS NULL OR jsonb_typeof(job) = 'object'),
    ADD COLUMN lease_until TIMESTAMPTZ;
ALTER TABLE flow.automation_runs DROP CONSTRAINT automation_runs_what_check;
ALTER TABLE flow.automation_runs ADD CONSTRAINT automation_runs_what_check
    CHECK ((event_id IS NOT NULL AND ref IS NULL AND on_date IS NULL AND slot_at IS NULL)
        OR (event_id IS NULL AND ref IS NOT NULL AND on_date IS NOT NULL AND slot_at IS NULL)
        OR (event_id IS NULL AND ref IS NULL AND on_date IS NULL AND slot_at IS NOT NULL));
CREATE UNIQUE INDEX automation_runs_slot ON flow.automation_runs (automation_id, slot_at) WHERE slot_at IS NOT NULL;
CREATE INDEX idx_automation_runs_drafts ON flow.automation_runs (tenant_id) WHERE status = 'queued' AND job IS NOT NULL;
