-- module: pay
-- 0150 lát 3 "Luật VN" + nối lát 4 (chấm công): hai máy tính mới — `prorata` (mức tháng × ngày công có lương / ngày công chuẩn, từ bảng công đã chốt)
-- và `overtime` (giờ làm thêm × đơn giá giờ × hệ số luật); dòng phiếu loại `employer` (phần doanh nghiệp đóng: bảo hiểm, kinh phí công đoàn — chi phí
-- cộng nợ phải trả, KHÔNG trừ vào phiếu); số liệu luật của phiếu (`figures`: thu nhập chịu thuế, thuế, bảo hiểm đã khấu trừ) để kỳ phụ cùng tháng chi
-- tính thuế LUỸ KẾ. Chỉ nới ràng buộc + thêm cột có mặc định ⇒ bản cũ còn chạy lúc chuyển; không quét bảng.
DO $$
DECLARE c record;
BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid = 'pay.components'::regclass AND contype = 'c'
            AND pg_get_constraintdef(oid) LIKE '%calculator%' AND pg_get_constraintdef(oid) NOT LIKE '%advance_offset%source%' LOOP
  EXECUTE format('ALTER TABLE pay.components DROP CONSTRAINT %I', c.conname);
 END LOOP;
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid = 'pay.slip_lines'::regclass AND contype = 'c'
            AND pg_get_constraintdef(oid) LIKE '%withholding%' LOOP
  EXECUTE format('ALTER TABLE pay.slip_lines DROP CONSTRAINT %I', c.conname);
 END LOOP;
END $$;
ALTER TABLE pay.components
 ADD CONSTRAINT components_calculator_known CHECK (calculator IN ('fixed', 'prorata', 'rate_x_qty', 'overtime', 'from_source', 'manual', 'advance_offset')),
 ADD CONSTRAINT components_earning_calculators CHECK (calculator NOT IN ('fixed', 'prorata', 'rate_x_qty', 'overtime', 'from_source') OR kind = 'earning'),
 ADD CONSTRAINT components_offset_has_no_account CHECK (calculator = 'advance_offset' OR expense_account <> '');
ALTER TABLE pay.slip_lines ADD CONSTRAINT slip_lines_kind_known CHECK (kind IN ('earning', 'deduction', 'withholding', 'offset', 'employer'));
ALTER TABLE pay.slips ADD COLUMN figures JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(figures) = 'object');
CREATE INDEX slips_party_pay_month ON pay.slips (tenant_id, party_id, pay_date) WHERE payable_id IS NOT NULL;
