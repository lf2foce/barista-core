-- module: core
-- 086 (0205 §5c): ĐĂNG NHẬP BẰNG MÃ QUA EMAIL — cho người không dùng Google (và là khuôn cho cách đăng nhập khác sau này). Mã 6 số gửi tới email ĐÃ
-- ĐƯỢC MỜI; lưu BĂM, dùng một lần, hết hạn 10 phút, tối đa 5 lần nhập sai mỗi mã. ``client_id`` trống = đăng nhập Barista; có = ứng dụng first-party
-- (server ứng dụng xin mã thay người dùng; ``nonce_hash`` gắn mã với đúng trình duyệt đã xin). Chỉ kết nối owner đọc / ghi. Chỉ THÊM.

CREATE TABLE core.login_codes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT NOT NULL CHECK (email = lower(btrim(email)) AND email <> ''),
    code_hash   TEXT NOT NULL,
    client_id   TEXT REFERENCES core.mcp_clients(client_id) ON DELETE CASCADE,
    nonce_hash  TEXT,
    attempts    INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ
);
CREATE INDEX idx_login_codes_email ON core.login_codes (email, created_at DESC);
ALTER TABLE core.login_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.login_codes FORCE ROW LEVEL SECURITY;
CREATE POLICY p_login_codes ON core.login_codes USING (false);   -- chỉ kết nối owner (đường đăng nhập) đọc / ghi
COMMENT ON TABLE core.login_codes IS 'Mã đăng nhập gửi qua email (0205 §5c): băm, một lần, 10 phút, tối đa 5 lần sai';
