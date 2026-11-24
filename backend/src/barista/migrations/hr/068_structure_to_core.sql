-- module: hr, core — lý do: chép MỘT LẦN lịch sử cơ cấu của hr sang bảng của lõi trước khi bỏ bảng / cột bên hr (bên dưới)
-- ADR 0256: đội và trưởng đội theo ngày về MỘT nguồn ở lõi (``core.team_terms``); module này chỉ còn cây QUẢN LÝ TRỰC TIẾP theo người.
-- Lý do chạm bảng của lõi trong migration của gói: chép MỘT LẦN lịch sử đang nằm ở ``hr.assignments.team_id`` / ``hr.team_heads`` sang lõi trước khi bỏ;
-- sau bản này gói không còn ghi cơ cấu đội ở đâu nữa. Bỏ cột / bảng mà bản cũ đang đọc ⇒ bảo trì (deployment.json).
-- Quy tắc chép — KHÔNG đoán lịch sử:
--   * Hai nguồn BỔ SUNG cho nhau: phân công đội nói "là thành viên (đội chính) từ ngày – đến ngày", nhiệm kỳ trưởng nói "là trưởng từ ngày – đến ngày". Với
--     mỗi (người, đội) hai dãy khoảng được GHÉP thành các đoạn nối nhau — vd thành viên tháng 1–6, trưởng tháng 3–4 ⇒ thành viên → trưởng → thành viên.
--     Mỗi đoạn mang dấu NGƯỜI SỬA / LÚC SỬA / lý do lùi ngày của dòng nguồn (cả hai nguồn cùng phủ ⇒ dấu của lần sửa sau cùng): ``hr.structure_on`` dùng dấu
--     này để người sửa cơ cấu không tự xác nhận phần chia liên quan.
--   * Dòng ĐANG MỞ của hr chỉ dùng khi KHỚP dòng đang mở của lõi (base/112 mở từ ngày chuyển, đánh dấu "không rõ từ khi nào"): cùng là thành viên chính /
--     cùng là trưởng. Khớp ⇒ dòng của lõi lấy ngày bắt đầu thật; lệch ⇒ giữ bên lõi (bên đang cấp quyền), ghi chú, phần ĐÃ ĐÓNG vẫn chép.
--   * Chỉ người có tài khoản (thành viên đội của lõi là tài khoản); cá nhân không tài khoản ⇒ ghi chú, không chép.
--   * Một (người, đội) chép TRỌN hoặc không chép: đoạn nào đụng ràng buộc của lõi (chồng lấn với dòng đã có) ⇒ bỏ cả nhóm, ghi chú.
-- Ghi chú ở ``core.team_terms_migration_notes`` để người xem lại. Khoảng ngày của lõi nửa mở: valid_to của lõi = valid_to (bao gồm) của hr + 1.
SET LOCAL lock_timeout = '10s';
SET CONSTRAINTS ALL IMMEDIATE;

CREATE TEMP TABLE hr_structure_src ON COMMIT DROP AS
SELECT a.tenant_id, p.user_id, a.party_id, a.team_id, 'M'::text AS kind, a.valid_from AS lo, a.valid_to + 1 AS hi, NULL::text AS head_role,
       coalesce(a.edited_by, a.created_by) AS editor, a.edited_at, a.backdate_reason AS reason
FROM hr.assignments a LEFT JOIN parties.parties p ON p.id = a.party_id AND p.tenant_id = a.tenant_id WHERE a.team_id IS NOT NULL
UNION ALL
SELECT h.tenant_id, p.user_id, h.party_id, h.team_id, 'L', h.valid_from, h.valid_to + 1, h.head_role,
       coalesce(h.edited_by, h.created_by), h.edited_at, h.backdate_reason
FROM hr.team_heads h LEFT JOIN parties.parties p ON p.id = h.party_id AND p.tenant_id = h.tenant_id;

INSERT INTO core.team_terms_migration_notes (tenant_id, note, data)
SELECT s.tenant_id, CASE s.kind WHEN 'L' THEN 'Hồ sơ làm việc ghi trưởng đội là người không có tài khoản: không chép (trưởng đội phải là tài khoản)'
                                ELSE 'Hồ sơ làm việc ghi đội cho người không có tài khoản: không chép (giao dịch của họ mang đội ghi lúc ký)' END,
       jsonb_build_object('team_id', s.team_id, 'party_id', s.party_id, 'from', s.lo, 'to', s.hi - 1)
FROM hr_structure_src s WHERE s.user_id IS NULL;
DELETE FROM hr_structure_src WHERE user_id IS NULL;

DO $$
DECLARE
    g RECORD;
    s RECORD;
    c RECORD;
    has_core BOOLEAN;
    hr_home BOOLEAN;
    hr_lead BOOLEAN;
    use_open BOOLEAN;
    -- đoạn đang gom (các đoạn liền nhau cùng tính chất gộp làm một)
    a_lo DATE; a_hi DATE; a_home BOOLEAN; a_lead BOOLEAN; a_role TEXT; a_editor UUID; a_at TIMESTAMPTZ; a_reason TEXT;
BEGIN
    FOR g IN SELECT DISTINCT tenant_id, user_id, team_id FROM hr_structure_src LOOP
        SELECT * INTO c FROM core.team_terms tt
        WHERE tt.tenant_id = g.tenant_id AND tt.user_id = g.user_id AND tt.team_id = g.team_id AND tt.valid_to IS NULL;
        has_core := FOUND;
        SELECT coalesce(bool_or(x.kind = 'M'), false), coalesce(bool_or(x.kind = 'L'), false) INTO hr_home, hr_lead FROM hr_structure_src x
        WHERE x.tenant_id = g.tenant_id AND x.user_id = g.user_id AND x.team_id = g.team_id AND x.hi IS NULL;
        use_open := false;
        IF hr_home OR hr_lead THEN
            IF has_core AND c.since_unknown AND c.is_home = hr_home AND c.is_lead = hr_lead
               AND NOT EXISTS (SELECT 1 FROM hr_structure_src x WHERE x.tenant_id = g.tenant_id AND x.user_id = g.user_id AND x.team_id = g.team_id
                               AND x.hi IS NULL AND x.lo > c.valid_from) THEN
                use_open := true;
            ELSE
                INSERT INTO core.team_terms_migration_notes (tenant_id, note, data)
                VALUES (g.tenant_id, 'Hồ sơ làm việc và Quản trị ghi khác nhau về hiện tại (đội chính / trưởng đội): giữ theo Quản trị (bên đang cấp quyền), '
                                     || 'ngày bắt đầu của dòng hiện tại để "không rõ"; lịch sử đã đóng vẫn chép',
                        jsonb_build_object('team_id', g.team_id, 'user_id', g.user_id, 'hr_member', hr_home, 'hr_lead', hr_lead,
                                           'core_member', CASE WHEN has_core THEN c.is_home END, 'core_lead', CASE WHEN has_core THEN c.is_lead END));
            END IF;
        END IF;
        BEGIN
            a_lo := NULL;
            FOR s IN
                WITH i AS (SELECT * FROM hr_structure_src x WHERE x.tenant_id = g.tenant_id AND x.user_id = g.user_id AND x.team_id = g.team_id
                                                               AND (x.hi IS NOT NULL OR use_open)),
                     pts AS (SELECT lo AS d FROM i UNION SELECT coalesce(hi, 'infinity') FROM i),
                     seg AS (SELECT d AS lo, lead(d) OVER (ORDER BY d) AS hi FROM pts),
                     cov AS (SELECT seg.lo, nullif(seg.hi, 'infinity') AS hi, m.kind IS NOT NULL AS is_home, l.kind IS NOT NULL AS is_lead, l.head_role,
                                    l.kind IS NOT NULL AND (m.kind IS NULL OR l.edited_at >= m.edited_at) AS by_lead,
                                    m.editor AS m_editor, m.edited_at AS m_at, m.reason AS m_reason, l.editor AS l_editor, l.edited_at AS l_at, l.reason AS l_reason
                             FROM seg
                             LEFT JOIN LATERAL (SELECT * FROM i WHERE i.kind = 'M' AND i.lo <= seg.lo AND coalesce(i.hi, 'infinity') > seg.lo
                                                ORDER BY i.edited_at DESC LIMIT 1) m ON true
                             LEFT JOIN LATERAL (SELECT * FROM i WHERE i.kind = 'L' AND i.lo <= seg.lo AND coalesce(i.hi, 'infinity') > seg.lo
                                                ORDER BY i.edited_at DESC LIMIT 1) l ON true
                             WHERE seg.hi IS NOT NULL AND (m.kind IS NOT NULL OR l.kind IS NOT NULL))
                SELECT false AS last, lo, hi, is_home, is_lead, head_role, CASE WHEN by_lead THEN l_editor ELSE m_editor END AS editor,
                       CASE WHEN by_lead THEN l_at ELSE m_at END AS edited_at, CASE WHEN by_lead THEN l_reason ELSE m_reason END AS reason FROM cov
                UNION ALL SELECT true, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL     -- dòng chốt: đẩy đoạn đang gom ra
                ORDER BY 1, 2
            LOOP
                IF NOT s.last AND a_lo IS NOT NULL AND a_hi = s.lo AND a_home = s.is_home AND a_lead = s.is_lead AND a_role IS NOT DISTINCT FROM s.head_role THEN
                    a_hi := s.hi;
                    IF s.edited_at > a_at THEN a_editor := s.editor; a_at := s.edited_at; a_reason := s.reason; END IF;
                    CONTINUE;
                END IF;
                IF a_lo IS NOT NULL THEN
                    IF a_hi IS NULL THEN     -- đoạn còn mở = dòng đang mở của lõi: lấy ngày bắt đầu thật
                        UPDATE core.team_terms tt SET valid_from = a_lo, since_unknown = false, head_role = coalesce(a_role, tt.head_role),
                                                      edited_by = a_editor, edited_at = a_at, backdate_reason = a_reason
                        WHERE tt.id = c.id;
                    ELSE
                        INSERT INTO core.team_terms (tenant_id, user_id, team_id, is_lead, is_home, valid_from, valid_to, head_role, edited_by, edited_at, backdate_reason)
                        VALUES (g.tenant_id, g.user_id, g.team_id, a_lead, a_home, a_lo, a_hi, a_role, a_editor, a_at, a_reason);
                    END IF;
                END IF;
                a_lo := s.lo; a_hi := s.hi; a_home := s.is_home; a_lead := s.is_lead; a_role := s.head_role;
                a_editor := s.editor; a_at := s.edited_at; a_reason := s.reason;
            END LOOP;
        EXCEPTION WHEN exclusion_violation OR unique_violation OR check_violation THEN
            INSERT INTO core.team_terms_migration_notes (tenant_id, note, data)
            VALUES (g.tenant_id, 'Lịch sử cũ của Hồ sơ làm việc chồng lấn với dòng đã có ở lõi: không chép lịch sử của người này ở đội này',
                    jsonb_build_object('team_id', g.team_id, 'user_id', g.user_id));
        END;
    END LOOP;
END $$;

DROP TABLE hr.team_heads;
DROP FUNCTION hr.check_team_head();

-- Phân công chỉ còn người quản lý trực tiếp. Dòng chỉ ghi đội (không có quản lý) không còn nghĩa ⇒ bỏ; dòng còn lại bỏ cột đội.
ALTER TABLE hr.assignments DISABLE TRIGGER assignment_append_only;
DELETE FROM hr.assignments WHERE manager_party_id IS NULL;
ALTER TABLE hr.assignments ENABLE TRIGGER assignment_append_only;
ALTER TABLE hr.assignments DROP COLUMN team_id;
ALTER TABLE hr.assignments ALTER COLUMN manager_party_id SET NOT NULL;

-- Một người một phân công tại một thời điểm: DB tự giữ bằng ràng buộc loại trừ (trigger tự truy vấn cũ lọt khi hai giao dịch ghi cùng lúc).
DROP TRIGGER assignment_valid ON hr.assignments;
DROP FUNCTION hr.check_assignment();
ALTER TABLE hr.assignments ADD CONSTRAINT assignments_one_per_day
    EXCLUDE USING gist (tenant_id WITH =, party_id WITH =, daterange(valid_from, valid_to, '[]') WITH &&);
