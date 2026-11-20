-- module: hr
-- 0202: CHỨNG CHỈ / GIẤY PHÉP HÀNH NGHỀ của nhân sự — chung cho mọi ngành. LOẠI chứng chỉ là dữ liệu của công ty (không loại nào viết trong code;
-- gói ngành góp loại mặc định qua lệnh hr.save_credential_kind). Mỗi người nhiều chứng chỉ; trạng thái (còn hạn / sắp hết / hết hạn / bị thay / thu hồi)
-- TÍNH lúc đọc, không lưu. SỐ chứng chỉ + bản scan là dữ liệu cá nhân ⇒ bảng riêng, tài nguyên riêng `hr.credential_private` (trưởng đội thấy trạng
-- thái của đội, không thấy số / bản scan). Sổ nhắc `credential_notices`: mỗi chứng chỉ mỗi mốc (sắp hết hạn / đã hết hạn) báo đúng một lần.
-- Bảng mới, rỗng; bản cũ không đọc ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

CREATE TABLE hr.credential_kinds (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 key TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{0,39}$'),
 name TEXT NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 120),
 issuer_hint TEXT NOT NULL DEFAULT '' CHECK (length(issuer_hint) <= 200),
 expires BOOLEAN NOT NULL DEFAULT true,                                                       -- loại có hạn (bằng lái, chứng chỉ có thời hạn) hay vĩnh viễn
 validity_months INT CHECK (validity_months IS NULL OR validity_months BETWEEN 1 AND 600),     -- thời hạn mặc định: trống ngày hết hạn ⇒ ngày cấp + số tháng
 remind_days INT NOT NULL DEFAULT 30 CHECK (remind_days BETWEEN 0 AND 365),                   -- báo trước bao nhiêu ngày
 required_roles TEXT[] NOT NULL DEFAULT '{}',                                                 -- vai phải có (trống = không bắt buộc ai)
 required_teams UUID[] NOT NULL DEFAULT '{}',                                                 -- đội (cả cây con) phải có
 active BOOLEAN NOT NULL DEFAULT true,
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, key), UNIQUE (tenant_id, id),
 CHECK (expires OR validity_months IS NULL)
);
-- danh mục cấu hình, không phải dữ liệu cá nhân: mọi thành viên đọc (tên loại trên chứng chỉ của mình); ghi chỉ qua lệnh có quyền hr.credentials
CALL core.secure_tenant('hr.credential_kinds');
GRANT SELECT, INSERT, UPDATE ON hr.credential_kinds TO barista_app;

-- MỘT chứng chỉ của MỘT người. Chỉ thêm; sửa sai = thu hồi + ghi dòng mới. Cập nhật được: thu hồi (một lần), xác nhận dòng nạp từ hệ cũ (một lần),
-- và người / đội chủ dòng cho RLS (user_id = đăng nhập của chính người đó — "của tôi"; team_id = đội hiện tại — "đội tôi quản", đồng bộ khi đổi đội).
CREATE TABLE hr.credentials (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 party_id UUID NOT NULL,
 user_id UUID REFERENCES core.users(id),
 team_id UUID,
 kind_id UUID NOT NULL,
 issued_on DATE, expires_on DATE,                                                             -- expires_on = ngày CUỐI còn hiệu lực
 issuer TEXT NOT NULL DEFAULT '' CHECK (length(issuer) <= 200),
 note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 500),
 recorded_on DATE NOT NULL DEFAULT core.today(),
 loaded_at TIMESTAMPTZ,                                                                       -- nạp từ hệ cũ (0185): tham chiếu, không nhắc tới khi xác nhận
 verified_on DATE, verified_by UUID,                                                          -- người phụ trách xác nhận dòng nạp còn đúng ⇒ nhắc từ ngày đó
 revoked_at TIMESTAMPTZ, revoked_by UUID, revoke_reason TEXT NOT NULL DEFAULT '' CHECK (length(revoke_reason) <= 300),
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id),
 FOREIGN KEY (tenant_id, kind_id) REFERENCES hr.credential_kinds (tenant_id, id),
 CHECK (expires_on IS NULL OR issued_on IS NULL OR expires_on >= issued_on),
 CHECK ((revoked_at IS NULL) = (revoked_by IS NULL)),
 CHECK (verified_on IS NULL OR loaded_at IS NOT NULL)
);
CREATE INDEX credentials_party ON hr.credentials (tenant_id, party_id, kind_id);
CREATE INDEX credentials_user ON hr.credentials (tenant_id, user_id) WHERE user_id IS NOT NULL;
-- vòng quét nhắc hạn: chỉ dòng còn sống, có hạn
CREATE INDEX credentials_due ON hr.credentials (tenant_id, expires_on) WHERE revoked_at IS NULL AND expires_on IS NOT NULL;
CALL core.secure_table('hr.credentials', 'hr.credentials', 'user_id', 'team_id');
GRANT SELECT, INSERT ON hr.credentials TO barista_app;
GRANT UPDATE (user_id, team_id, verified_on, verified_by, revoked_at, revoked_by, revoke_reason) ON hr.credentials TO barista_app;
CREATE FUNCTION hr.credentials_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Chứng chỉ chỉ thêm: sai thì thu hồi rồi ghi dòng mới'; END IF;
 IF (to_jsonb(NEW) - 'user_id' - 'team_id' - 'verified_on' - 'verified_by' - 'revoked_at' - 'revoked_by' - 'revoke_reason')
    <> (to_jsonb(OLD) - 'user_id' - 'team_id' - 'verified_on' - 'verified_by' - 'revoked_at' - 'revoked_by' - 'revoke_reason')
    OR (OLD.revoked_at IS NOT NULL AND (NEW.revoked_at, NEW.revoked_by, NEW.revoke_reason) IS DISTINCT FROM (OLD.revoked_at, OLD.revoked_by, OLD.revoke_reason))
    OR (OLD.verified_on IS NOT NULL AND (NEW.verified_on, NEW.verified_by) IS DISTINCT FROM (OLD.verified_on, OLD.verified_by))
 THEN RAISE EXCEPTION 'Chứng chỉ chỉ thêm: sai thì thu hồi rồi ghi dòng mới (thu hồi / xác nhận chỉ một lần)'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER credentials_guard BEFORE UPDATE OR DELETE ON hr.credentials FOR EACH ROW EXECUTE FUNCTION hr.credentials_guard();

-- SỐ chứng chỉ + BẢN SCAN (kho tệp — 0122, khoá ngoại file_id, không bytea): tài nguyên riêng — chính người đó và người phụ trách nhân sự cả công ty.
-- Trưởng đội đọc chứng chỉ của đội nhưng không có dòng ở đây ⇒ không thấy số, không mở được bản scan (kho tệp kiểm dòng trỏ tới tệp dưới RLS).
CREATE TABLE hr.credential_private (
 credential_id UUID PRIMARY KEY,
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 user_id UUID REFERENCES core.users(id),
 number TEXT NOT NULL DEFAULT '' CHECK (length(number) <= 80),
 file_id UUID REFERENCES core.files(id),
 file_name TEXT NOT NULL DEFAULT '' CHECK (length(file_name) <= 300),
 file_type TEXT NOT NULL DEFAULT '' CHECK (length(file_type) <= 120),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id, credential_id) REFERENCES hr.credentials (tenant_id, id)
);
CREATE INDEX credential_private_file ON hr.credential_private (file_id) WHERE file_id IS NOT NULL;
CALL core.secure_table('hr.credential_private', 'hr.credential_private', 'user_id');
GRANT SELECT, INSERT ON hr.credential_private TO barista_app;
GRANT UPDATE (user_id, file_id, file_name, file_type) ON hr.credential_private TO barista_app;
CREATE FUNCTION hr.credential_private_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP = 'DELETE' OR NEW.number <> OLD.number OR NEW.credential_id <> OLD.credential_id THEN
  RAISE EXCEPTION 'Số chứng chỉ không sửa: sai thì thu hồi rồi ghi dòng mới';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER credential_private_guard BEFORE UPDATE OR DELETE ON hr.credential_private FOR EACH ROW EXECUTE FUNCTION hr.credential_private_guard();

-- SỔ NHẮC: một dòng = một mốc của một chứng chỉ đã được xử lý (đã báo, hoặc không có ai để báo). Khoá chính chặn báo lặp kể cả hai vòng quét cùng lúc.
CREATE TABLE hr.credential_notices (
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 credential_id UUID NOT NULL,
 milestone TEXT NOT NULL CHECK (milestone IN ('expiring', 'expired')),
 recipients INT NOT NULL DEFAULT 0,
 sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id, credential_id, milestone),
 FOREIGN KEY (tenant_id, credential_id) REFERENCES hr.credentials (tenant_id, id)
);
CALL core.secure_table('hr.credential_notices', 'hr.credentials');
GRANT SELECT, INSERT ON hr.credential_notices TO barista_app;
