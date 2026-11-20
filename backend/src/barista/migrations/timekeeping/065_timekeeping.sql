-- module: timekeeping
-- 0150 lát 4 "Chấm công & nghỉ phép": giờ chấm là SỰ THẬT (chỉ thêm), bảng công là PHÉP TÍNH (không lưu ngày — hàm thuần), chỉ phần ĐÃ CHỐT mới thành
-- công trả lương (đẩy vào pay.record_work). Gói mới, schema riêng, bảng rỗng; gói tắt mặc định ⇒ không bảo trì.
-- Hàng rào ở DB: giờ chấm / xét giờ chấm / sổ phép chỉ thêm; người xét không xét giờ chấm của chính mình; người nhập không nhập giờ chấm cho chính
-- mình; một người một tháng chốt một lần; tháng đã chốt của một người ⇒ không thêm giờ chấm / phép / làm thêm vào tháng đó; người chốt không chốt bảng
-- công của chính mình; số liệu đã chốt không sửa — người lao động chỉ ghi xác nhận / phản hồi của CHÍNH mình, một lần.
CREATE SCHEMA timekeeping;
GRANT USAGE ON SCHEMA timekeeping TO barista_app;

CREATE FUNCTION timekeeping.append_only() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'Chấm công chỉ thêm: sửa = dòng mới (giờ bổ sung có duyệt, dòng xét, dòng điều chỉnh phép)' USING ERRCODE = 'restrict_violation';
END $$;


-- ĐỊA ĐIỂM + LUẬT CHẤM (dữ liệu): dải IP công khai của văn phòng (trình duyệt không đọc được tên wifi ⇒ "theo wifi" = theo IP công khai), tuỳ chọn
-- vùng toạ độ (tâm + bán kính). Trượt luật ⇒ lần chấm vẫn ghi, gắn cờ cho quản lý xét — không từ chối.
CREATE TABLE timekeeping.sites (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 code TEXT NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{0,29}$'),
 name TEXT NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 120),
 ip_ranges CIDR[] NOT NULL DEFAULT '{}',
 geo_lat NUMERIC(9, 6), geo_lng NUMERIC(9, 6), geo_radius_m INT CHECK (geo_radius_m IS NULL OR geo_radius_m BETWEEN 20 AND 5000),
 active BOOLEAN NOT NULL DEFAULT true,
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, code), UNIQUE (tenant_id, id),
 CHECK ((geo_lat IS NULL) = (geo_lng IS NULL) AND (geo_lat IS NULL) = (geo_radius_m IS NULL))
);
CALL core.secure_table('timekeeping.sites', 'timekeeping.policy');
GRANT SELECT, INSERT, UPDATE ON timekeeping.sites TO barista_app;
-- Kiểm IP của một lần chấm theo luật địa điểm, KHÔNG lộ dải IP cho người chấm (họ không có quyền đọc địa điểm): '' = trong mạng văn phòng.
CREATE FUNCTION timekeeping.site_check(p_site uuid, p_ip inet) RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 SELECT CASE WHEN s.id IS NULL OR NOT s.active THEN 'no_site' WHEN p_ip IS NULL THEN 'ip_unknown'
             WHEN EXISTS (SELECT 1 FROM unnest(s.ip_ranges) r WHERE p_ip <<= r) THEN '' ELSE 'ip_outside' END
 FROM (SELECT 1) one LEFT JOIN timekeeping.sites s ON s.tenant_id = core.tenant() AND s.id = p_site
$$;
REVOKE ALL ON FUNCTION timekeeping.site_check(uuid, inet) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION timekeeping.site_check(uuid, inet) TO barista_app;

-- GIỜ LÀM CỐ ĐỊNH của công ty (lịch làm việc): ngày trong tuần (ISO 1 = thứ Hai … 7 = Chủ nhật), giờ vào / ra, nghỉ giữa ca, phút đi muộn được tha.
CREATE TABLE timekeeping.patterns (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 code TEXT NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{0,29}$'),
 name TEXT NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 120),
 weekdays INT[] NOT NULL CHECK (cardinality(weekdays) BETWEEN 1 AND 7 AND weekdays <@ ARRAY[1, 2, 3, 4, 5, 6, 7]),
 starts TIME NOT NULL, ends TIME NOT NULL CHECK (ends > starts),
 break_minutes INT NOT NULL DEFAULT 60 CHECK (break_minutes BETWEEN 0 AND 240),
 grace_minutes INT NOT NULL DEFAULT 0 CHECK (grace_minutes BETWEEN 0 AND 120),
 active BOOLEAN NOT NULL DEFAULT true,
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, code), UNIQUE (tenant_id, id)
);
-- giờ làm: mọi thành viên đọc được (bảng công của mình cần) — ghi chỉ qua lệnh của người có quyền chính sách chấm công
CALL core.secure_tenant('timekeeping.patterns');
GRANT SELECT, INSERT, UPDATE ON timekeeping.patterns TO barista_app;

-- AI CHẤM THẾ NÀO (chỉ thêm, hiệu lực từ ngày): giờ làm nào, địa điểm nào, cách tính công — punch (theo giờ vào / ra) hoặc exception (đủ công theo lịch,
-- chỉ trừ ngoại lệ: nghỉ, vắng — văn phòng không bắt chấm vô ích).
CREATE TABLE timekeeping.enrolments (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 user_id UUID NOT NULL REFERENCES core.users(id), team_id UUID,
 pattern_id UUID NOT NULL, site_id UUID,
 mode TEXT NOT NULL CHECK (mode IN ('punch', 'exception', 'off')),
 valid_from DATE NOT NULL,
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, user_id, valid_from), UNIQUE (tenant_id, id),
 FOREIGN KEY (tenant_id, pattern_id) REFERENCES timekeeping.patterns (tenant_id, id),
 FOREIGN KEY (tenant_id, site_id) REFERENCES timekeeping.sites (tenant_id, id)
);
CALL core.secure_table('timekeeping.enrolments', 'timekeeping', 'user_id', 'team_id');
GRANT SELECT, INSERT ON timekeeping.enrolments TO barista_app;
CREATE TRIGGER enrolments_append_only BEFORE UPDATE OR DELETE ON timekeeping.enrolments FOR EACH ROW EXECUTE FUNCTION timekeeping.append_only();
-- cách tính công quyết ngày có lương ⇒ người gán không gán cho chính mình (như 053)
CREATE FUNCTION timekeeping.enrolments_self() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.user_id = NEW.created_by THEN
  RAISE EXCEPTION 'Không tự gán cách chấm công cho chính mình' USING ERRCODE = 'check_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER enrolments_self BEFORE INSERT ON timekeeping.enrolments FOR EACH ROW EXECUTE FUNCTION timekeeping.enrolments_self();

-- Mọi dòng theo ĐĂNG NHẬP của người lao động (``user_id``): chấm công cần tài khoản; gói Lương tự dịch đăng nhập sang định danh khi nhận công.
-- GIỜ CHẤM: chỉ thêm. Bằng chứng tối thiểu (0150 §8, BVDLCN): IP lúc chấm, kết quả kiểm luật — không theo dõi vị trí liên tục. ``team_id`` = đội chính
-- lúc chấm (trưởng đội thấy giờ của đội mình). Cờ khác rỗng ⇒ chờ quản lý xét.
CREATE TABLE timekeeping.punches (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 user_id UUID NOT NULL REFERENCES core.users(id), team_id UUID,
 at TIMESTAMPTZ NOT NULL,
 direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
 method TEXT NOT NULL CHECK (method IN ('web', 'import', 'correction')),
 site_id UUID, ip INET,
 flags TEXT[] NOT NULL DEFAULT '{}',
 note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 300),
 approved_by UUID[] NOT NULL DEFAULT '{}',      -- giờ bổ sung: người duyệt (chép từ phiếu duyệt)
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id),
 CHECK (method <> 'correction' OR cardinality(approved_by) > 0)
);
CREATE INDEX punches_user_at ON timekeeping.punches (tenant_id, user_id, at);
CREATE INDEX punches_flagged ON timekeeping.punches (tenant_id, at) WHERE flags <> '{}';
CALL core.secure_table('timekeeping.punches', 'timekeeping', 'user_id', 'team_id');
GRANT SELECT, INSERT ON timekeeping.punches TO barista_app;
CREATE TRIGGER punches_append_only BEFORE UPDATE OR DELETE ON timekeeping.punches FOR EACH ROW EXECUTE FUNCTION timekeeping.append_only();
CREATE FUNCTION timekeeping.punches_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF timekeeping.month_closed(NEW.tenant_id, NEW.user_id, (NEW.at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) THEN
  RAISE EXCEPTION 'Tháng này của người này đã chốt bảng công — điều chỉnh vào kỳ sau' USING ERRCODE = 'restrict_violation';
 END IF;
 IF NEW.method = 'import' AND NEW.user_id = NEW.created_by THEN
  RAISE EXCEPTION 'Không tự nhập giờ chấm cho chính mình' USING ERRCODE = 'check_violation';
 END IF;
 IF NEW.method = 'correction' AND NEW.user_id IS NOT NULL AND NEW.user_id = ANY (NEW.approved_by) THEN
  RAISE EXCEPTION 'Không tự duyệt giờ bổ sung của chính mình' USING ERRCODE = 'check_violation';
 END IF;
 IF NEW.method = 'web' AND NEW.user_id IS DISTINCT FROM NEW.created_by THEN
  RAISE EXCEPTION 'Chấm công trên app là của chính người chấm' USING ERRCODE = 'check_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER punches_guard BEFORE INSERT ON timekeeping.punches FOR EACH ROW EXECUTE FUNCTION timekeeping.punches_guard();

-- XÉT GIỜ CHẤM CÓ CỜ: nhận (tính công) hoặc bỏ (không tính). Một lần mỗi giờ chấm, chỉ thêm; người xét không phải chính người chấm.
CREATE TABLE timekeeping.punch_reviews (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 punch_id UUID NOT NULL,
 decision TEXT NOT NULL CHECK (decision IN ('accept', 'reject')),
 note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 300),
 reviewed_by UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, punch_id),
 FOREIGN KEY (tenant_id, punch_id) REFERENCES timekeeping.punches (tenant_id, id)
);
CALL core.secure_child('timekeeping.punch_reviews', 'timekeeping.punches', 'punch_id');
GRANT SELECT, INSERT ON timekeeping.punch_reviews TO barista_app;
CREATE TRIGGER punch_reviews_append_only BEFORE UPDATE OR DELETE ON timekeeping.punch_reviews FOR EACH ROW EXECUTE FUNCTION timekeeping.append_only();
CREATE FUNCTION timekeeping.punch_reviews_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r record;
BEGIN
 SELECT p.user_id, p.at INTO r FROM timekeeping.punches p WHERE p.tenant_id = NEW.tenant_id AND p.id = NEW.punch_id;
 IF r.user_id IS NOT NULL AND r.user_id = NEW.reviewed_by THEN
  RAISE EXCEPTION 'Không tự xét giờ chấm của chính mình — nhờ quản lý khác' USING ERRCODE = 'check_violation';
 END IF;
 IF timekeeping.month_closed(NEW.tenant_id, r.user_id, (r.at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date) THEN
  RAISE EXCEPTION 'Tháng này của người này đã chốt bảng công' USING ERRCODE = 'restrict_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER punch_reviews_guard BEFORE INSERT ON timekeeping.punch_reviews FOR EACH ROW EXECUTE FUNCTION timekeeping.punch_reviews_guard();

-- XIN BỔ SUNG GIỜ (quên chấm): qua chuỗi duyệt; duyệt ⇒ một giờ chấm method=correction mang tên người duyệt. Không sửa giờ chấm cũ.
CREATE TABLE timekeeping.corrections (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 user_id UUID NOT NULL REFERENCES core.users(id), team_id UUID,
 at TIMESTAMPTZ NOT NULL, direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
 reason TEXT NOT NULL CHECK (btrim(reason) <> '' AND length(reason) <= 300),
 status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'approved', 'rejected')),
 approval_id UUID, punch_id UUID,
 requested_by UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id)
);
CALL core.secure_table('timekeeping.corrections', 'timekeeping', 'user_id', 'team_id');
GRANT SELECT, INSERT ON timekeeping.corrections TO barista_app;
GRANT UPDATE (status, approval_id, punch_id) ON timekeeping.corrections TO barista_app;

-- LOẠI NGHỈ (dữ liệu của công ty): ai trả (công ty / quỹ bảo hiểm / không ai), có trừ phép năm không. Không lưu chẩn đoán bệnh — chỉ loại nghỉ.
CREATE TABLE timekeeping.leave_types (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 code TEXT NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{0,29}$'),
 name TEXT NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 120),
 paid_by TEXT NOT NULL CHECK (paid_by IN ('company', 'insurance', 'none')),
 uses_balance BOOLEAN NOT NULL DEFAULT false,
 active BOOLEAN NOT NULL DEFAULT true,
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, code)
);
-- loại nghỉ: mọi thành viên đọc được (xin nghỉ cần chọn loại) — ghi chỉ qua lệnh của người có quyền chính sách chấm công
CALL core.secure_tenant('timekeeping.leave_types');
GRANT SELECT, INSERT, UPDATE ON timekeeping.leave_types TO barista_app;

-- ĐƠN NGHỈ: qua chuỗi ``leave``; duyệt ⇒ dòng sổ phép (loại có số dư) + tính vào bảng công.
CREATE TABLE timekeeping.leave_requests (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 user_id UUID NOT NULL REFERENCES core.users(id), team_id UUID,
 leave_type TEXT NOT NULL,
 from_day DATE NOT NULL, to_day DATE NOT NULL CHECK (to_day >= from_day AND to_day - from_day <= 92),
 half_day BOOLEAN NOT NULL DEFAULT false CHECK (NOT half_day OR from_day = to_day),
 days NUMERIC(6, 1) NOT NULL CHECK (days > 0),
 reason TEXT NOT NULL DEFAULT '' CHECK (length(reason) <= 300),
 status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'approved', 'rejected', 'cancelled')),
 approval_id UUID, approved_by UUID[] NOT NULL DEFAULT '{}',
 requested_by UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id)
);
CREATE INDEX leave_requests_user ON timekeeping.leave_requests (tenant_id, user_id, from_day);
CALL core.secure_table('timekeeping.leave_requests', 'timekeeping', 'user_id', 'team_id');
GRANT SELECT, INSERT ON timekeeping.leave_requests TO barista_app;
GRANT UPDATE (status, approval_id, approved_by) ON timekeeping.leave_requests TO barista_app;

-- LÀM THÊM GIỜ: cần người lao động đồng ý (người đó tự xin) + quản lý duyệt; trần ngày / tháng / năm là quy định (vn-labour) — vượt ⇒ từ chối lúc xin.
CREATE TABLE timekeeping.overtime_requests (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 user_id UUID NOT NULL REFERENCES core.users(id), team_id UUID,
 day DATE NOT NULL,
 minutes INT NOT NULL CHECK (minutes BETWEEN 15 AND 720),
 reason TEXT NOT NULL CHECK (btrim(reason) <> '' AND length(reason) <= 300),
 status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'approved', 'rejected', 'cancelled')),
 approval_id UUID, approved_by UUID[] NOT NULL DEFAULT '{}',
 requested_by UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id)
);
CREATE INDEX overtime_user ON timekeeping.overtime_requests (tenant_id, user_id, day);
CALL core.secure_table('timekeeping.overtime_requests', 'timekeeping', 'user_id', 'team_id');
GRANT SELECT, INSERT ON timekeeping.overtime_requests TO barista_app;
GRANT UPDATE (status, approval_id, approved_by) ON timekeeping.overtime_requests TO barista_app;

-- Đơn đã DUYỆT (nghỉ, làm thêm) không thêm / duyệt vào tháng đã chốt của người đó; người duyệt không phải chính người xin.
CREATE FUNCTION timekeeping.requests_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE d0 date; d1 date;
BEGIN
 IF TG_TABLE_NAME = 'leave_requests' THEN d0 := NEW.from_day; d1 := NEW.to_day; ELSE d0 := NEW.day; d1 := NEW.day; END IF;
 IF NEW.status = 'approved' AND (TG_OP = 'INSERT' OR OLD.status <> 'approved') THEN
  IF NEW.user_id = ANY (NEW.approved_by) OR cardinality(NEW.approved_by) = 0 THEN
   RAISE EXCEPTION 'Đơn cần người khác duyệt' USING ERRCODE = 'check_violation';
  END IF;
  IF timekeeping.month_closed(NEW.tenant_id, NEW.user_id, d0) OR timekeeping.month_closed(NEW.tenant_id, NEW.user_id, d1) THEN
   RAISE EXCEPTION 'Tháng của đơn này đã chốt bảng công' USING ERRCODE = 'restrict_violation';
  END IF;
 END IF;
 IF TG_OP = 'UPDATE' AND OLD.status <> 'requested' THEN
  RAISE EXCEPTION 'Đơn đã xử lý — không đổi trạng thái nữa' USING ERRCODE = 'restrict_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER leave_requests_guard BEFORE INSERT OR UPDATE ON timekeeping.leave_requests FOR EACH ROW EXECUTE FUNCTION timekeeping.requests_guard();
CREATE TRIGGER overtime_requests_guard BEFORE INSERT OR UPDATE ON timekeeping.overtime_requests FOR EACH ROW EXECUTE FUNCTION timekeeping.requests_guard();

-- SỔ PHÉP: chỉ thêm; số dư = tổng dòng (như sổ cái). Cấp / tích luỹ (+), nghỉ (−), điều chỉnh (±), hết hạn (−), chuyển năm.
CREATE TABLE timekeeping.leave_ledger (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 user_id UUID NOT NULL REFERENCES core.users(id), team_id UUID,
 leave_type TEXT NOT NULL,
 year INT NOT NULL CHECK (year BETWEEN 2000 AND 2100),
 days NUMERIC(6, 1) NOT NULL CHECK (days <> 0),
 kind TEXT NOT NULL CHECK (kind IN ('grant', 'taken', 'adjust', 'expire', 'carry')),
 request_id UUID,
 note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 300),
 key TEXT NOT NULL CHECK (btrim(key) <> ''),
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, key),
 CHECK ((kind = 'taken') = (request_id IS NOT NULL)),
 CHECK (kind NOT IN ('taken', 'expire') OR days < 0)
);
CREATE INDEX leave_ledger_user ON timekeeping.leave_ledger (tenant_id, user_id, leave_type, year);
CALL core.secure_table('timekeeping.leave_ledger', 'timekeeping', 'user_id', 'team_id');
GRANT SELECT, INSERT ON timekeeping.leave_ledger TO barista_app;
CREATE TRIGGER leave_ledger_append_only BEFORE UPDATE OR DELETE ON timekeeping.leave_ledger FOR EACH ROW EXECUTE FUNCTION timekeeping.append_only();
CREATE FUNCTION timekeeping.leave_ledger_self() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.kind IN ('grant', 'adjust') AND NEW.user_id = NEW.created_by THEN
  RAISE EXCEPTION 'Không tự cấp / điều chỉnh phép cho chính mình' USING ERRCODE = 'check_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER leave_ledger_self BEFORE INSERT ON timekeeping.leave_ledger FOR EACH ROW EXECUTE FUNCTION timekeeping.leave_ledger_self();

-- CHỐT BẢNG CÔNG theo tháng (một đội, hoặc cả công ty: team_id trống). Mỗi người một dòng tổng; một người một tháng tối đa một dòng (DB giữ).
CREATE TABLE timekeeping.closes (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 period DATE NOT NULL CHECK (extract(day FROM period) = 1),
 team_id UUID,
 confirm_by DATE NOT NULL,
 closed_by UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id)
);
CALL core.secure_table('timekeeping.closes', 'timekeeping', 'closed_by', 'team_id');
GRANT SELECT, INSERT ON timekeeping.closes TO barista_app;
CREATE TRIGGER closes_append_only BEFORE UPDATE OR DELETE ON timekeeping.closes FOR EACH ROW EXECUTE FUNCTION timekeeping.append_only();

CREATE TABLE timekeeping.close_lines (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 close_id UUID NOT NULL,
 period DATE NOT NULL,
 user_id UUID NOT NULL REFERENCES core.users(id), team_id UUID,
 totals JSONB NOT NULL CHECK (jsonb_typeof(totals) = 'object'),
 confirm_by DATE NOT NULL, closed_by UUID NOT NULL,   -- chép từ lần chốt: người lao động đọc dòng của mình mà không cần thấy dòng chốt của đội
 inputs_hash TEXT NOT NULL CHECK (length(inputs_hash) = 64),
 confirmed_at TIMESTAMPTZ,
 dispute TEXT NOT NULL DEFAULT '' CHECK (length(dispute) <= 500),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, user_id, period), UNIQUE (tenant_id, id),
 FOREIGN KEY (tenant_id, close_id) REFERENCES timekeeping.closes (tenant_id, id)
);
CALL core.secure_table('timekeeping.close_lines', 'timekeeping', 'user_id', 'team_id');
GRANT SELECT, INSERT ON timekeeping.close_lines TO barista_app;
GRANT UPDATE (confirmed_at, dispute) ON timekeeping.close_lines TO barista_app;
CREATE FUNCTION timekeeping.close_lines_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE closer uuid;
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Bảng công đã chốt không xoá' USING ERRCODE = 'restrict_violation'; END IF;
 IF TG_OP = 'INSERT' THEN
  SELECT c.closed_by INTO closer FROM timekeeping.closes c WHERE c.tenant_id = NEW.tenant_id AND c.id = NEW.close_id;
  IF closer IS DISTINCT FROM NEW.closed_by THEN
   RAISE EXCEPTION 'Dòng bảng công phải mang đúng người chốt của lần chốt' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.user_id IS NOT NULL AND NEW.user_id = closer THEN
   RAISE EXCEPTION 'Không tự chốt bảng công của chính mình — nhờ quản lý khác' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.confirmed_at IS NOT NULL OR NEW.dispute <> '' THEN
   RAISE EXCEPTION 'Xác nhận bảng công là việc của người lao động' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
 END IF;
 IF (to_jsonb(NEW) - ARRAY['confirmed_at', 'dispute']) <> (to_jsonb(OLD) - ARRAY['confirmed_at', 'dispute']) THEN
  RAISE EXCEPTION 'Số liệu bảng công đã chốt không sửa — điều chỉnh ở kỳ sau' USING ERRCODE = 'restrict_violation';
 END IF;
 IF OLD.confirmed_at IS NOT NULL OR OLD.dispute <> '' THEN
  RAISE EXCEPTION 'Đã xác nhận / phản hồi bảng công này' USING ERRCODE = 'restrict_violation';
 END IF;
 IF NEW.user_id IS NULL OR NEW.user_id IS DISTINCT FROM core.me() THEN
  RAISE EXCEPTION 'Chỉ chính người lao động xác nhận bảng công của mình' USING ERRCODE = 'check_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER close_lines_guard BEFORE INSERT OR UPDATE OR DELETE ON timekeeping.close_lines FOR EACH ROW EXECUTE FUNCTION timekeeping.close_lines_guard();

-- Tháng (giờ Việt Nam) của một người đã chốt chưa — dùng bởi các trigger khoá kỳ (đọc bỏ qua RLS: người ghi có thể không thấy dòng chốt).
CREATE FUNCTION timekeeping.month_closed(p_tenant uuid, p_user uuid, p_day date) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path = pg_catalog AS $$
 SELECT EXISTS (SELECT 1 FROM timekeeping.close_lines c WHERE c.tenant_id = p_tenant AND c.user_id = p_user AND c.period = date_trunc('month', p_day)::date)
$$;
