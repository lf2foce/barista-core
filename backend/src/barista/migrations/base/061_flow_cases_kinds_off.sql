-- module: flow — lý do: hiệu năng (perf doc §14). Chính sách "gói của hồ sơ có bật không" trên flow.cases gọi core.subject_on(subject_kind) ở TỪNG
-- hồ sơ (mỗi lần một EXISTS core.tenant_packs qua RLS; đo 10/11: ~3.000 lần mỗi lượt flow.needs_action / flow.board, ~90ms) trong khi tập "loại
-- có gói đang tắt" chỉ vài phần tử. Dùng đúng mẫu 058: InitPlan core.kinds_off() một lần mỗi câu. Cùng nghĩa với core.subject_on cho mọi loại trong
-- sổ, loại lạ, rỗng, NULL (test_kinds_off_says_exactly_what_subject_on_says). Không bảo trì: chỉ đổi chữ chính sách.
SET LOCAL lock_timeout = '10s';
ALTER POLICY p_pack_on ON flow.cases USING (coalesce(NOT (subject_kind = ANY ((SELECT core.kinds_off())::text[])), true));
