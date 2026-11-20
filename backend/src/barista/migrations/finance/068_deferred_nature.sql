-- module: acct — lý do: 0147 lát A — bản chất khoản phải thu thứ tư `deferred` (doanh thu nhận trước): lập khoản Nợ phải thu / Có `deferred:<pháp nhân>`, doanh thu
-- chỉ ghi nhận khi dịch vụ được cung cấp (lát B của 0147, chưa làm). Chỉ NỚI ràng buộc `nature` (thêm một giá trị) + thêm ràng buộc hình dạng cho giá trị mới:
-- mọi dòng cũ vẫn hợp lệ, bản cũ chạy song song không ghi `deferred` ⇒ không bảo trì. Khoản `deferred` không mang phí, người nhận, tài khoản doanh thu.
SET LOCAL lock_timeout = '10s';
ALTER TABLE acct.receivables DROP CONSTRAINT receivables_nature_check;
ALTER TABLE acct.receivables ADD CONSTRAINT receivables_nature_check CHECK (nature IN ('revenue', 'pass_through', 'deposit', 'deferred'));
ALTER TABLE acct.receivables ADD CONSTRAINT receivables_deferred_shape
    CHECK (nature <> 'deferred' OR (fee_vnd = 0 AND revenue_account = '' AND payee_key = '' AND category = ''));
