-- module: hr
-- 0150 lát 3: ĐIỀU KIỆN TRẢ cho luật lương Việt Nam — lương đóng bảo hiểm, vùng lương tối thiểu nơi làm việc, đoàn viên công đoàn (thêm cột vào
-- `hr.terms`, vẫn chỉ thêm: đổi = dòng mới từ ngày mới) và NGƯỜI PHỤ THUỘC đăng ký giảm trừ gia cảnh (`hr.dependants`). Cả hai thuộc tài nguyên RIÊNG
-- `hr.terms` (người xem hồ sơ làm việc không mặc nhiên thấy). Gói Lương chỉ đọc qua `hr.terms_on` (đếm người phụ thuộc, không đọc tên).
-- Cột mới để trống được (dòng cũ không có) ⇒ không bảo trì, không quét bảng.
ALTER TABLE hr.terms
 ADD COLUMN insured_vnd BIGINT CHECK (insured_vnd IS NULL OR insured_vnd > 0),             -- lương làm căn cứ đóng bảo hiểm bắt buộc / tháng
 ADD COLUMN work_region TEXT CHECK (work_region IS NULL OR work_region IN ('I', 'II', 'III', 'IV')),   -- vùng lương tối thiểu nơi làm việc
 ADD COLUMN union_member BOOLEAN NOT NULL DEFAULT false;                                    -- đoàn viên (đoàn phí 1%)

-- NGƯỜI PHỤ THUỘC: chỉ thêm, trừ một việc — ghi ngày thôi đăng ký (một lần, không trước ngày bắt đầu). Tên người phụ thuộc là dữ liệu cá nhân của người
-- thứ ba: chỉ lưu tên + quan hệ (cần để đăng ký giảm trừ), không ngày sinh, không giấy tờ (mã định danh mã hoá: lát 2 — 0105 "còn mở").
CREATE TABLE hr.dependants (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 party_id UUID NOT NULL,
 user_id UUID REFERENCES core.users(id),          -- đăng nhập của người lao động (chính họ đọc dòng của mình)
 name TEXT NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 120),
 relation TEXT NOT NULL CHECK (relation IN ('child', 'spouse', 'parent', 'other')),
 registered_from DATE NOT NULL,
 registered_to DATE,
 note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 300),
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id),
 CHECK (registered_to IS NULL OR registered_to >= registered_from)
);
CREATE INDEX dependants_party ON hr.dependants (tenant_id, party_id, registered_from);
-- cùng một người phụ thuộc đăng ký hai lần cho cùng người lao động ⇒ giảm trừ hai lần: DB chặn khi dòng cũ còn hiệu lực
CREATE UNIQUE INDEX dependants_once ON hr.dependants (tenant_id, party_id, lower(btrim(name)), relation) WHERE registered_to IS NULL;
CALL core.secure_table('hr.dependants', 'hr.terms', 'user_id');
GRANT SELECT, INSERT ON hr.dependants TO barista_app;
GRANT UPDATE (registered_to) ON hr.dependants TO barista_app;
CREATE FUNCTION hr.dependants_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Người phụ thuộc chỉ thêm: thôi đăng ký = ghi ngày kết thúc'; END IF;
 IF OLD.registered_to IS NOT NULL OR (to_jsonb(NEW) - 'registered_to') <> (to_jsonb(OLD) - 'registered_to') THEN
  RAISE EXCEPTION 'Người phụ thuộc: chỉ ghi ngày thôi đăng ký một lần';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER dependants_guard BEFORE UPDATE OR DELETE ON hr.dependants FOR EACH ROW EXECUTE FUNCTION hr.dependants_guard();
