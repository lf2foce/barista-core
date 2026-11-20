-- module: hr
-- 0160: TRƯỞNG ĐỘI THEO NGÀY — ai đứng đầu một đội trong khoảng nào (khuyết = không có dòng phủ ngày đó). Cùng kiểu với phân công: chỉ thêm và đóng.
-- Hoa hồng đọc qua `hr.structure_on` để đề xuất người hưởng theo cơ cấu TẠI NGÀY KÝ (đổi trưởng về sau không đổi giao dịch cũ).
-- `core.team_members.is_lead` vẫn là quyền xem cây đội HÔM NAY (kernel/teams.py); bảng này là LỊCH SỬ chức vụ, không cấp quyền gì.
-- Bảng mới, rỗng; bản cũ không đọc ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
CREATE TABLE hr.team_heads (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL DEFAULT core.tenant(),
 team_id UUID NOT NULL, party_id UUID NOT NULL,
 valid_from DATE NOT NULL, valid_to DATE, created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK(valid_to IS NULL OR valid_to>=valid_from), UNIQUE(tenant_id,id)
);
CREATE INDEX team_heads_team ON hr.team_heads(tenant_id,team_id,valid_from);
CALL core.secure_table('hr.team_heads','hr');
GRANT SELECT,INSERT ON hr.team_heads TO barista_app;
GRANT UPDATE(valid_to) ON hr.team_heads TO barista_app;
CREATE TRIGGER team_head_append_only BEFORE UPDATE OR DELETE ON hr.team_heads FOR EACH ROW EXECUTE FUNCTION hr.append_only('valid_to');
-- Một đội một trưởng tại một thời điểm (một người đứng đầu hai đội cùng lúc thì được — hai dòng hai đội).
CREATE FUNCTION hr.check_team_head() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM hr.team_heads h WHERE h.tenant_id=NEW.tenant_id AND h.id<>NEW.id AND h.team_id=NEW.team_id
           AND daterange(h.valid_from,h.valid_to,'[]') && daterange(NEW.valid_from,NEW.valid_to,'[]'))
 THEN RAISE EXCEPTION 'Trùng thời gian trưởng đội của cùng một đội'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER team_head_valid BEFORE INSERT OR UPDATE ON hr.team_heads FOR EACH ROW EXECUTE FUNCTION hr.check_team_head();
