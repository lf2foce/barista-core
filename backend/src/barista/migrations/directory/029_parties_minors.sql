-- module: parties
-- 0112: dữ liệu trẻ em trong định danh. Chỉ THÊM (bản cũ đang chạy không vỡ): bảng mới, cột sinh tự động, cột có mặc định.
-- Ngày sinh ở bảng riêng theo `parties.contacts`, không phải cột của parties.parties: RLS theo dòng, kế toán có quyền danh mục chọn bên nhận tiền thì đọc được mọi cột của danh mục.
CREATE TABLE parties.birth_dates (
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 party_id UUID NOT NULL,
 born_on DATE NOT NULL CHECK (born_on >= DATE '1900-01-01'),
 source TEXT NOT NULL DEFAULT '',
 recorded_by UUID,
 at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id, party_id),
 FOREIGN KEY (tenant_id, party_id) REFERENCES parties.parties (tenant_id, id)
);
CALL core.secure_table('parties.birth_dates', 'parties.contacts');
GRANT SELECT, INSERT, UPDATE, DELETE ON parties.birth_dates TO barista_app;

-- Đồng ý của ai: cột sinh từ given_by_party_id nên không có cách nào ghi lệch, bản cũ chèn dòng không cần biết cột này.
UPDATE parties.consents SET given_by_party_id = NULL WHERE given_by_party_id = party_id;
ALTER TABLE parties.consents
  ADD COLUMN given_as TEXT GENERATED ALWAYS AS (CASE WHEN given_by_party_id IS NULL THEN 'subject' ELSE 'guardian' END) STORED,
  ADD CONSTRAINT consents_giver_is_someone_else CHECK (given_by_party_id IS NULL OR given_by_party_id <> party_id);
COMMENT ON COLUMN parties.consents.given_as IS 'subject = chính người này; guardian = người giám hộ đồng ý thay. Dòng của người không còn là giám hộ vẫn giữ làm vết, nhưng không tính khi xét trạng thái (0112)';

-- Vết kết thúc quan hệ: ai / vì sao / theo quy định nào. Vòng quét đủ tuổi để ended_by trống và ghi ended_rule.
ALTER TABLE parties.relations
  ADD COLUMN ended_by UUID,
  ADD COLUMN ended_reason TEXT NOT NULL DEFAULT '',
  ADD COLUMN ended_rule TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_relations_open_guardian ON parties.relations (tenant_id, related_party_id) WHERE kind = 'guardian_of' AND ended_on IS NULL;
