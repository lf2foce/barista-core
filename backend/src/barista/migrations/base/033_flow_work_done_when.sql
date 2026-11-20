-- module: flow, core — lý do: đăng ký loại đối tượng mới vào core.subject_kinds (bảng tham chiếu của lõi, như bản gốc của flow)
-- 0117: kế hoạch và việc là ĐỐI TƯỢNG — trao đổi (talk), cuộc gọi (meet) gắn vào được, ai thấy kế hoạch / việc mới thấy (kiểm ở DB lúc truy cập,
-- hàm thấy-được SECURITY INVOKER ⇒ RLS của người hỏi áp: kế hoạch thấy qua phạm vi hoặc qua một việc của mình; việc thấy qua phạm vi hoặc hàng chờ đội).
CREATE FUNCTION flow.subject_visible_plan(id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $_$ SELECT EXISTS (SELECT 1 FROM flow.plans p WHERE p.id = $1) $_$;

CREATE FUNCTION flow.subject_visible_task(id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $_$ SELECT EXISTS (SELECT 1 FROM flow.tasks t WHERE t.id = $1) $_$;

INSERT INTO core.subject_kinds (kind, visible_fn, pack) VALUES ('flow.plan', 'flow.subject_visible_plan', 'base');
INSERT INTO core.subject_kinds (kind, visible_fn, pack) VALUES ('flow.task', 'flow.subject_visible_task', 'base');

-- 0117: việc trỏ tới một đối tượng nghiệp vụ của gói bất kỳ (khách, giao dịch, kỳ sổ…) kiểu ``source / source_ref`` — không khoá ngoại chéo module.
-- Quyền xem đối tượng vẫn của module chủ (hỏi bằng quyền người xem lúc mở); việc KHÔNG ẩn khi gói của đối tượng tắt (việc thuộc người / kế hoạch,
-- ẩn đi thì tiến độ kế hoạch nói sai).
ALTER TABLE flow.tasks ADD COLUMN subject_kind text, ADD COLUMN subject_id uuid,
    ADD CONSTRAINT tasks_subject_pair CHECK ((subject_kind IS NULL) = (subject_id IS NULL));
CREATE INDEX idx_flow_tasks_subject ON flow.tasks USING btree (tenant_id, subject_kind, subject_id) WHERE subject_id IS NOT NULL;

-- 0117: ``done_when`` — việc có BỘ KIỂM (cùng họ ``registry.CHECKERS`` của mục hồ sơ, đọc đối tượng của việc) chỉ xong khi bộ kiểm báo đạt.
--   done_by: ai / cái gì đóng việc — self (người làm tự báo), evidence (bộ kiểm đạt), override (ngoại lệ đã qua chuỗi duyệt). Có khi và chỉ khi xong.
--   check_*: kết quả lần kiểm gần nhất (chưa kiểm được = unknown — không phải "chưa đạt").
--   evidence_lost_at: lần gần nhất việc đã đóng bằng bằng chứng mà lần kiểm sau không còn đạt ⇒ việc TỰ MỞ LẠI; giữ mốc này để đếm số lần đóng sai.
ALTER TABLE flow.tasks
    ADD COLUMN done_check text,
    ADD COLUMN done_args jsonb DEFAULT '{}'::jsonb NOT NULL,
    ADD COLUMN done_by text,
    ADD COLUMN check_status text,
    ADD COLUMN check_value text DEFAULT ''::text NOT NULL,
    ADD COLUMN checked_at timestamp with time zone,
    ADD COLUMN evidence_lost_at timestamp with time zone,
    ADD COLUMN override_reason text DEFAULT ''::text NOT NULL,
    ADD COLUMN override_asked_by uuid,
    ADD COLUMN override_by uuid[] DEFAULT '{}'::uuid[] NOT NULL;
-- việc đã xong trước bản này đều là người tự báo (chưa có đường nào khác)
UPDATE flow.tasks SET done_by = 'self' WHERE status = 'done';
ALTER TABLE flow.tasks
    ADD CONSTRAINT tasks_done_by_check CHECK (done_by = ANY (ARRAY['self'::text, 'evidence'::text, 'override'::text])),
    ADD CONSTRAINT tasks_done_by_when CHECK ((status = 'done') = (done_by IS NOT NULL)),
    ADD CONSTRAINT tasks_check_status_check CHECK (check_status = ANY (ARRAY['missing'::text, 'received'::text, 'verified'::text, 'unknown'::text])),
    ADD CONSTRAINT tasks_check_needs_subject CHECK (done_check IS NULL OR subject_id IS NOT NULL);
CREATE INDEX idx_flow_tasks_checked ON flow.tasks USING btree (tenant_id, checked_at) WHERE done_check IS NOT NULL;

-- Chốt ở DB, mọi đường ghi (lệnh, kéo thẻ, tự động hoá, bản cũ còn chạy lúc chuyển, câu SQL viết sai trong handler):
--   * bản cũ không biết done_by ⇒ đánh dấu xong = "tự báo", mở lại ⇒ xoá;
--   * điều kiện xong (bộ kiểm, tham số, đối tượng) không đổi sau khi giao;
--   * kết quả kiểm (check_*), mốc mất căn cứ và dữ liệu ngoại lệ (override_*) CHỈ quyền hệ thống ghi (bộ kiểm, lệnh sau duyệt) — người không tự
--     ghi "đạt" rồi đóng trong cùng một câu;
--   * huỷ việc có bộ kiểm: chỉ quyền hệ thống (sau phiếu duyệt, huỷ cả kế hoạch, đối tượng bị huỷ);
--   * chuyển sang "xong": việc có bộ kiểm không tự báo được; "evidence" cần quyền hệ thống + lần kiểm gần nhất "verified" + có bộ kiểm; "override"
--     cần quyền hệ thống + có bộ kiểm + người xin + ít nhất một người duyệt KHÁC người xin; giá trị khác bị từ chối.
CREATE FUNCTION flow.task_done_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    sys boolean := core.is_system();
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.done_check IS NOT NULL AND (NEW.done_check IS DISTINCT FROM OLD.done_check OR NEW.done_args <> OLD.done_args
            OR NEW.subject_kind IS DISTINCT FROM OLD.subject_kind OR NEW.subject_id IS DISTINCT FROM OLD.subject_id) THEN
        RAISE EXCEPTION 'Điều kiện xong của việc không đổi được sau khi giao — huỷ việc và giao việc mới' USING ERRCODE = 'check_violation';
    END IF;
    IF NOT sys AND (
        (TG_OP = 'INSERT' AND (NEW.check_status IS NOT NULL OR NEW.check_value <> '' OR NEW.checked_at IS NOT NULL OR NEW.evidence_lost_at IS NOT NULL
                               OR NEW.override_reason <> '' OR NEW.override_asked_by IS NOT NULL OR cardinality(NEW.override_by) > 0))
        OR (TG_OP = 'UPDATE' AND (NEW.check_status IS DISTINCT FROM OLD.check_status OR NEW.check_value IS DISTINCT FROM OLD.check_value
                                  OR NEW.checked_at IS DISTINCT FROM OLD.checked_at OR NEW.evidence_lost_at IS DISTINCT FROM OLD.evidence_lost_at
                                  OR NEW.override_reason IS DISTINCT FROM OLD.override_reason OR NEW.override_asked_by IS DISTINCT FROM OLD.override_asked_by
                                  OR NEW.override_by IS DISTINCT FROM OLD.override_by))) THEN
        RAISE EXCEPTION 'Kết quả kiểm và dữ liệu ngoại lệ của việc chỉ hệ thống ghi' USING ERRCODE = 'check_violation';
    END IF;
    -- huỷ việc có bộ kiểm cũng là "không làm mà đóng": chỉ qua phiếu duyệt hoặc quyền hệ thống (huỷ cả kế hoạch, đối tượng bị huỷ)
    IF NEW.status = 'cancelled' AND NEW.done_check IS NOT NULL AND NOT sys AND (TG_OP = 'INSERT' OR OLD.status <> 'cancelled') THEN
        RAISE EXCEPTION 'Huỷ việc có bộ kiểm cần qua phiếu duyệt' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status <> 'done' THEN
        NEW.done_by := NULL;
    ELSIF NEW.done_by IS NULL THEN
        NEW.done_by := 'self';
    END IF;
    IF NEW.status = 'done' AND (TG_OP = 'INSERT' OR OLD.status <> 'done' OR NEW.done_by IS DISTINCT FROM OLD.done_by) THEN
        IF NEW.done_by = 'self' THEN
            IF NEW.done_check IS NOT NULL THEN
                RAISE EXCEPTION 'Việc này xong khi bộ kiểm % đạt — không tự đánh dấu xong được', NEW.done_check USING ERRCODE = 'check_violation';
            END IF;
        ELSIF NEW.done_by = 'evidence' THEN
            IF NOT sys OR NEW.done_check IS NULL OR NEW.check_status IS DISTINCT FROM 'verified' THEN
                RAISE EXCEPTION 'Đóng bằng bằng chứng chỉ khi bộ kiểm vừa báo đạt' USING ERRCODE = 'check_violation';
            END IF;
        ELSIF NEW.done_by = 'override' THEN
            IF NOT sys OR NEW.done_check IS NULL OR NEW.override_asked_by IS NULL OR cardinality(NEW.override_by) = 0
                    OR NEW.override_asked_by = ANY (NEW.override_by) THEN
                RAISE EXCEPTION 'Đóng ngoại lệ chỉ sau phiếu duyệt, người duyệt khác người xin' USING ERRCODE = 'check_violation';
            END IF;
        ELSE
            RAISE EXCEPTION 'Cách đóng việc không hợp lệ: %', NEW.done_by USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER task_done_guard BEFORE INSERT OR UPDATE ON flow.tasks FOR EACH ROW EXECUTE FUNCTION flow.task_done_guard();

-- Chuỗi duyệt "đóng việc không cần bằng chứng" của gói nền cho công ty đang chạy (phát hành chỉ chạy migration, như 020). Công ty sửa trong app.
INSERT INTO core.approval_chains (tenant_id, key, label, steps)
SELECT t.id, 'task_override', 'Đóng việc không cần bằng chứng', '[{"kind":"team_lead"}]'::jsonb FROM core.tenants t
WHERE NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = t.id AND i.kind = 'chain' AND i.key = 'task_override')
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs (tenant_id, kind, key, pack, version)
SELECT id, 'chain', 'task_override', 'base', NULL FROM core.tenants ON CONFLICT DO NOTHING;
