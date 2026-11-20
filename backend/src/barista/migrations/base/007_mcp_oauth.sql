-- module: core
-- 007 (0093): MCP CHỈ ĐỌC qua OAuth — Barista tự làm máy chủ OAuth tối giản (thư viện mcp 2.x lo PKCE / metadata / token endpoint): ứng dụng AI
-- (Claude, ChatGPT…) đăng ký client, người dùng ĐỒNG Ý trên web Barista (phiên đăng nhập sẵn có, công ty đang chọn) ⇒ mã ⇒ token gắn đúng người +
-- công ty. Token lưu BĂM như core.sessions; người dùng thấy / thu hồi kết nối của chính mình. Chỉ THÊM.

CREATE TABLE core.mcp_clients (
    client_id  TEXT PRIMARY KEY CHECK (btrim(client_id) <> ''),
    info       JSONB NOT NULL,                       -- OAuthClientInformationFull (tên, redirect_uris…) — client công khai + PKCE
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE core.mcp_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.mcp_clients FORCE ROW LEVEL SECURITY;
CREATE POLICY p_mcp_clients ON core.mcp_clients USING (false);   -- chỉ kết nối owner (máy chủ OAuth) đọc / ghi

-- Yêu cầu cấp quyền (chờ người đồng ý) · mã cấp quyền · token truy cập · token làm mới — một bảng, phân bằng ``kind``.
CREATE TABLE core.mcp_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind         TEXT NOT NULL CHECK (kind IN ('request', 'code', 'access', 'refresh')),
    token_hash   TEXT NOT NULL UNIQUE,
    client_id    TEXT NOT NULL REFERENCES core.mcp_clients(client_id) ON DELETE CASCADE,
    user_id      UUID REFERENCES core.users(id) ON DELETE CASCADE,       -- NULL khi còn là yêu cầu chờ đồng ý
    tenant_id    UUID REFERENCES core.tenants(id) ON DELETE CASCADE,
    scopes       TEXT[] NOT NULL DEFAULT '{}',
    params       JSONB NOT NULL DEFAULT '{}',        -- yêu cầu / mã: code_challenge, redirect_uri, state, resource
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (kind = 'request' OR (user_id IS NOT NULL AND tenant_id IS NOT NULL))
);
CREATE INDEX idx_mcp_tokens_user ON core.mcp_tokens (user_id) WHERE revoked_at IS NULL;
ALTER TABLE core.mcp_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.mcp_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY p_mcp_tokens ON core.mcp_tokens USING (user_id = core.me());   -- mỗi người thấy / thu hồi kết nối của chính mình
GRANT SELECT, UPDATE (revoked_at) ON core.mcp_tokens TO barista_app;
