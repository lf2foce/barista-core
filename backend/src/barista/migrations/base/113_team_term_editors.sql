-- module: core — ADR 0256 (rà 24/11/2026): một dòng ``core.team_terms`` mang HAI căn cứ — "là thành viên của đội này (đội chính hay không)" và "là trưởng đội" —
-- do hai người khác nhau ghi được (A xếp người vào đội, B phong trưởng). Bản 112 chỉ giữ MỘT người sửa mỗi dòng ⇒ lần sửa sau che người sửa trước, người đó lọt
-- khỏi danh sách "người sửa cơ cấu không tự xác nhận phần chia" mà bên đọc cơ cấu tại một ngày trả về. Bản này ghi người sửa THEO TỪNG CĂN CỨ:
--   home_by / home_at — người ghi lần cuối căn cứ thành viên (vào đội, đổi đội chính ↔ đội tham gia);
--   lead_by / lead_at — người ghi lần cuối căn cứ trưởng đội (chỉ có khi dòng là trưởng);
-- đổi một căn cứ thì căn cứ kia được MANG SANG dòng nối tiếp nguyên người sửa cũ. ``edited_by`` vẫn là lần chạm cuối của dòng (kể cả sửa lùi ngày, đóng dòng).
-- Chỉ thêm cột + thay thân hàm trigger ⇒ không cần bảo trì riêng (112 đã cần).
SET LOCAL lock_timeout = '10s';
ALTER TABLE core.team_terms ADD COLUMN home_by UUID, ADD COLUMN home_at TIMESTAMPTZ, ADD COLUMN lead_by UUID, ADD COLUMN lead_at TIMESTAMPTZ;
-- dòng đã có (112 vừa tạo, hoặc DB dev đã chạy 112): căn cứ nào cũng do người sửa duy nhất đang ghi
UPDATE core.team_terms SET home_by = edited_by, home_at = edited_at;
UPDATE core.team_terms SET lead_by = edited_by, lead_at = edited_at WHERE is_lead;
ALTER TABLE core.team_terms ALTER COLUMN home_at SET NOT NULL, ALTER COLUMN home_at SET DEFAULT now(),
    ADD CONSTRAINT team_terms_lead_mark CHECK (is_lead OR (lead_by IS NULL AND lead_at IS NULL));

CREATE OR REPLACE FUNCTION core.team_terms_sync() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, pg_catalog AS $$
DECLARE
    d DATE;
    was core.team_terms%ROWTYPE;      -- dòng đang mở trước thay đổi này (căn cứ không đổi lấy người sửa từ đây)
    had BOOLEAN := false;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.user_id, NEW.team_id, NEW.tenant_id) IS DISTINCT FROM (OLD.user_id, OLD.team_id, OLD.tenant_id) THEN
        RAISE EXCEPTION 'Không đổi người / đội của một dòng thành viên: xoá dòng cũ, thêm dòng mới';
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        d := core.today_of(OLD.tenant_id);
        SELECT * INTO was FROM core.team_terms
        WHERE tenant_id = OLD.tenant_id AND user_id = OLD.user_id AND team_id = OLD.team_id AND valid_to IS NULL FOR UPDATE;
        had := FOUND;
        IF TG_OP = 'UPDATE' AND NEW.is_lead = OLD.is_lead AND NEW.is_home = OLD.is_home THEN
            RETURN NULL;
        END IF;
        IF was.valid_from >= d THEN      -- mở trong hôm nay: chưa thành lịch sử ⇒ bỏ, dòng mới (nếu có) thay chỗ
            DELETE FROM core.team_terms WHERE id = was.id;
        ELSE
            UPDATE core.team_terms SET valid_to = d, edited_by = core.me(), edited_at = now() WHERE id = was.id;
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        d := core.today_of(NEW.tenant_id);
        had := had AND TG_OP = 'UPDATE';
        INSERT INTO core.team_terms (tenant_id, user_id, team_id, is_lead, is_home, valid_from, head_role, edited_by, home_by, home_at, lead_by, lead_at)
        VALUES (NEW.tenant_id, NEW.user_id, NEW.team_id, NEW.is_lead, NEW.is_home, d,
                CASE WHEN NEW.is_lead THEN (SELECT mb.role FROM core.memberships mb WHERE mb.tenant_id = NEW.tenant_id AND mb.user_id = NEW.user_id AND mb.active) END,
                core.me(),
                CASE WHEN had AND NEW.is_home = OLD.is_home THEN was.home_by ELSE core.me() END,
                CASE WHEN had AND NEW.is_home = OLD.is_home THEN was.home_at ELSE now() END,
                CASE WHEN NOT NEW.is_lead THEN NULL WHEN had AND OLD.is_lead THEN was.lead_by ELSE core.me() END,
                CASE WHEN NOT NEW.is_lead THEN NULL WHEN had AND OLD.is_lead THEN was.lead_at ELSE now() END);
    END IF;
    RETURN NULL;
END $$;
