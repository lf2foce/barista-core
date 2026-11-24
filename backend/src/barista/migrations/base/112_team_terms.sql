-- module: core — ADR 0256: MỘT nguồn cho cơ cấu đội, có ngày hiệu lực.
-- ``core.team_members`` vẫn là bảng "ai đang ở đội nào, ai đang là trưởng" mà quyền (RLS, kernel/teams.py) đọc. Từ bản này MỌI thay đổi của nó được
-- trigger của DB chép thành dòng có ngày ở ``core.team_terms`` (không đường ghi nào bỏ qua được trigger) — câu hỏi "tại ngày X người này ở đội nào, ai là
-- trưởng" (hoa hồng theo ngày ký) đọc ``core.team_terms``; không còn bảng cơ cấu thứ hai ở module khác. Sửa LÙI NGÀY chỉ qua hai hàm
-- ``core.team_term_since`` / ``core.team_term_until`` (ghi người sửa + lý do). Không ghi trước cho tương lai: thay đổi có hiệu lực lúc ghi.
-- Khoảng ngày nửa mở [valid_from, valid_to): dòng đóng hôm nay không còn phủ hôm nay; dòng mở rồi đổi trong cùng một ngày thì sửa tại chỗ (không để lại
-- lịch sử dài 0 ngày). Thêm: mỗi đội tối đa MỘT trưởng tại một thời điểm; nhật ký cấp cha của đội ``core.team_parent_log`` (cây đội tại một ngày).
-- Chỉ thêm bảng / trigger / chỉ mục; bản cũ chạy song song vẫn ghi ``core.team_members`` như trước và được trigger chép. Ràng buộc "một trưởng" làm bản cũ
-- báo lỗi nếu đặt trưởng thứ hai trong lúc chuyển ⇒ phát hành ở chế độ bảo trì (deployment.json).

SET LOCAL lock_timeout = '10s';
CREATE EXTENSION IF NOT EXISTS btree_gist SCHEMA public;

CREATE TABLE core.team_terms_migration_notes (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id  UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    note       TEXT NOT NULL,
    data       JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CALL core.secure_tenant('core.team_terms_migration_notes');
GRANT SELECT ON core.team_terms_migration_notes TO barista_app;

CREATE TABLE core.team_terms (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES core.users(id),   -- không CASCADE: người / đội đã có lịch sử thì không xoá được (lịch sử giải thích tiền đã chia);
    team_id         UUID NOT NULL REFERENCES core.teams(id),   -- xoá cả công ty vẫn chạy (dòng này đi theo tenant_id trong cùng câu lệnh)
    is_lead         BOOLEAN NOT NULL DEFAULT false,
    is_home         BOOLEAN NOT NULL DEFAULT true,
    valid_from      DATE NOT NULL,
    valid_to        DATE,                                   -- ngày ĐẦU TIÊN không còn hiệu lực; NULL = đang hiệu lực
    since_unknown   BOOLEAN NOT NULL DEFAULT false,         -- dòng có từ trước khi có lịch sử: valid_from là ngày bắt đầu GHI, không phải ngày người đó vào đội
    head_role       TEXT,                                   -- vai công ty của người đó lúc dòng TRƯỞNG mở (memberships không có lịch sử vai — 0160)
    edited_by       UUID,
    edited_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    backdate_reason TEXT NOT NULL DEFAULT '',
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT team_terms_one_row_per_day EXCLUDE USING gist (tenant_id WITH =, user_id WITH =, team_id WITH =, daterange(valid_from, valid_to) WITH &&) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT team_terms_one_home EXCLUDE USING gist (tenant_id WITH =, user_id WITH =, daterange(valid_from, valid_to) WITH &&) WHERE (is_home) DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT team_terms_one_lead EXCLUDE USING gist (tenant_id WITH =, team_id WITH =, daterange(valid_from, valid_to) WITH &&) WHERE (is_lead) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX team_terms_team ON core.team_terms (tenant_id, team_id, valid_from);
CREATE UNIQUE INDEX team_terms_open ON core.team_terms (tenant_id, user_id, team_id) WHERE valid_to IS NULL;
CALL core.secure_tenant('core.team_terms');
GRANT SELECT ON core.team_terms TO barista_app;             -- ghi: chỉ trigger và hai hàm lùi ngày bên dưới

CREATE TABLE core.team_parent_log (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    team_id    UUID NOT NULL REFERENCES core.teams(id) ON DELETE CASCADE,
    parent_id  UUID REFERENCES core.teams(id),              -- NULL = đội gốc trong khoảng đó; đội từng là cấp cha thì không xoá được
    valid_from DATE NOT NULL,
    valid_to   DATE,
    since_unknown BOOLEAN NOT NULL DEFAULT false,
    edited_by  UUID,
    edited_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT team_parent_one_per_day EXCLUDE USING gist (tenant_id WITH =, team_id WITH =, daterange(valid_from, valid_to) WITH &&)
);
CALL core.secure_tenant('core.team_parent_log');
GRANT SELECT ON core.team_parent_log TO barista_app;

-- ── Trưởng đội: tối đa một người một đội tại một thời điểm. Ràng buộc trì hoãn tới cuối giao dịch ⇒ đổi trưởng không phụ thuộc thứ tự câu lệnh. ──
-- Dữ liệu cũ có đội hai trưởng: KHÔNG tự chọn. Có căn cứ (Hồ sơ làm việc đang ghi đúng một trong số họ là trưởng đương nhiệm) ⇒ giữ người đó; không có căn
-- cứ ⇒ đội tạm KHUYẾT trưởng (thu hẹp quyền, không mở rộng) và từng người bị hạ được ghi vào core.team_terms_migration_notes để quản trị đặt lại bằng
-- "Ghi trưởng đội từ ngày". Bảng của gói Nhân sự chỉ được đọc nếu gói đã cài trên DB này (lõi không phụ thuộc gói).
DO $$
DECLARE
    has_hr BOOLEAN := to_regclass('hr.team_heads') IS NOT NULL AND to_regclass('parties.parties') IS NOT NULL;
    r RECORD;
    keep UUID;
BEGIN
    FOR r IN SELECT tenant_id, team_id FROM core.team_members WHERE is_lead GROUP BY tenant_id, team_id HAVING count(*) > 1 LOOP
        keep := NULL;
        IF has_hr THEN
            EXECUTE 'SELECT p.user_id FROM hr.team_heads h JOIN parties.parties p ON p.id = h.party_id AND p.tenant_id = h.tenant_id
                     JOIN core.team_members tm ON tm.tenant_id = h.tenant_id AND tm.team_id = h.team_id AND tm.user_id = p.user_id AND tm.is_lead
                     WHERE h.tenant_id = $1 AND h.team_id = $2 AND h.valid_to IS NULL' INTO keep USING r.tenant_id, r.team_id;
        END IF;
        WITH demoted AS (
            UPDATE core.team_members tm SET is_lead = false
            WHERE tm.tenant_id = r.tenant_id AND tm.team_id = r.team_id AND tm.is_lead AND tm.user_id IS DISTINCT FROM keep
            RETURNING tm.user_id)
        INSERT INTO core.team_terms_migration_notes (tenant_id, note, data)
        SELECT r.tenant_id, CASE WHEN keep IS NULL THEN 'Đội có nhiều trưởng, không có căn cứ chọn: đội tạm khuyết trưởng — đặt lại bằng "Ghi trưởng đội từ ngày"'
                                 ELSE 'Đội có nhiều trưởng: giữ người đang là trưởng theo Hồ sơ làm việc, người này thành thành viên thường' END,
               jsonb_build_object('user_id', d.user_id, 'team_id', r.team_id, 'kept', keep) FROM demoted d;
    END LOOP;
END $$;
ALTER TABLE core.team_members ADD CONSTRAINT team_members_one_lead EXCLUDE USING gist (tenant_id WITH =, team_id WITH =) WHERE (is_lead)
    DEFERRABLE INITIALLY DEFERRED;

-- ── Chép thay đổi của core.team_members thành dòng có ngày ──
CREATE FUNCTION core.team_terms_sync() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, pg_catalog AS $$
DECLARE
    d DATE;
    open_from DATE;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.user_id, NEW.team_id, NEW.tenant_id) IS DISTINCT FROM (OLD.user_id, OLD.team_id, OLD.tenant_id) THEN
        RAISE EXCEPTION 'Không đổi người / đội của một dòng thành viên: xoá dòng cũ, thêm dòng mới';
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        d := core.today_of(OLD.tenant_id);
        SELECT valid_from INTO open_from FROM core.team_terms
        WHERE tenant_id = OLD.tenant_id AND user_id = OLD.user_id AND team_id = OLD.team_id AND valid_to IS NULL FOR UPDATE;
        IF TG_OP = 'UPDATE' AND NEW.is_lead = OLD.is_lead AND NEW.is_home = OLD.is_home THEN
            RETURN NULL;
        END IF;
        IF open_from >= d THEN      -- mở trong hôm nay: chưa thành lịch sử ⇒ bỏ, dòng mới (nếu có) thay chỗ
            DELETE FROM core.team_terms WHERE tenant_id = OLD.tenant_id AND user_id = OLD.user_id AND team_id = OLD.team_id AND valid_to IS NULL;
        ELSE
            UPDATE core.team_terms SET valid_to = d, edited_by = core.me(), edited_at = now()
            WHERE tenant_id = OLD.tenant_id AND user_id = OLD.user_id AND team_id = OLD.team_id AND valid_to IS NULL;
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        d := core.today_of(NEW.tenant_id);
        INSERT INTO core.team_terms (tenant_id, user_id, team_id, is_lead, is_home, valid_from, head_role, edited_by)
        VALUES (NEW.tenant_id, NEW.user_id, NEW.team_id, NEW.is_lead, NEW.is_home, d,
                CASE WHEN NEW.is_lead THEN (SELECT mb.role FROM core.memberships mb WHERE mb.tenant_id = NEW.tenant_id AND mb.user_id = NEW.user_id AND mb.active) END,
                core.me());
    END IF;
    RETURN NULL;
END $$;
-- Ba ràng buộc loại trừ khai DEFERRABLE: đổi trưởng (hạ người cũ, đặt người mới) hay đổi đội chính (xoá dòng cũ, thêm dòng mới) trong MỘT giao dịch —
-- thứ tự câu lệnh không làm vướng; cuối giao dịch mới kiểm.
CREATE TRIGGER team_terms_sync AFTER INSERT OR UPDATE OR DELETE ON core.team_members FOR EACH ROW EXECUTE FUNCTION core.team_terms_sync();

CREATE FUNCTION core.team_parent_sync() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, pg_catalog AS $$
DECLARE
    d DATE := core.today_of(NEW.tenant_id);
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.parent_id IS NOT DISTINCT FROM OLD.parent_id THEN
            RETURN NULL;
        END IF;
        DELETE FROM core.team_parent_log WHERE tenant_id = NEW.tenant_id AND team_id = NEW.id AND valid_to IS NULL AND valid_from >= d;
        UPDATE core.team_parent_log SET valid_to = d, edited_by = core.me(), edited_at = now()
        WHERE tenant_id = NEW.tenant_id AND team_id = NEW.id AND valid_to IS NULL;
    END IF;
    INSERT INTO core.team_parent_log (tenant_id, team_id, parent_id, valid_from, edited_by) VALUES (NEW.tenant_id, NEW.id, NEW.parent_id, d, core.me());
    RETURN NULL;
END $$;
CREATE TRIGGER team_parent_sync AFTER INSERT OR UPDATE OF parent_id ON core.teams FOR EACH ROW EXECUTE FUNCTION core.team_parent_sync();

-- ── Dữ liệu đang có: dòng mở từ hôm nay, đánh dấu "không rõ từ khi nào" (không đoán lịch sử — gói có căn cứ thì tự lùi ngày bằng hàm bên dưới) ──
INSERT INTO core.team_terms (tenant_id, user_id, team_id, is_lead, is_home, valid_from, since_unknown, head_role)
SELECT tm.tenant_id, tm.user_id, tm.team_id, tm.is_lead, tm.is_home, core.today_of(tm.tenant_id), true,
       CASE WHEN tm.is_lead THEN (SELECT mb.role FROM core.memberships mb WHERE mb.tenant_id = tm.tenant_id AND mb.user_id = tm.user_id AND mb.active) END
FROM core.team_members tm;
INSERT INTO core.team_parent_log (tenant_id, team_id, parent_id, valid_from, since_unknown)
SELECT t.tenant_id, t.id, t.parent_id, core.today_of(t.tenant_id), true FROM core.teams t;

-- ── Sửa lùi ngày (chỉ lịch sử; hiện tại không đổi). Gọi sau khi đã ghi thay đổi bằng core.team_members. ──
-- "Người này ở đội này (với vai trò đang ghi) TỪ ngày p_from": kéo ngày bắt đầu của dòng đang mở về trước. Dòng liền trước mà dòng này thay chỗ (đội chính
-- cũ của người đó, trưởng cũ của đội, dòng cũ của chính cặp người–đội) được rút ngắn tới p_from; dòng nằm hẳn sau p_from ⇒ từ chối (lịch sử ở giữa phải
-- sửa trước, không âm thầm xoá).
CREATE FUNCTION core.team_term_since(p_user UUID, p_team UUID, p_from DATE, p_reason TEXT) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, pg_catalog AS $$
DECLARE
    t core.team_terms%ROWTYPE;
    o core.team_terms%ROWTYPE;
    blocked INT;
BEGIN
    SELECT * INTO t FROM core.team_terms WHERE tenant_id = core.tenant() AND user_id = p_user AND team_id = p_team AND valid_to IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Người này đang không ở đội đó';
    END IF;
    IF p_from > core.today() THEN
        RAISE EXCEPTION 'Không ghi trước cho tương lai';
    END IF;
    IF p_from >= t.valid_from AND NOT t.since_unknown THEN
        RETURN;                                             -- đã có hiệu lực từ ngày đó hoặc sớm hơn
    END IF;
    IF p_from >= t.valid_from THEN                          -- chỉ xác nhận ngày bắt đầu của dòng "không rõ từ khi nào"
        UPDATE core.team_terms SET valid_from = p_from, since_unknown = false, edited_by = core.me(), edited_at = now(), backdate_reason = coalesce(p_reason, '') WHERE id = t.id;
        RETURN;
    END IF;
    SELECT count(*) INTO blocked FROM core.team_terms x
    WHERE x.tenant_id = t.tenant_id AND x.id <> t.id AND x.valid_from >= p_from AND x.valid_from < t.valid_from
      AND ((x.user_id = t.user_id AND x.team_id = t.team_id) OR (t.is_home AND x.is_home AND x.user_id = t.user_id) OR (t.is_lead AND x.is_lead AND x.team_id = t.team_id));
    IF blocked > 0 THEN
        RAISE EXCEPTION 'Trong khoảng từ ngày đó tới nay đã có thay đổi khác của người / đội này: ngày mới phải sau thay đổi gần nhất';
    END IF;
    FOR o IN SELECT * FROM core.team_terms x
             WHERE x.tenant_id = t.tenant_id AND x.id <> t.id AND x.valid_from < p_from AND (x.valid_to IS NULL OR x.valid_to > p_from)
               AND ((x.user_id = t.user_id AND x.team_id = t.team_id) OR (t.is_home AND x.is_home AND x.user_id = t.user_id) OR (t.is_lead AND x.is_lead AND x.team_id = t.team_id))
    LOOP
        UPDATE core.team_terms SET valid_to = p_from, edited_by = core.me(), edited_at = now() WHERE id = o.id;
        -- người bị thay vẫn ở lại đội với vai trò khác (trưởng cũ thành thành viên): dòng nối tiếp của họ lùi theo, không để hở khoảng
        UPDATE core.team_terms n SET valid_from = p_from, edited_by = core.me(), edited_at = now()
        WHERE n.tenant_id = o.tenant_id AND n.user_id = o.user_id AND n.team_id = o.team_id AND n.id <> t.id AND n.valid_to IS NULL AND n.valid_from = o.valid_to;
    END LOOP;
    UPDATE core.team_terms SET valid_from = p_from, since_unknown = false, edited_by = core.me(), edited_at = now(), backdate_reason = coalesce(p_reason, '') WHERE id = t.id;
END $$;

-- "Người này THÔI ở đội này / thôi làm trưởng TỪ ngày p_to": kéo ngày đóng của dòng vừa đóng (hôm nay) về trước.
CREATE FUNCTION core.team_term_until(p_user UUID, p_team UUID, p_to DATE, p_reason TEXT) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, pg_catalog AS $$
DECLARE
    t core.team_terms%ROWTYPE;
BEGIN
    SELECT * INTO t FROM core.team_terms WHERE tenant_id = core.tenant() AND user_id = p_user AND team_id = p_team AND valid_to IS NOT NULL
    ORDER BY valid_to DESC LIMIT 1 FOR UPDATE;
    IF NOT FOUND OR t.valid_to <> core.today() THEN
        RAISE EXCEPTION 'Không có dòng vừa đóng hôm nay của người này ở đội đó';
    END IF;
    IF p_to >= t.valid_to THEN
        RETURN;
    END IF;
    IF p_to <= t.valid_from THEN
        RAISE EXCEPTION 'Ngày thôi phải sau ngày bắt đầu (%)', to_char(t.valid_from, 'DD/MM/YYYY');
    END IF;
    UPDATE core.team_terms SET valid_to = p_to, edited_by = core.me(), edited_at = now(), backdate_reason = coalesce(p_reason, '') WHERE id = t.id;
    -- dòng thay chỗ mở hôm nay của CHÍNH cặp người–đội (vd thôi làm trưởng nhưng vẫn là thành viên) kéo về cùng ngày: không để hở khoảng
    UPDATE core.team_terms SET valid_from = p_to, edited_by = core.me(), edited_at = now()
    WHERE tenant_id = t.tenant_id AND user_id = t.user_id AND team_id = t.team_id AND valid_to IS NULL AND valid_from = core.today();
END $$;
GRANT EXECUTE ON FUNCTION core.team_term_since(UUID, UUID, DATE, TEXT), core.team_term_until(UUID, UUID, DATE, TEXT) TO barista_app;
