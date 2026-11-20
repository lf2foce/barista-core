-- module: hr
-- 0160 (review 11/11/2026): AI SỬA cơ cấu thì không tự xác nhận hoa hồng dựng từ cơ cấu đó. Mỗi dòng phân công / trưởng đội ghi NGƯỜI sửa lần cuối + lúc nào
-- (ghi dòng mới HOẶC đóng dòng cũ), lý do khi lùi ngày quá hạn mức, và vai công ty của trưởng LÚC BỔ NHIỆM (memberships không có lịch sử vai ⇒ chụp lại
-- theo từng nhiệm kỳ trưởng đội, không đọc vai hôm nay cho ngày ký đã qua). Chỉ thêm cột có thể trống / mặc định ⇒ bản cũ chạy song song, không bảo trì.
-- Dòng có trước bản này: edited_by trống ⇒ người đọc dùng created_by.
SET LOCAL lock_timeout = '10s';
ALTER TABLE hr.assignments ADD COLUMN edited_by UUID, ADD COLUMN edited_at TIMESTAMPTZ NOT NULL DEFAULT now(), ADD COLUMN backdate_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE hr.team_heads ADD COLUMN edited_by UUID, ADD COLUMN edited_at TIMESTAMPTZ NOT NULL DEFAULT now(), ADD COLUMN backdate_reason TEXT NOT NULL DEFAULT '',
                          ADD COLUMN head_role TEXT;
GRANT UPDATE (valid_to, edited_by, edited_at) ON hr.assignments, hr.team_heads TO barista_app;

-- Đóng dòng được phép ghi lại người / lúc đóng; mọi cột khác vẫn không sửa, không xoá.
CREATE FUNCTION hr.append_close_edit() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP = 'DELETE' OR OLD.valid_to IS NOT NULL
    OR (to_jsonb(OLD) - 'valid_to' - 'edited_by' - 'edited_at') <> (to_jsonb(NEW) - 'valid_to' - 'edited_by' - 'edited_at')
 THEN RAISE EXCEPTION 'Lịch sử làm việc chỉ thêm và đóng, không sửa'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER assignment_append_only ON hr.assignments;
CREATE TRIGGER assignment_append_only BEFORE UPDATE OR DELETE ON hr.assignments FOR EACH ROW EXECUTE FUNCTION hr.append_close_edit();
DROP TRIGGER team_head_append_only ON hr.team_heads;
CREATE TRIGGER team_head_append_only BEFORE UPDATE OR DELETE ON hr.team_heads FOR EACH ROW EXECUTE FUNCTION hr.append_close_edit();
