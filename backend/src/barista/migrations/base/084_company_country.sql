-- module: core
-- 0199: QUỐC GIA CÔNG TY (mã ISO 3166-1 alpha-2). SĐT lưu E.164 ở mọi nơi; HIỂN THỊ theo quốc gia công ty: số cùng nước ⇒ dạng nội địa (Việt Nam:
-- ``0912 345 678``), số nước khác ⇒ ``+<mã nước>…``. Mặc định VN (mọi công ty đang có hiện như cũ). Cột có mặc định ⇒ không cần cửa sổ bảo trì.
ALTER TABLE core.tenants ADD COLUMN country text NOT NULL DEFAULT 'VN' CONSTRAINT tenants_country_check CHECK (country ~ '^[A-Z]{2}$');
COMMENT ON COLUMN core.tenants.country IS 'Quốc gia công ty (0199, ISO 3166-1 alpha-2): cách HIỂN THỊ SĐT (lưu luôn E.164)';
-- quản trị công ty đổi quốc gia của CHÍNH công ty mình (RLS p_tenant: id = core.tenant()); chỉ cột này
GRANT UPDATE (country) ON core.tenants TO barista_app;
