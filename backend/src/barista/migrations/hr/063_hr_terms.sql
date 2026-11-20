-- module: hr
-- 0150 lát 1: ĐIỀU KIỆN TRẢ theo hợp đồng, tài nguyên riêng `hr.terms` (người được xem hồ sơ làm việc không mặc nhiên thấy). Lát này chỉ có LOẠI
-- hợp đồng (quy tắc khấu trừ 10% của người không ký hợp đồng lao động / ký dưới 3 tháng dựa vào đó); mức trả, người phụ thuộc, mã định danh ở lát 2.
-- Chỉ thêm: đổi điều kiện = dòng mới có ngày hiệu lực, dòng hiệu lực tại một ngày = dòng có ngày hiệu lực mới nhất không sau ngày đó.
-- Bảng mới, rỗng; gói cũ không đọc ⇒ không bảo trì.
CREATE TABLE hr.terms (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL DEFAULT core.tenant(),
 party_id UUID NOT NULL,
 user_id UUID REFERENCES core.users(id),          -- đăng nhập của chính người đó lúc ghi (chính họ đọc dòng của mình — phạm vi "own")
 contract_kind TEXT NOT NULL CHECK (contract_kind IN ('indefinite', 'fixed_term', 'probation', 'service')),
 term_months INT CHECK (term_months IS NULL OR term_months BETWEEN 1 AND 36),
 effective_from DATE NOT NULL,
 note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 500),
 created_by UUID, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, party_id, effective_from), UNIQUE (tenant_id, id),
 CHECK ((contract_kind = 'fixed_term') = (term_months IS NOT NULL))
);
CREATE INDEX terms_party ON hr.terms (tenant_id, party_id, effective_from DESC);
CALL core.secure_table('hr.terms', 'hr.terms', 'user_id');
GRANT SELECT, INSERT ON hr.terms TO barista_app;
CREATE FUNCTION hr.terms_append_only() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'Điều kiện trả chỉ thêm: đổi = dòng mới có ngày hiệu lực';
END $$;
CREATE TRIGGER terms_append_only BEFORE UPDATE OR DELETE ON hr.terms FOR EACH ROW EXECUTE FUNCTION hr.terms_append_only();
