-- module: core, ledger — lý do: pháp nhân là bảng lõi; mọi bút toán (sổ cái của lõi) phải mang pháp nhân ngay khi bảng ra đời
-- 012 (0099): NHIỀU PHÁP NHÂN TRONG MỘT CÔNG TY — pháp nhân là CHIỀU, không phải công ty riêng: khách, lead, dự án, đội dùng chung; tiền, hoá
-- đơn, tài khoản ngân hàng, thuế tách theo mã số thuế. Mỗi bút toán thuộc ĐÚNG MỘT pháp nhân (bút toán đã cân theo từng bút toán ⇒ tự cân
-- theo từng pháp nhân). Mỗi công ty có ít nhất một pháp nhân MẶC ĐỊNH (tạo sẵn từ tên công ty) ⇒ công ty một pháp nhân không thấy gì khác.
-- Chỉ THÊM: dòng không chọn pháp nhân ⇒ trigger điền pháp nhân mặc định của công ty ⇒ bản cũ còn chạy lúc chuyển vẫn ghi được.

CREATE TABLE core.legal_entities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    code        TEXT NOT NULL CHECK (code ~ '^[A-Z0-9_-]{1,20}$'),
    name        TEXT NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 200),
    tax_code    TEXT NOT NULL DEFAULT '' CHECK (tax_code ~ '^[0-9-]{0,20}$'),
    address     TEXT NOT NULL DEFAULT '' CHECK (length(address) <= 300),
    is_default  BOOLEAN NOT NULL DEFAULT false,
    active      BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code)
);
CREATE UNIQUE INDEX uq_legal_entity_default ON core.legal_entities (tenant_id) WHERE is_default;
CALL core.secure_tenant('core.legal_entities');   -- tên pháp nhân ai trong công ty cũng đọc được (hiện trên chứng từ); sửa: lệnh quản trị
GRANT SELECT, INSERT, UPDATE ON core.legal_entities TO barista_app;

CREATE FUNCTION core.default_entity() RETURNS UUID LANGUAGE sql STABLE AS $$
    SELECT id FROM core.legal_entities WHERE tenant_id = core.tenant() AND is_default
$$;

-- Trigger dùng chung: dòng chưa chọn pháp nhân ⇒ pháp nhân mặc định CỦA CÔNG TY CỦA DÒNG (theo tenant_id của dòng — không phụ thuộc phiên:
-- vòng quét / kết nối hệ thống ghi cũng đúng). Bảng tiền của module gắn trigger này trong migration của module đó.
CREATE FUNCTION core.fill_entity() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $$
BEGIN
    IF NEW.entity_id IS NULL THEN
        SELECT e.id INTO NEW.entity_id FROM core.legal_entities e WHERE e.tenant_id = NEW.tenant_id AND e.is_default;
    END IF;
    RETURN NEW;
END $$;

-- mỗi công ty đang có: một pháp nhân mặc định mang tên công ty
INSERT INTO core.legal_entities (tenant_id, code, name, is_default)
SELECT id, 'CHINH', name, true FROM core.tenants;

-- bút toán: pháp nhân (sổ chỉ thêm ⇒ tạm tắt khoá sửa để điền dữ liệu cũ, bật lại ngay)
ALTER TABLE ledger.journals ADD COLUMN entity_id UUID REFERENCES core.legal_entities(id);
ALTER TABLE ledger.journals DISABLE TRIGGER t_journals_immutable;
UPDATE ledger.journals j SET entity_id = e.id FROM core.legal_entities e WHERE e.tenant_id = j.tenant_id AND e.is_default;
ALTER TABLE ledger.journals ENABLE TRIGGER t_journals_immutable;
ALTER TABLE ledger.journals ALTER COLUMN entity_id SET NOT NULL;
CREATE TRIGGER t_journals_entity BEFORE INSERT ON ledger.journals FOR EACH ROW EXECUTE FUNCTION core.fill_entity();
CREATE INDEX idx_journals_entity ON ledger.journals (tenant_id, entity_id, at);
