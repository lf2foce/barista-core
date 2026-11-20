-- module: acct — lý do: 0156 (sửa sau review) — rào CUỐI ở DB: tổng đã đảo của MỘT phiếu thu không vượt số của phiếu, kể cả khi một đường ghi quên kiểm / hai
-- lệnh đảo chạy đồng thời (khoá theo phiếu). Chỉ thêm hàm + trigger trên bảng mới của 066 ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
CREATE FUNCTION acct.payment_reversal_cap() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE paid BIGINT; done BIGINT;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('acct.payment_reversal:' || NEW.payment_id::text, 0));
  SELECT amount_vnd INTO paid FROM acct.payments WHERE id = NEW.payment_id;
  SELECT coalesce(sum(amount_vnd), 0) INTO done FROM acct.payment_reversals WHERE payment_id = NEW.payment_id;
  IF paid IS NULL OR done + NEW.amount_vnd > paid THEN
    RAISE EXCEPTION 'Đảo vượt số của phiếu thu' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER payment_reversal_cap BEFORE INSERT ON acct.payment_reversals FOR EACH ROW EXECUTE FUNCTION acct.payment_reversal_cap();
