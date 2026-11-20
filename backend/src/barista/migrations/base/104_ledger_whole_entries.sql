-- module: ledger
-- 104 (rà Codex 20/11/2026 lõi tiền) — BÚT TOÁN LÀ MỘT KHỐI, và mỗi bút toán gốc chỉ đảo MỘT lần. Bất biến giữ ở DB bằng trigger, KHÔNG bằng
-- quyền đặc biệt cho việc GHI: vai ứng dụng giữ quyền ghi và RLS (công ty, quyền sổ cái của phiên) áp như mọi bảng — không ghi qua hàm SECURITY DEFINER
-- (role migration có BYPASSRLS, ghi bằng nó sẽ bỏ qua RLS — bản đầu của 104 mắc đúng lỗi này, rà Codex 20/11). Trigger KIỂM thì SECURITY DEFINER (dưới).
--  * Dòng chỉ vào bút toán ghi CÙNG giao dịch: mỗi bút toán đóng dấu giao dịch đã ghi nó (``written_in``, trigger điền, không tự đặt được); dòng thêm
--    vào bút toán của giao dịch khác (đã commit) ⇒ từ chối — trước đây vai ứng dụng thêm được một cặp Nợ / Có cân vào bút toán đã ghi (2 → 4 dòng).
--  * Mỗi bút toán có ≥ 2 dòng lúc commit (trigger hoãn) — trước đây commit được đầu bút toán 0 dòng. Cân Nợ / Có (t_balanced) và chỉ thêm
--    (t_*_immutable) giữ nguyên từ 000_baseline.
--  * Đảo một lần: chỉ mục duy nhất trên ``reverses`` — kể cả hai yêu cầu đảo đồng thời với hai khoá khác nhau. Huỷ một lần đảo = bút toán mới có tham
--    chiếu (đảo của bút toán đảo vẫn được — ``reverses`` trỏ bút toán khác).
-- ``ledger.write_journal`` (hàm thường, quyền người gọi) ghi đầu + dòng trong một lời gọi — ``kernel/ledger.py`` (``post``) dùng nó.
-- Kiểm 20/11 trên mọi DB của 4 bản cài: 0 bút toán bị đảo hai lần. Bút toán cũ có ``written_in`` trống ⇒ không nhận thêm dòng. Bản app cũ (đang chạy
-- lúc chuyển) ghi đầu + dòng trong cùng giao dịch, ≥ 2 dòng ⇒ vẫn ghi được ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

CREATE UNIQUE INDEX ux_journals_reverse_once ON ledger.journals (tenant_id, reverses) WHERE reverses IS NOT NULL;

ALTER TABLE ledger.journals ADD COLUMN written_in xid8;

CREATE FUNCTION ledger.stamp_written_in() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    NEW.written_in := pg_current_xact_id();   -- giao dịch gốc (kể cả khi chèn trong điểm lưu) — bên chèn không tự đặt
    RETURN NEW;
END $$;
CREATE TRIGGER t_journals_written_in BEFORE INSERT ON ledger.journals FOR EACH ROW EXECUTE FUNCTION ledger.stamp_written_in();

-- Hai hàm KIỂM dưới đây chạy SECURITY DEFINER như mọi trigger kiểm của sổ cái (check_balanced, check_period_lock): bất biến phải tính trên MỌI dòng,
-- không theo người đang gọi — gói ghi tiền bằng quyền hệ thống rồi commit dưới phiên của người dùng không thấy dòng sổ cái (RLS ⇒ đếm ra 0).
-- Chúng chỉ đếm và từ chối, không trả dữ liệu; còn GHI vẫn là quyền người gọi (``write_journal`` là hàm thường).
CREATE FUNCTION ledger.check_same_entry() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM ledger.journals j WHERE j.id = NEW.journal_id AND j.written_in = pg_current_xact_id()) THEN
        RAISE EXCEPTION 'Bút toán đã ghi không nhận thêm dòng — sửa bằng bút toán đảo';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER t_postings_same_entry BEFORE INSERT ON ledger.postings FOR EACH ROW EXECUTE FUNCTION ledger.check_same_entry();

CREATE FUNCTION ledger.check_whole_entry() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog' AS $$
BEGIN
    IF (SELECT count(*) FROM ledger.postings p WHERE p.journal_id = NEW.id) < 2 THEN
        RAISE EXCEPTION 'Bút toán “%” cần ít nhất hai dòng', NEW.memo;
    END IF;
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER t_journals_whole AFTER INSERT ON ledger.journals DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger.check_whole_entry();

CREATE FUNCTION ledger.write_journal(p_key text, p_memo text, p_source text, p_ref text, p_reverses uuid, p_entity uuid, p_on date,
                                     p_lines jsonb) RETURNS uuid
    LANGUAGE plpgsql AS $$
DECLARE jid uuid; n int; total bigint; zero int;
BEGIN
    SELECT count(*), coalesce(sum((l->>'amount')::bigint), 0), count(*) FILTER (WHERE (l->>'amount')::bigint = 0)
      INTO n, total, zero FROM jsonb_array_elements(p_lines) l;
    IF n < 2 OR total <> 0 OR zero > 0 THEN
        RAISE EXCEPTION 'Bút toán “%” cần ít nhất hai dòng khác 0 và cân Nợ / Có', p_memo;
    END IF;
    INSERT INTO ledger.journals (memo, source, source_ref, idempotency_key, reverses, created_by, entity_id, posted_on)
    VALUES (p_memo, p_source, p_ref, p_key, p_reverses, core.me(), p_entity, p_on)   -- pháp nhân trống ⇒ trigger điền mặc định; rồi trigger khoá kỳ
    ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id INTO jid;
    IF jid IS NULL THEN
        RETURN NULL;   -- khoá đã có (yêu cầu song song vừa ghi) ⇒ bên gọi so nội dung với bút toán đã ghi
    END IF;
    INSERT INTO ledger.postings (journal_id, account_id, amount_vnd, memo)
    SELECT jid, (l->>'account')::uuid, (l->>'amount')::bigint, p_memo FROM jsonb_array_elements(p_lines) l;
    RETURN jid;
END $$;
GRANT EXECUTE ON FUNCTION ledger.write_journal(text, text, text, text, uuid, uuid, date, jsonb) TO barista_app;
