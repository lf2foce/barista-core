-- module: acct, ledger — lý do: số lúc lập của khoản phải thu lấy từ bút toán LẬP khoản trong sổ cái (chỉ đọc)
-- 077 (rà Codex 20/11/2026 lõi tiền) — SỐ LÚC LẬP của khoản phải thu: ``issued_amount_vnd`` / ``issued_fee_vnd`` ghi một lần lúc lập, không bao giờ
-- đổi. Gọi lại ``acct.issue_receivable`` cùng (nguồn, tham chiếu) so với số lúc lập, không với số hiện tại — số hiện tại đã phản ánh điều chỉnh
-- (giấy báo có giảm, hoá đơn GTGT giá chưa thuế cộng thuế, huỷ hoá đơn trừ lại).
-- Điền cho dữ liệu cũ TỪ NGUỒN, không suy từ trạng thái (hai lần suy từ trạng thái đã sai: huỷ sau giảm một phần, cộng VAT):
--  1. có bút toán LẬP — khoá ở ``recognized_key``, ghi một lần lúc lập: ``acct.ar:<id>`` (khoản doanh thu / nhận trước) hoặc khoá từng dòng của lô
--     đầu kỳ ``acct.opening_batch:<lô>:<dòng>`` ⇒ số Nợ phải thu trên bút toán đó;
--  2. không có (thu hộ, cọc) ⇒ số hiện tại trừ thuế của hoá đơn giá chưa thuế còn hiệu lực; khoản đã giảm mà không có bút toán
--     lập ⇒ không suy được ⇒ DỪNG;
--  phí: khoản doanh thu = số tiền (ràng buộc bảng); khác ⇒ phí hiện tại trừ thuế đã cộng (hoá đơn cộng thuế vào cả phí).
-- Đối chiếu chéo: khoản có bút toán lập, chưa giảm, mà số hiện tại trừ thuế ≠ bút toán lập ⇒ DỪNG, liệt kê mã khoản. Kiểm 20/11 trên DB của 4 bản cài:
-- 9.484 / 9.484 khoản có bút toán lập, 0 lệch, 0 khoản phải dừng. Trigger điền khi chèn (bản app cũ không biết cột vẫn chèn được), chặn sửa ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

ALTER TABLE acct.receivables ADD COLUMN issued_amount_vnd bigint, ADD COLUMN issued_fee_vnd bigint;

CREATE TEMP TABLE receivable_sources ON COMMIT DROP AS
SELECT r.id, r.tenant_id, r.code, r.nature, r.amount_vnd, r.fee_vnd, r.credited_vnd,
       (SELECT p.amount_vnd FROM ledger.journals j JOIN ledger.postings p ON p.journal_id = j.id JOIN ledger.accounts a ON a.id = p.account_id
         WHERE j.tenant_id = r.tenant_id AND j.idempotency_key = r.recognized_key AND r.recognized_key <> '' AND a.code = r.receivable_account
           AND p.amount_vnd > 0) AS journal_vnd,
       (SELECT coalesce(sum(v.tax_vnd), 0) FROM acct.vat_invoices v
         WHERE v.receivable_id = r.id AND v.kind = 'invoice' AND v.price_mode = 'exclusive' AND v.cancelled_on IS NULL)::bigint AS vat_added
  FROM acct.receivables r;

DO $$
DECLARE bad text;
BEGIN
    SELECT string_agg(code, ', ' ORDER BY code) INTO bad FROM (
        SELECT code FROM receivable_sources
         WHERE (journal_vnd IS NULL AND credited_vnd > 0)                                          -- đã giảm, không có nguồn để suy
            OR (journal_vnd IS NOT NULL AND credited_vnd = 0 AND amount_vnd - vat_added <> journal_vnd)   -- hai nguồn không khớp
            OR (nature <> 'revenue' AND credited_vnd > 0)                                          -- giấy báo có chỉ áp cho doanh thu
         LIMIT 50) x;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION '077: không xác định chắc số lúc lập của khoản phải thu: % — đối chiếu tay trước khi nâng cấp', bad;
    END IF;
END $$;

UPDATE acct.receivables r
   SET issued_amount_vnd = coalesce(s.journal_vnd, s.amount_vnd - s.vat_added),
       issued_fee_vnd = CASE WHEN s.nature = 'revenue' THEN coalesce(s.journal_vnd, s.amount_vnd - s.vat_added) ELSE s.fee_vnd - s.vat_added END
  FROM receivable_sources s WHERE s.id = r.id;
ALTER TABLE acct.receivables ALTER COLUMN issued_amount_vnd SET NOT NULL, ALTER COLUMN issued_fee_vnd SET NOT NULL,
    ADD CONSTRAINT receivables_issued_check CHECK (issued_amount_vnd > 0 AND issued_fee_vnd BETWEEN 0 AND issued_amount_vnd);

CREATE FUNCTION acct.receivable_issued_facts() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.issued_amount_vnd := NEW.amount_vnd;   -- số lúc lập = đúng số được chèn, bên chèn không tự đặt khác
        NEW.issued_fee_vnd := NEW.fee_vnd;
    ELSIF NEW.issued_amount_vnd IS DISTINCT FROM OLD.issued_amount_vnd OR NEW.issued_fee_vnd IS DISTINCT FROM OLD.issued_fee_vnd THEN
        RAISE EXCEPTION 'Số lúc lập của khoản phải thu không sửa được — điều chỉnh bằng giấy báo có';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER t_receivable_issued_facts BEFORE INSERT OR UPDATE ON acct.receivables FOR EACH ROW EXECUTE FUNCTION acct.receivable_issued_facts();
