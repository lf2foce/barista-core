-- module: core — lý do: 0128 — cài đặt công ty là dữ liệu (bảng chung core.settings, như core.vocabulary của 0124). Tách từ base/040_chat_files_team_spaces.sql (0130, ghi chú 08/11: bảng của gói về thư mục gói; phần Trao đổi ở talk/040_links_team_rooms.sql).
-- CHỈ THÊM (bảng mới, nới một ràng buộc): bản cũ chạy song song lúc chuyển vẫn đọc / ghi đúng ⇒ không cần cửa sổ bảo trì.

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
