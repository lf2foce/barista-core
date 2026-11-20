-- module: core
-- 0153: mặc định của gói thêm loại "record" — dòng cấu hình ghi qua lệnh của module sở hữu (vd chặng lead mặc định qua crm.save_stage).
-- Chỉ nới CHECK (thêm giá trị) ⇒ bản cũ vẫn ghi được mọi loại nó biết, không bảo trì.
ALTER TABLE core.pack_installs DROP CONSTRAINT pack_installs_kind_check;
ALTER TABLE core.pack_installs ADD CONSTRAINT pack_installs_kind_check
    CHECK (kind = ANY (ARRAY['role', 'grant', 'team', 'chain', 'rule', 'flow', 'vocab', 'setting', 'record']));
