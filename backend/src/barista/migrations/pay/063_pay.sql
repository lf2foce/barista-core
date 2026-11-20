-- module: pay
-- 0150 lát 1 "Trả theo công": sổ công chỉ thêm (nguồn ghi bằng khoá của mình), khoản lương + mức có phiên bản / hiệu lực, kỳ lương nháp → gửi duyệt →
-- chốt (ghi sổ qua acct), phiếu lương từng người kèm BẢN CHỤP đầu vào. Gói mới, schema riêng, bảng rỗng; gói tắt mặc định ⇒ không bảo trì.
-- Hàng rào ở DB (không phụ thuộc đường ghi): sổ công / khoản / mức chỉ thêm; một dòng công vào tối đa một phiếu; phiếu và dòng phiếu chỉ đổi khi kỳ
-- còn nháp; kỳ đã chốt không sửa; kỳ chạy bóng không bao giờ gửi duyệt / chốt / ghi sổ / tới tay người lao động; người duyệt có phiếu trong kỳ ⇒ không chốt.
CREATE SCHEMA pay;
GRANT USAGE ON SCHEMA pay TO barista_app;

CREATE FUNCTION pay.append_only() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'Sổ công / khoản lương / mức trả chỉ thêm: sửa = dòng mới (dòng đảo, phiên bản mới, mức từ ngày mới)';
END $$;

-- SỔ CÔNG: mỗi dòng một đơn vị công nguồn đã chốt (buổi đã xác nhận, quyền hưởng đã mở, dòng nhập Excel, khoản nhập tay đã duyệt).
CREATE TABLE pay.work_entries (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 party_id UUID NOT NULL,
 user_id UUID REFERENCES core.users(id),     -- đăng nhập của người được trả (người đó thấy dòng của mình); NULL = người không đăng nhập
 entity_id UUID NOT NULL,
 work_date DATE NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('qty', 'amount')),   -- qty: số lượng × mức; amount: tiền nguồn đã tính
 unit TEXT NOT NULL DEFAULT '' CHECK (unit IN ('', 'session', 'hour', 'shift', 'day', 'piece')),
 quantity NUMERIC(12, 4) NOT NULL DEFAULT 0,
 amount_vnd BIGINT,
 component TEXT NOT NULL DEFAULT '' CHECK (component ~ '^([A-Z][A-Z0-9_]{1,29})?$'),
 role_key TEXT NOT NULL DEFAULT '' CHECK (role_key ~ '^[a-z0-9_.]{0,60}$'),
 source TEXT NOT NULL CHECK (source ~ '^[a-z_]+\.[a-z_]+$'),
 source_ref TEXT NOT NULL CHECK (btrim(source_ref) <> ''),
 key TEXT NOT NULL CHECK (btrim(key) <> '' AND length(key) <= 300),
 reverses_id UUID,
 evidence JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(evidence) = 'object'),
 payload JSONB NOT NULL,                      -- đầu vào nguồn gửi: gọi lại cùng khoá phải cùng nội dung
 approved_by UUID[] NOT NULL DEFAULT '{}',    -- người duyệt (khoản nhập tay) — trigger so với người được trả
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, key), UNIQUE (tenant_id, id), UNIQUE (tenant_id, reverses_id),
 FOREIGN KEY (tenant_id, reverses_id) REFERENCES pay.work_entries (tenant_id, id),
 CHECK ((kind = 'qty') = (unit <> '')),
 CHECK ((kind = 'qty' AND amount_vnd IS NULL AND quantity <> 0) OR (kind = 'amount' AND amount_vnd IS NOT NULL AND amount_vnd <> 0 AND quantity = 0))
);
CREATE INDEX work_entries_open ON pay.work_entries (tenant_id, entity_id, work_date);
CREATE INDEX work_entries_party ON pay.work_entries (tenant_id, party_id, work_date DESC);
CALL core.secure_table('pay.work_entries', 'pay', 'user_id');
GRANT SELECT, INSERT ON pay.work_entries TO barista_app;
CREATE TRIGGER work_entries_append_only BEFORE UPDATE OR DELETE ON pay.work_entries FOR EACH ROW EXECUTE FUNCTION pay.append_only();
-- TÁCH BẠCH NHIỆM VỤ ở DB (như base/053): dòng công do NGƯỜI của gói Lương ghi (nhập Excel, khoản nhập tay) không được ghi / duyệt cho chính người ghi /
-- người duyệt. Công do nguồn khác ghi (giáo viên xác nhận buổi của mình, hoa hồng mở theo phí đã thu) và lương cố định (suy từ mức người khác đặt) không
-- thuộc luật này — nguồn đó tự giữ phần kiểm của nó.
CREATE FUNCTION pay.work_entries_self() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF NEW.source IN ('pay.import', 'pay.manual') AND NEW.user_id IS NOT NULL AND (NEW.user_id = NEW.created_by OR NEW.user_id = ANY (NEW.approved_by)) THEN
  RAISE EXCEPTION 'Không ghi / duyệt khoản lương cho chính mình — nhờ người khác' USING ERRCODE = 'check_violation';
 END IF;
 IF NEW.source = 'pay.manual' AND cardinality(NEW.approved_by) = 0 THEN
  RAISE EXCEPTION 'Khoản nhập tay cần người duyệt' USING ERRCODE = 'check_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER work_entries_self BEFORE INSERT ON pay.work_entries FOR EACH ROW EXECUTE FUNCTION pay.work_entries_self();

-- KHOẢN NHẬP TAY CHỜ DUYỆT: chi tiết (người, số tiền, lý do) nằm ở đây dưới RLS của Lương; phiếu duyệt chỉ mang mã — người xem phiếu duyệt / nhật ký mà
-- không có quyền Lương không thấy lương của ai (0150 §8).
CREATE TABLE pay.entry_requests (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 party_id UUID NOT NULL,
 component TEXT NOT NULL CHECK (component ~ '^[A-Z][A-Z0-9_]{1,29}$'),
 amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
 work_date DATE NOT NULL,
 reason TEXT NOT NULL CHECK (btrim(reason) <> '' AND length(reason) <= 500),
 status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'recorded', 'rejected')),
 approval_id UUID, entry_id UUID,
 requested_by UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id)
);
CALL core.secure_table('pay.entry_requests', 'pay');
GRANT SELECT, INSERT ON pay.entry_requests TO barista_app;
GRANT UPDATE (status, approval_id, entry_id) ON pay.entry_requests TO barista_app;

-- KHOẢN LƯƠNG của công ty, có phiên bản (đổi = phiên bản mới); máy tính chọn từ tập đóng (0150 §5) — không ô công thức.
CREATE TABLE pay.components (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 code TEXT NOT NULL CHECK (code ~ '^[A-Z][A-Z0-9_]{1,29}$'),
 version INT NOT NULL CHECK (version >= 1),
 label TEXT NOT NULL CHECK (btrim(label) <> '' AND length(label) <= 120),
 kind TEXT NOT NULL CHECK (kind IN ('earning', 'deduction')),
 calculator TEXT NOT NULL CHECK (calculator IN ('fixed', 'rate_x_qty', 'from_source', 'manual', 'advance_offset')),
 taxable BOOLEAN NOT NULL DEFAULT true,
 expense_account TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL DEFAULT '' CHECK (source ~ '^[a-z_]{0,40}$'),   -- from_source: nguồn công nhận khoản này; advance_offset: nguồn tiền ứng được cấn
 active BOOLEAN NOT NULL DEFAULT true,
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, code, version),
 CHECK (calculator NOT IN ('fixed', 'rate_x_qty', 'from_source') OR kind = 'earning'),
 CHECK (calculator <> 'advance_offset' OR (kind = 'deduction' AND source <> '')),
 CHECK (calculator = 'advance_offset' OR expense_account <> '')
);
CALL core.secure_table('pay.components', 'pay.policy');
GRANT SELECT, INSERT ON pay.components TO barista_app;
CREATE TRIGGER components_append_only BEFORE UPDATE OR DELETE ON pay.components FOR EACH ROW EXECUTE FUNCTION pay.append_only();

-- MỨC: chỉ thêm, hiệu lực từ ngày; áp dòng CỤ THỂ NHẤT (người > vai > chung) có hiệu lực vào ngày làm.
CREATE TABLE pay.rates (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 component TEXT NOT NULL CHECK (component ~ '^[A-Z][A-Z0-9_]{1,29}$'),
 unit TEXT NOT NULL CHECK (unit IN ('session', 'hour', 'shift', 'day', 'piece', 'month')),
 role_key TEXT NOT NULL DEFAULT '' CHECK (role_key ~ '^[a-z0-9_.]{0,60}$'),
 party_id UUID,
 amount_vnd BIGINT NOT NULL CHECK (amount_vnd >= 0),
 effective_from DATE NOT NULL,
 note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 300),
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK (unit <> 'month' OR party_id IS NOT NULL)
);
CREATE INDEX rates_pick ON pay.rates (tenant_id, unit, effective_from);
CALL core.secure_table('pay.rates', 'pay.policy');
GRANT SELECT, INSERT ON pay.rates TO barista_app;
CREATE TRIGGER rates_append_only BEFORE UPDATE OR DELETE ON pay.rates FOR EACH ROW EXECUTE FUNCTION pay.append_only();

-- KỲ LƯƠNG theo pháp nhân + tháng công + ngày chi. "Đã chi" không lưu ở đây: đọc từ lệnh chi ở acct.
CREATE TABLE pay.runs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 entity_id UUID NOT NULL,
 period DATE NOT NULL CHECK (extract(day FROM period) = 1),
 pay_date DATE NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('regular', 'offcycle', 'correction', 'shadow')),
 seq INT NOT NULL CHECK (seq >= 1),
 status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'locked')),
 submission INT NOT NULL DEFAULT 0,           -- lần gửi duyệt thứ mấy: phiếu duyệt cũ (đã rút về nháp) không chốt được lần gửi mới
 approval_id UUID,
 approved_by UUID[] NOT NULL DEFAULT '{}',    -- chép từ người duyệt lúc chốt: trigger so với người có phiếu trong kỳ
 posted_on DATE,                              -- ngày hạch toán CHỐT lúc gửi duyệt (người duyệt thấy đúng ngày sẽ ghi)
 locked_at TIMESTAMPTZ, computed_at TIMESTAMPTZ,
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id), UNIQUE (tenant_id, entity_id, period, kind, seq),
 CHECK (kind <> 'shadow' OR status = 'draft'),
 CHECK ((status = 'locked') = (locked_at IS NOT NULL)),
 CHECK ((status = 'draft') = (posted_on IS NULL))
);
CREATE UNIQUE INDEX runs_one_open ON pay.runs (tenant_id, entity_id) WHERE kind = 'regular' AND status <> 'locked';
CALL core.secure_table('pay.runs', 'pay');
GRANT SELECT, INSERT, UPDATE, DELETE ON pay.runs TO barista_app;

-- PHIẾU: một người một kỳ. Chủ dòng (owner_id) chỉ điền lúc chốt ⇒ người lao động không thấy phiếu nháp, không bao giờ thấy phiếu chạy bóng.
CREATE TABLE pay.slips (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 run_id UUID NOT NULL,
 run_kind TEXT NOT NULL,
 party_id UUID NOT NULL,
 user_id UUID REFERENCES core.users(id),
 owner_id UUID REFERENCES core.users(id),
 entity_id UUID NOT NULL, period DATE NOT NULL, pay_date DATE NOT NULL,
 name TEXT NOT NULL,
 gross_vnd BIGINT NOT NULL, deductions_vnd BIGINT NOT NULL, net_vnd BIGINT NOT NULL,
 held_reason TEXT NOT NULL DEFAULT '',
 unverified TEXT[] NOT NULL DEFAULT '{}',
 inputs JSONB NOT NULL, inputs_hash TEXT NOT NULL CHECK (length(inputs_hash) = 64),
 payable_id UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id), UNIQUE (tenant_id, run_id, party_id),
 FOREIGN KEY (tenant_id, run_id) REFERENCES pay.runs (tenant_id, id),
 CHECK (run_kind <> 'shadow' OR (owner_id IS NULL AND payable_id IS NULL)),
 CHECK (owner_id IS NULL OR owner_id = user_id),
 CHECK (net_vnd = gross_vnd - deductions_vnd)
);
CREATE INDEX slips_owner ON pay.slips (tenant_id, owner_id, pay_date DESC) WHERE owner_id IS NOT NULL;
CALL core.secure_table('pay.slips', 'pay.slips', 'owner_id');
GRANT SELECT, INSERT, DELETE ON pay.slips TO barista_app;
GRANT UPDATE (owner_id, payable_id) ON pay.slips TO barista_app;

CREATE TABLE pay.slip_lines (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 slip_id UUID NOT NULL,
 seq INT NOT NULL,
 component TEXT NOT NULL,
 label TEXT NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('earning', 'deduction', 'withholding', 'offset')),
 amount_vnd BIGINT NOT NULL CHECK (amount_vnd >= 0),
 basis TEXT NOT NULL DEFAULT '',
 rule_ref TEXT NOT NULL CHECK (btrim(rule_ref) <> ''),   -- mọi dòng phiếu nói nó đến từ quy tắc / khoản nào (0150 §12)
 account TEXT NOT NULL DEFAULT '',
 unverified BOOLEAN NOT NULL DEFAULT false,
 UNIQUE (tenant_id, slip_id, seq),
 FOREIGN KEY (tenant_id, slip_id) REFERENCES pay.slips (tenant_id, id)
);
CALL core.secure_child('pay.slip_lines', 'pay.slips', 'slip_id');
GRANT SELECT, INSERT, DELETE ON pay.slip_lines TO barista_app;

-- Một dòng công vào TỐI ĐA một phiếu (khoá chính theo dòng công) — kể cả hai lần lập kỳ chạy song song.
CREATE TABLE pay.slip_entries (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 slip_id UUID NOT NULL,
 entry_id UUID NOT NULL,
 UNIQUE (tenant_id, entry_id),
 FOREIGN KEY (tenant_id, slip_id) REFERENCES pay.slips (tenant_id, id),
 FOREIGN KEY (tenant_id, entry_id) REFERENCES pay.work_entries (tenant_id, id)
);
CREATE INDEX slip_entries_slip ON pay.slip_entries (tenant_id, slip_id);
CALL core.secure_child('pay.slip_entries', 'pay.slips', 'slip_id');
GRANT SELECT, INSERT, DELETE ON pay.slip_entries TO barista_app;

-- Kỳ: chỉ nháp → gửi duyệt → (rút về nháp | chốt). Đã gửi duyệt ⇒ đầu vào đóng băng; đã chốt ⇒ không sửa gì. Chốt: có người duyệt, người duyệt không có
-- phiếu trong kỳ, mọi phiếu đã ghi nghĩa vụ. Hàm đọc phiếu / kỳ bỏ qua RLS (người ghi có thể không thấy hết) và chỉ trả lỗi / cho qua.
CREATE FUNCTION pay.runs_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE free CONSTANT text[] := ARRAY['status', 'approval_id', 'approved_by', 'locked_at', 'posted_on'];
BEGIN
 IF TG_OP = 'DELETE' THEN
  IF OLD.status <> 'draft' THEN RAISE EXCEPTION 'Kỳ lương đã gửi duyệt / đã chốt: không xoá được' USING ERRCODE = 'restrict_violation'; END IF;
  RETURN OLD;
 END IF;
 IF OLD.status = 'locked' THEN
  RAISE EXCEPTION 'Kỳ lương đã chốt: không sửa — sửa sai bằng kỳ sau' USING ERRCODE = 'restrict_violation';
 END IF;
 IF OLD.status <> 'draft' AND (to_jsonb(NEW) - free) <> (to_jsonb(OLD) - free) THEN
  RAISE EXCEPTION 'Kỳ lương đã gửi duyệt: đầu vào đóng băng — rút về nháp để sửa' USING ERRCODE = 'restrict_violation';
 END IF;
 IF OLD.status <> 'draft' AND NEW.status <> 'draft' AND NEW.posted_on IS DISTINCT FROM OLD.posted_on THEN
  RAISE EXCEPTION 'Ngày hạch toán chốt lúc gửi duyệt — chỉ đổi khi rút kỳ về nháp' USING ERRCODE = 'restrict_violation';
 END IF;
 IF NEW.status <> OLD.status AND NOT ((OLD.status = 'draft' AND NEW.status = 'submitted') OR (OLD.status = 'submitted' AND NEW.status IN ('draft', 'locked'))) THEN
  RAISE EXCEPTION 'Chuyển trạng thái kỳ lương không hợp lệ: % → %', OLD.status, NEW.status USING ERRCODE = 'restrict_violation';
 END IF;
 IF NEW.status = 'locked' THEN
  IF NEW.posted_on IS DISTINCT FROM OLD.posted_on THEN
   RAISE EXCEPTION 'Chốt kỳ lương phải ghi đúng ngày hạch toán đã gửi duyệt' USING ERRCODE = 'restrict_violation';
  END IF;
  IF cardinality(NEW.approved_by) = 0 THEN
   RAISE EXCEPTION 'Chốt kỳ lương cần người duyệt' USING ERRCODE = 'restrict_violation';
  END IF;
  IF EXISTS (SELECT 1 FROM pay.slips s WHERE s.tenant_id = NEW.tenant_id AND s.run_id = NEW.id AND s.user_id = ANY (NEW.approved_by)) THEN
   RAISE EXCEPTION 'Người duyệt có phiếu trong kỳ lương này — cần người khác duyệt (0150 §8)' USING ERRCODE = 'restrict_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pay.slips s WHERE s.tenant_id = NEW.tenant_id AND s.run_id = NEW.id)
     OR EXISTS (SELECT 1 FROM pay.slips s WHERE s.tenant_id = NEW.tenant_id AND s.run_id = NEW.id AND (s.payable_id IS NULL OR s.held_reason <> '')) THEN
   RAISE EXCEPTION 'Chốt kỳ lương: kỳ rỗng hoặc còn phiếu chưa ghi nghĩa vụ' USING ERRCODE = 'restrict_violation';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER runs_guard BEFORE UPDATE OR DELETE ON pay.runs FOR EACH ROW EXECUTE FUNCTION pay.runs_guard();

-- Phiếu chỉ thêm / xoá khi kỳ còn nháp (tính lại); cập nhật DUY NHẤT: lúc chốt (kỳ đang gửi duyệt) điền nghĩa vụ + chủ dòng, một lần.
CREATE FUNCTION pay.slips_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r record;
BEGIN
 SELECT x.status, x.kind INTO r FROM pay.runs x WHERE x.tenant_id = coalesce(NEW.tenant_id, OLD.tenant_id) AND x.id = coalesce(NEW.run_id, OLD.run_id) FOR SHARE;
 IF TG_OP = 'UPDATE' THEN
  IF r.status = 'submitted' AND OLD.owner_id IS NULL AND OLD.payable_id IS NULL AND NEW.payable_id IS NOT NULL
     AND (to_jsonb(NEW) - ARRAY['owner_id', 'payable_id']) = (to_jsonb(OLD) - ARRAY['owner_id', 'payable_id']) THEN
   RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Phiếu lương không sửa: tính lại kỳ nháp, hoặc sửa sai ở kỳ sau' USING ERRCODE = 'restrict_violation';
 END IF;
 IF r.status IS DISTINCT FROM 'draft' THEN
  RAISE EXCEPTION 'Kỳ lương đã gửi duyệt / đã chốt: phiếu không thêm / xoá được' USING ERRCODE = 'restrict_violation';
 END IF;
 IF TG_OP = 'INSERT' AND NEW.run_kind IS DISTINCT FROM r.kind THEN
  RAISE EXCEPTION 'Loại kỳ của phiếu khác loại kỳ' USING ERRCODE = 'restrict_violation';
 END IF;
 RETURN coalesce(NEW, OLD);
END $$;
CREATE TRIGGER slips_guard BEFORE INSERT OR UPDATE OR DELETE ON pay.slips FOR EACH ROW EXECUTE FUNCTION pay.slips_guard();

-- Dòng phiếu / dòng công của phiếu: không sửa; thêm / xoá chỉ khi kỳ còn nháp; kỳ chạy bóng không giữ dòng công nào.
CREATE FUNCTION pay.slip_child_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r record;
BEGIN
 IF TG_OP = 'UPDATE' THEN
  RAISE EXCEPTION 'Dòng phiếu lương không sửa' USING ERRCODE = 'restrict_violation';
 END IF;
 SELECT x.status, x.kind INTO r FROM pay.slips s JOIN pay.runs x ON x.tenant_id = s.tenant_id AND x.id = s.run_id
 WHERE s.tenant_id = coalesce(NEW.tenant_id, OLD.tenant_id) AND s.id = coalesce(NEW.slip_id, OLD.slip_id) FOR SHARE OF x;
 IF r.status IS DISTINCT FROM 'draft' THEN
  RAISE EXCEPTION 'Kỳ lương đã gửi duyệt / đã chốt: dòng phiếu không thêm / xoá được' USING ERRCODE = 'restrict_violation';
 END IF;
 IF TG_TABLE_NAME = 'slip_entries' AND TG_OP = 'INSERT' AND r.kind = 'shadow' THEN
  RAISE EXCEPTION 'Kỳ chạy bóng không giữ dòng công' USING ERRCODE = 'restrict_violation';
 END IF;
 RETURN coalesce(NEW, OLD);
END $$;
CREATE TRIGGER slip_lines_guard BEFORE INSERT OR UPDATE OR DELETE ON pay.slip_lines FOR EACH ROW EXECUTE FUNCTION pay.slip_child_guard();
CREATE TRIGGER slip_entries_guard BEFORE INSERT OR UPDATE OR DELETE ON pay.slip_entries FOR EACH ROW EXECUTE FUNCTION pay.slip_child_guard();
