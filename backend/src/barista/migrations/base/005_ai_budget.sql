-- module: core
-- 005 (0092): HẠN MỨC CHI PHÍ AI THEO CÔNG TY — trần USD / tháng do NHÀ CUNG CẤP đặt (entitle.py --ai-budget; bên trả tiền model), cảnh báo ở
-- ngưỡng % (mặc định 80). Chi trong tháng đếm ở ``core.ai_spend`` (cấp CÔNG TY, cộng dồn cùng giao dịch ghi lượt) — không cộng ``core.ai_usage``
-- bằng quyền người dùng (RLS theo dòng ⇒ nhân viên chỉ thấy lượt của mình, sẽ không bao giờ chạm trần). Vượt trần ⇒ ``llm.model_for`` từ chối
-- như "công ty tắt AI" (mọi lối đã có đường xử lý). Chỉ THÊM.

ALTER TABLE core.tenant_profile
    ADD COLUMN ai_monthly_usd NUMERIC(10, 2) CHECK (ai_monthly_usd > 0),          -- NULL = không trần
    ADD COLUMN ai_warn_pct INT NOT NULL DEFAULT 80 CHECK (ai_warn_pct BETWEEN 1 AND 100);

CREATE TABLE core.ai_spend (
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    month      DATE NOT NULL CHECK (month = date_trunc('month', month)::date),   -- ngày 1 của tháng (giờ Việt Nam)
    usd        NUMERIC(14, 6) NOT NULL DEFAULT 0 CHECK (usd >= 0),
    warned_at  TIMESTAMPTZ,                                                         -- đã báo quản trị chạm ngưỡng (một lần / tháng)
    stopped_at TIMESTAMPTZ,                                                         -- đã báo hết hạn mức (một lần / tháng)
    PRIMARY KEY (tenant_id, month)
);
CALL core.secure_tenant('core.ai_spend');
GRANT SELECT, INSERT, UPDATE ON core.ai_spend TO barista_app;

-- tháng hiện tại: cộng từ nhật ký sẵn có (lượt chưa có giá không tính — như màn Chi phí trợ lý AI)
INSERT INTO core.ai_spend (tenant_id, month, usd)
SELECT tenant_id, date_trunc('month', at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date, sum(cost_usd)
FROM core.ai_usage
WHERE cost_usd IS NOT NULL AND at >= date_trunc('month', now() AT TIME ZONE 'Asia/Ho_Chi_Minh') AT TIME ZONE 'Asia/Ho_Chi_Minh'
GROUP BY 1, 2;
