-- module: core, talk — lý do: 0128 — cài đặt công ty là dữ liệu (bảng chung core.settings, như core.vocabulary của 0124) + tab "Tệp" / phòng đội của Trao đổi (talk.links, talk.rooms.team_id). Hai phần độc lập, cùng một đợt.
-- CHỈ THÊM (bảng mới, cột cho phép NULL, nới một ràng buộc): bản cũ chạy song song lúc chuyển vẫn đọc / ghi đúng ⇒ không cần cửa sổ bảo trì
-- (không vào deployment.json). Tin gửi bằng bản cũ trong lúc chuyển không có dòng talk.links (tab "Link" thiếu đúng các tin đó) — chấp nhận.

-- ── Cài đặt công ty (0128 §3, §5): khoá do module khai bằng Module.setting (code), giá trị bật / tắt là DỮ LIỆU của công ty, quản trị đổi trong app.
-- Chưa có dòng ⇒ mặc định gói khai. Mặc định của gói cài qua nhật ký cài như mọi mục mặc định khác (công ty đã đổi thì bật lại gói không ghi đè).
CREATE TABLE core.settings (
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    key        TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'),
    enabled    BOOLEAN NOT NULL,
    updated_by UUID REFERENCES core.users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);
CALL core.secure_tenant('core.settings');
GRANT SELECT, INSERT, UPDATE, DELETE ON core.settings TO barista_app;
COMMENT ON TABLE core.settings IS 'Cài đặt bật / tắt của công ty (0128): khoá khai trong code, giá trị là dữ liệu';

ALTER TABLE core.pack_installs DROP CONSTRAINT pack_installs_kind_check;
ALTER TABLE core.pack_installs ADD CONSTRAINT pack_installs_kind_check
    CHECK (kind = ANY (ARRAY['role', 'grant', 'team', 'chain', 'rule', 'flow', 'vocab', 'setting']));

-- ── Link trong tin (0128 §1): code rút đường dẫn lúc gửi / sửa tin; không tải trang đích (tránh SSRF). Bảng con của tin ⇒ cùng quyền luồng,
-- tin thu hồi thì link ẩn như tệp (talk.may_see_message). Sửa tin ⇒ thay bộ link của tin đó.
CREATE TABLE talk.links (
    id         UUID DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id  UUID DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    message_id BIGINT NOT NULL REFERENCES talk.messages(id) ON DELETE CASCADE,
    url        TEXT NOT NULL CHECK (url ~ '^https?://' AND length(url) <= 2000),
    host       TEXT NOT NULL DEFAULT '' CHECK (length(host) <= 255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (message_id, url)
);
CALL core.secure_child('talk.links', 'talk.messages', 'message_id');
CREATE POLICY p_recalled ON talk.links AS RESTRICTIVE FOR SELECT USING (talk.may_see_message(message_id));
GRANT SELECT, INSERT, DELETE ON talk.links TO barista_app;

-- Tin đã có: rút link một lần theo đúng luật của code (talk.LINK_RE: http(s):// tới khoảng trắng / dấu nháy / ngoặc nhọn, bỏ dấu câu cuối;
-- link dài quá 2000 ký tự bỏ qua, tên miền cắt 255 ký tự — như talk.links()). Không dòng nào làm hỏng cả migration.
INSERT INTO talk.links (tenant_id, message_id, url, host)
SELECT DISTINCT ON (x.id, x.url) x.tenant_id, x.id, x.url, left(lower(coalesce(substring(x.url FROM '^https?://(?:[^@/?#]*@)?([^/:?#]+)'), '')), 255)
FROM (SELECT msg.tenant_id, msg.id, rtrim(mt[1], '.,;:!?)]}') AS url
      FROM talk.messages msg CROSS JOIN LATERAL regexp_matches(msg.body, '(https?://[^\s<>"''`]+)', 'g') AS mt
      WHERE msg.recalled_at IS NULL) x
WHERE length(x.url) <= 2000 AND x.url ~ '^https?://.'
ON CONFLICT DO NOTHING;

-- ── Phòng của đội (0128 §5): phòng có thể gắn MỘT đội; công ty bật talk.team_rooms ⇒ tạo đội là có phòng. Giải thể / xoá đội ⇒ phòng còn, bỏ gắn.
ALTER TABLE talk.rooms ADD COLUMN team_id UUID REFERENCES core.teams(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX rooms_team_key ON talk.rooms (tenant_id, team_id) WHERE team_id IS NOT NULL;

-- Tìm tệp theo tên (0128 §2) gấp chữ bằng talk.fold NGAY TRONG truy vấn, chỉ trên các luồng người tìm đọc được (lọc RLS trước) — chưa cần chỉ mục.
-- Khi số tệp lớn: thêm cột sinh / chỉ mục biểu thức trên talk.fold(filename) ở một migration riêng.
