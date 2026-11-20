-- module: hr
-- 0107: thin work record referencing parties. No salary, tax, insurance, contract or obligation tables here. Effective-dated so history starts at cutover.
CREATE SCHEMA hr;
GRANT USAGE ON SCHEMA hr TO barista_app;
CREATE TABLE hr.employments (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL DEFAULT core.tenant(),
 party_id UUID NOT NULL, entity_id UUID,
 employee_code TEXT NOT NULL CHECK(btrim(employee_code)<>''), title TEXT NOT NULL DEFAULT '',
 started_on DATE NOT NULL, ended_on DATE, created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK(ended_on IS NULL OR ended_on>=started_on), UNIQUE(tenant_id,id)
);
CREATE INDEX employments_party ON hr.employments(tenant_id,party_id,started_on);
CREATE INDEX employments_code ON hr.employments(tenant_id,employee_code);
CREATE TABLE hr.assignments (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL DEFAULT core.tenant(),
 party_id UUID NOT NULL, team_id UUID, manager_party_id UUID,
 valid_from DATE NOT NULL, valid_to DATE, created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK(team_id IS NOT NULL OR manager_party_id IS NOT NULL), CHECK(manager_party_id IS DISTINCT FROM party_id),
 CHECK(valid_to IS NULL OR valid_to>=valid_from), UNIQUE(tenant_id,id)
);
CREATE INDEX assignments_party ON hr.assignments(tenant_id,party_id,valid_from);
CALL core.secure_table('hr.employments','hr');
CALL core.secure_table('hr.assignments','hr');
GRANT SELECT,INSERT ON hr.employments,hr.assignments TO barista_app;
GRANT UPDATE(ended_on) ON hr.employments TO barista_app;
GRANT UPDATE(valid_to) ON hr.assignments TO barista_app;
-- History is only appended and closed: never rewritten, even if a handler forgets.
CREATE FUNCTION hr.append_only() RETURNS TRIGGER LANGUAGE plpgsql AS $$ DECLARE closing TEXT := TG_ARGV[0]; BEGIN
 IF TG_OP='DELETE' OR (to_jsonb(OLD)->>closing) IS NOT NULL OR (to_jsonb(OLD)-closing)<>(to_jsonb(NEW)-closing)
 THEN RAISE EXCEPTION 'Lịch sử làm việc chỉ thêm và đóng, không sửa'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER employment_append_only BEFORE UPDATE OR DELETE ON hr.employments FOR EACH ROW EXECUTE FUNCTION hr.append_only('ended_on');
CREATE TRIGGER assignment_append_only BEFORE UPDATE OR DELETE ON hr.assignments FOR EACH ROW EXECUTE FUNCTION hr.append_only('valid_to');
-- One person one period per legal entity / one assignment at a time; one code belongs to one person inside a legal entity.
CREATE FUNCTION hr.check_employment() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM hr.employments e WHERE e.tenant_id=NEW.tenant_id AND e.id<>NEW.id AND e.party_id=NEW.party_id
           AND e.entity_id IS NOT DISTINCT FROM NEW.entity_id
           AND daterange(e.started_on,e.ended_on,'[]') && daterange(NEW.started_on,NEW.ended_on,'[]'))
 THEN RAISE EXCEPTION 'Trùng thời gian làm việc của cùng một người trong cùng pháp nhân'; END IF;
 IF EXISTS(SELECT 1 FROM hr.employments e WHERE e.tenant_id=NEW.tenant_id AND e.party_id<>NEW.party_id
           AND e.employee_code=NEW.employee_code AND e.entity_id IS NOT DISTINCT FROM NEW.entity_id)
 THEN RAISE EXCEPTION 'Mã nhân viên đã thuộc người khác trong pháp nhân này'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER employment_valid BEFORE INSERT OR UPDATE ON hr.employments FOR EACH ROW EXECUTE FUNCTION hr.check_employment();
CREATE FUNCTION hr.check_assignment() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM hr.assignments a WHERE a.tenant_id=NEW.tenant_id AND a.id<>NEW.id AND a.party_id=NEW.party_id
           AND daterange(a.valid_from,a.valid_to,'[]') && daterange(NEW.valid_from,NEW.valid_to,'[]'))
 THEN RAISE EXCEPTION 'Trùng thời gian phân công của cùng một người'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER assignment_valid BEFORE INSERT OR UPDATE ON hr.assignments FOR EACH ROW EXECUTE FUNCTION hr.check_assignment();
