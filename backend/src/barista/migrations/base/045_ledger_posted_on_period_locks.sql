-- module: ledger
-- 0119: NGÀY HẠCH TOÁN trên bút toán + KHOÁ KỲ theo pháp nhân. Cần BẢO TRÌ (deployment.json): bản cũ ghi bút toán không có ngày hạch toán và
-- không biết khoá kỳ ⇒ không chạy song song với bản mới. Mã Python luôn chọn ngày (`ledger.post` bắt buộc `on`, test quét mọi chỗ gọi).
--
-- `at` giữ nghĩa LÚC GHI (kiểm toán). `posted_on` = ngày hạch toán theo giờ Việt Nam; dòng cũ lấy theo `at` (sổ chỉ thêm ⇒ tạm tắt khoá sửa như
-- 012, bật lại ngay). Trigger khoá kỳ chỉ BEFORE INSERT ⇒ UPDATE điền ngày ở đây không đi qua nó — mốc khoá chưa tồn tại lúc này.
ALTER TABLE ledger.journals ADD COLUMN posted_on DATE;
ALTER TABLE ledger.journals DISABLE TRIGGER t_journals_immutable;
UPDATE ledger.journals SET posted_on = (at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date;
ALTER TABLE ledger.journals ENABLE TRIGGER t_journals_immutable;
ALTER TABLE ledger.journals ALTER COLUMN posted_on SET NOT NULL;
CREATE INDEX idx_journals_posted_on ON ledger.journals (entity_id, posted_on);

-- Mốc khoá: CHỈ THÊM, dòng mới nhất của pháp nhân là mốc hiện hành (NULL = mở hết sau khi mở lại toàn bộ). Lùi mốc chỉ qua lệnh sau duyệt
-- (approved_by ghi tên người duyệt) — kernel/ledger.reopen_to.
CREATE TABLE ledger.period_locks (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id       UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    entity_id       UUID NOT NULL REFERENCES core.legal_entities(id),
    locked_through  DATE,
    reason          TEXT NOT NULL DEFAULT '' CHECK (length(reason) <= 500),
    created_by      UUID REFERENCES core.users(id),
    approved_by     UUID[] NOT NULL DEFAULT '{}',
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_period_locks_entity ON ledger.period_locks (entity_id, id DESC);
CALL core.secure_table('ledger.period_locks', 'ledger');   -- như bút toán: chỉ người sổ cái cả công ty đọc / ghi
CREATE TRIGGER t_period_locks_immutable BEFORE DELETE OR UPDATE ON ledger.period_locks FOR EACH ROW EXECUTE FUNCTION ledger.immutable();
GRANT SELECT, INSERT ON ledger.period_locks TO barista_app;

-- Chặn ở DB: mọi đường ghi (lệnh, nhập, migration chạy lệnh, INSERT thẳng) cùng một luật. Khoá advisory DÙNG CHUNG theo pháp nhân trước khi đọc
-- mốc; tiến / lùi mốc lấy khoá RIÊNG cùng khoá (kernel/ledger.hold_period) ⇒ không bút toán nào lọt dưới mốc vừa tiến, bút toán song song của
-- cùng pháp nhân không chặn nhau. Tên trigger sắp SAU `t_journals_entity` (e < p — Postgres chạy trigger cùng loại theo tên) ⇒ pháp nhân đã
-- được điền mặc định; vẫn NULL ⇒ lỗi, không khoá theo NULL.
CREATE FUNCTION ledger.check_period_lock() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $$
DECLARE lt DATE;
BEGIN
    IF NEW.entity_id IS NULL THEN
        RAISE EXCEPTION 'bút toán chưa có pháp nhân lúc kiểm khoá kỳ';
    END IF;
    -- SQL ghi thẳng không khai ngày (migration viết trước 0119, vd 017 chạy SAU file này trên DB dựng dở vì gói nền chạy trước) ⇒ ngày Việt Nam
    -- của lúc ghi — đúng luật đã điền cho dòng cũ ở trên. Không phải đường của mã ứng dụng.
    NEW.posted_on := coalesce(NEW.posted_on, (NEW.at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date);
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('ledger.period_lock:' || NEW.entity_id::text, 0));
    SELECT l.locked_through INTO lt FROM ledger.period_locks l WHERE l.entity_id = NEW.entity_id ORDER BY l.id DESC LIMIT 1;
    IF lt IS NOT NULL AND NEW.posted_on <= lt THEN
        RAISE EXCEPTION 'Kỳ đã khoá tới % — chọn ngày hạch toán sau ngày đó, hoặc xin mở lại kỳ (có duyệt)', to_char(lt, 'DD/MM/YYYY');
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER t_journals_period_lock BEFORE INSERT ON ledger.journals FOR EACH ROW EXECUTE FUNCTION ledger.check_period_lock();

-- Dòng bút toán: thêm cặp dòng cân vào một bút toán ĐÃ CÓ thuộc kỳ đã khoá cũng là ghi vào kỳ đó ⇒ cùng luật, cùng khoá advisory.
CREATE FUNCTION ledger.check_posting_period_lock() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $$
DECLARE e UUID; d DATE; lt DATE;
BEGIN
    SELECT j.entity_id, j.posted_on INTO e, d FROM ledger.journals j WHERE j.id = NEW.journal_id;
    IF e IS NULL THEN
        RETURN NEW;   -- khoá ngoại báo lỗi bút toán không tồn tại
    END IF;
    PERFORM pg_advisory_xact_lock_shared(hashtextextended('ledger.period_lock:' || e::text, 0));
    SELECT l.locked_through INTO lt FROM ledger.period_locks l WHERE l.entity_id = e ORDER BY l.id DESC LIMIT 1;
    IF lt IS NOT NULL AND d <= lt THEN
        RAISE EXCEPTION 'Kỳ đã khoá tới % — không thêm dòng vào bút toán ngày %', to_char(lt, 'DD/MM/YYYY'), to_char(d, 'DD/MM/YYYY');
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER t_postings_period_lock BEFORE INSERT ON ledger.postings FOR EACH ROW EXECUTE FUNCTION ledger.check_posting_period_lock();

-- Mốc chỉ TIẾN bằng INSERT của ứng dụng. LÙI (hoặc mở hết) chỉ qua ledger.reopen_period_lock (SECURITY DEFINER — chạy bằng chủ bảng) và chỉ khi
-- có phiếu duyệt đang chạy đúng lệnh / chuỗi / người xin / pháp nhân, người duyệt nêu nằm trong phiếu. INSERT thẳng lùi mốc bằng quyền ứng dụng ⇒ lỗi.
CREATE FUNCTION ledger.guard_period_lock() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cur DATE; has BOOLEAN;
BEGIN
    SELECT true, l.locked_through INTO has, cur FROM ledger.period_locks l WHERE l.entity_id = NEW.entity_id ORDER BY l.id DESC LIMIT 1;
    IF NEW.locked_through IS NOT NULL AND (has IS NULL OR cur IS NULL OR NEW.locked_through > cur) THEN
        RETURN NEW;
    END IF;
    IF current_user <> (SELECT c.relowner::regrole::text FROM pg_class c WHERE c.oid = 'ledger.period_locks'::regclass)
       OR cardinality(NEW.approved_by) = 0 THEN
        RAISE EXCEPTION 'Lùi mốc khoá kỳ chỉ qua lệnh mở lại kỳ đã được duyệt';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER t_period_locks_guard BEFORE INSERT ON ledger.period_locks FOR EACH ROW EXECUTE FUNCTION ledger.guard_period_lock();

CREATE FUNCTION ledger.reopen_period_lock(p_entity UUID, p_through DATE, p_reason TEXT, p_by UUID, p_approved UUID[], p_command TEXT, p_chain TEXT)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $$
BEGIN
    IF core.tenant() IS NULL OR cardinality(p_approved) = 0 OR btrim(coalesce(p_reason, '')) = '' THEN
        RAISE EXCEPTION 'Mở lại kỳ cần phiên công ty, người duyệt và lý do';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM core.legal_entities e WHERE e.id = p_entity AND e.tenant_id = core.tenant()) THEN
        RAISE EXCEPTION 'Pháp nhân không thuộc công ty';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM core.approvals a WHERE a.tenant_id = core.tenant() AND a.status = 'pending' AND a.command = p_command
                     AND a.chain = p_chain AND a.requested_by = p_by AND a.input->>'entity_id' = p_entity::text
                     AND coalesce(a.input->>'locked_through', '') = coalesce(p_through::text, '')
                     AND p_approved[cardinality(p_approved)] = ANY(a.approver_ids) AND NOT (p_by = ANY(p_approved))) THEN
        RAISE EXCEPTION 'Không thấy phiếu duyệt mở lại kỳ đang chạy cho pháp nhân này';
    END IF;
    INSERT INTO ledger.period_locks (tenant_id, entity_id, locked_through, reason, created_by, approved_by)
    VALUES (core.tenant(), p_entity, p_through, btrim(p_reason), p_by, p_approved);
END $$;
REVOKE ALL ON FUNCTION ledger.reopen_period_lock(UUID, DATE, TEXT, UUID, UUID[], TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ledger.reopen_period_lock(UUID, DATE, TEXT, UUID, UUID[], TEXT, TEXT) TO barista_app;
