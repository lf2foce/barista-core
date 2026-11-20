-- module: acct
-- 0150 §8 (review an ninh 09/11): nghĩa vụ / lệnh chi / đề nghị chi MẬT theo nguồn. Nguồn ghi nghĩa vụ nêu tài nguyên quyền mà người xem phải có phạm
-- vi cả công ty mới thấy dòng (vd phiếu lương: "pay.slips") — giám đốc / quản trị có quyền đọc kế toán không vì thế mà thấy lương từng người. Dòng con
-- (phân bổ, cấn ứng, điều chỉnh) theo dòng cha (secure_child). Không nêu ⇒ '' ⇒ như trước. Thêm cột có mặc định hằng + chính sách hạn chế chỉ chặn dòng
-- có đánh dấu (chưa có dòng nào) ⇒ bản cũ chạy song song đúng ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
ALTER TABLE acct.payables ADD COLUMN confidential TEXT NOT NULL DEFAULT '' CHECK (confidential ~ '^([a-z_]+(\.[a-z_]+)?)?$');
ALTER TABLE acct.payouts ADD COLUMN confidential TEXT NOT NULL DEFAULT '' CHECK (confidential ~ '^([a-z_]+(\.[a-z_]+)?)?$');
ALTER TABLE acct.payment_requests ADD COLUMN confidential TEXT NOT NULL DEFAULT '' CHECK (confidential ~ '^([a-z_]+(\.[a-z_]+)?)?$');
CREATE POLICY p_confidential ON acct.payables AS RESTRICTIVE
 USING (confidential = '' OR owner_id = (SELECT core.me()) OR (SELECT core.scope(confidential)) = 'all')
 WITH CHECK (confidential = '' OR owner_id = (SELECT core.me()) OR (SELECT core.scope(confidential)) = 'all');
CREATE POLICY p_confidential ON acct.payouts AS RESTRICTIVE
 USING (confidential = '' OR user_id = (SELECT core.me()) OR (SELECT core.scope(confidential)) = 'all')
 WITH CHECK (confidential = '' OR user_id = (SELECT core.me()) OR (SELECT core.scope(confidential)) = 'all');
-- Dòng cấn ứng thấy theo KHOẢN ỨNG (không theo nghĩa vụ được cấn): khoản ứng không mật thì số còn ứng cộng đúng với người xem khoản ứng, kể cả khi
-- nghĩa vụ được cấn là lương mật (review 09/11). Với nguồn không đánh dấu mật, nghĩa vụ và khoản ứng cùng người cùng tài nguyên ⇒ thấy như trước.
ALTER POLICY p_scope ON acct.advance_offsets
 USING (tenant_id = (SELECT core.tenant()) AND EXISTS (SELECT 1 FROM acct.payables p WHERE p.id = advance_id))
 WITH CHECK (tenant_id = (SELECT core.tenant()) AND EXISTS (SELECT 1 FROM acct.payables p WHERE p.id = advance_id));
CREATE POLICY p_confidential ON acct.payment_requests AS RESTRICTIVE
 USING (confidential = '' OR (SELECT core.scope(confidential)) = 'all')
 WITH CHECK (confidential = '' OR (SELECT core.scope(confidential)) = 'all');
