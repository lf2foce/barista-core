-- module: core
-- 082 (ADR 0193, quyết định chủ sản phẩm 12/11/2026 chiều): CHÍNH SÁCH TRỢ LÝ AI là của NHÀ VẬN HÀNH, không phải của khách.
--  * Hạn mức theo người tính bằng TOKEN (mọi token trừ token ĐỌC cache: input − cache_read + output của lượt — agent/usage.quota_tokens), không
--    bằng tiền: cột mới
--    ``core.ai_tiers.person_monthly_tokens`` (NULL = không giới hạn) + bộ đếm ``core.ai_person_spend.tokens`` cộng cùng giao dịch ghi lượt.
--    ``person_monthly_usd`` của 081 thôi dùng (bản mới không đọc / ghi NULL) — giữ cột vì migration chỉ thêm.
--  * Tài nguyên ``core.ai_policy`` (hạng, model, hạn mức, báo cáo token) KHÔNG cấp được cho vai nào bằng quyền của khách: trigger từ chối mọi dòng
--    ``core.grants`` của tài nguyên này khi phiên app (không phải chủ bảng) không chạy bằng quyền hệ thống (quản trị công ty tự cấp cho mình ⇒ bị chặn ở DB).
-- CHỈ THÊM (cột cho phép trống / có mặc định, trigger trên dòng MỚI của một tài nguyên chưa ai được cấp) ⇒ bản cũ chạy song song không vướng,
-- không cần dừng dịch vụ. Bộ đếm token bắt đầu từ lúc chạy migration (tháng đang chạy không đếm bù lượt trước đó).

SET LOCAL lock_timeout = '10s';

ALTER TABLE core.ai_tiers ADD COLUMN person_monthly_tokens BIGINT CHECK (person_monthly_tokens > 0);
COMMENT ON COLUMN core.ai_tiers.person_monthly_usd IS 'Thôi dùng từ 082 (ADR 0193): hạn mức theo người tính bằng token — person_monthly_tokens';
COMMENT ON COLUMN core.ai_tiers.person_monthly_tokens IS 'Hạn mức token (input − cache đọc + output) của MỖI NGƯỜI trong hạng / tháng; NULL = không giới hạn. Nhà vận hành đặt';

ALTER TABLE core.ai_person_spend ADD COLUMN tokens BIGINT NOT NULL DEFAULT 0 CHECK (tokens >= 0);
COMMENT ON COLUMN core.ai_person_spend.stopped_at IS 'Lúc người này chạm hạn mức token của tháng (đã báo nhà vận hành — một lần / người / tháng)';

CREATE FUNCTION core.grants_operator_only() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- phiên app (không phải chủ bảng — migration / lệnh vận hành chạy bằng kết nối chủ) không chạy bằng quyền hệ thống ⇒ từ chối
    IF NEW.resource = 'core.ai_policy' AND NOT core.is_system()
       AND current_user <> (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'core.grants'::regclass) THEN
        RAISE EXCEPTION 'core.ai_policy chỉ nhà vận hành cấp (ADR 0193)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER grants_operator_only BEFORE INSERT OR UPDATE ON core.grants FOR EACH ROW EXECUTE FUNCTION core.grants_operator_only();
