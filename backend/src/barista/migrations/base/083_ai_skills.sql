-- module: core
-- 083 (ADR 0198, barista-docs): KỸ NĂNG CỦA TRỢ LÝ là DỮ LIỆU công ty — cách làm một việc hay gặp (khi dùng, vai thấy, hướng dẫn markdown có trần
-- dung lượng, công cụ cần), có phiên bản và vòng duyệt: nháp → gửi duyệt → người KHÁC người soạn duyệt (bất biến 6) → đang dùng → ngừng. Gói mang bộ
-- mặc định (``Pack(records=…)`` qua ``barista.api.skill``) cài bằng ``admin.install_defaults`` (chỉ thêm); công ty thêm / sửa kỹ năng của mình.
-- Mỗi lượt trợ lý chỉ thấy DANH SÁCH NGẮN (tên + khi dùng) của kỹ năng đang dùng cho vai người hỏi; hướng dẫn chỉ nạp khi model gọi ``core.use_skill``.
-- Kỹ năng không cấp quyền: công cụ khai trong kỹ năng vẫn lọc theo quyền người hỏi. ``core.ai_usage.skills`` ghi khoá@phiên bản kỹ năng đã dùng mỗi lượt.
-- CHỈ THÊM (bảng mới rỗng + một cột cho phép trống) ⇒ bản cũ chạy song song không đọc / ghi gì ở đây, không cần dừng dịch vụ.

SET LOCAL lock_timeout = '10s';

CREATE TABLE core.ai_skills (
    tenant_id     UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    key           TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{1,59}$'),
    version       INT NOT NULL CHECK (version > 0),
    title         TEXT NOT NULL CHECK (btrim(title) <> '' AND length(title) <= 80),
    when_to_use   TEXT NOT NULL CHECK (btrim(when_to_use) <> '' AND length(when_to_use) <= 240),
    roles         TEXT[] NOT NULL CHECK (cardinality(roles) > 0),       -- vai thấy kỹ năng (khoá core.roles); ít nhất một
    instructions  TEXT NOT NULL CHECK (btrim(instructions) <> '' AND length(instructions) <= 6000),   -- trần cứng; trần dùng thật ở code (ai_skills.MAX_CHARS)
    tools         TEXT[] NOT NULL DEFAULT '{}',                           -- tên thao tác kỹ năng cần (nhu cầu khai báo, KHÔNG phải quyền)
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'retired')),
    source        TEXT NOT NULL DEFAULT 'company' CHECK (source IN ('pack', 'company')),
    pack          TEXT,                                                   -- gói mang bản mặc định (source = pack)
    author_id     UUID REFERENCES core.users(id),                         -- người soạn / sửa nội dung lần cuối; NULL = bản của gói (review trong repo)
    submitted_at  TIMESTAMPTZ,                                            -- nháp đã gửi duyệt (sửa lại nháp ⇒ xoá, gửi lại)
    approved_by   UUID REFERENCES core.users(id),
    approved_at   TIMESTAMPTZ,
    retired_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key, version),
    CHECK (status <> 'active' OR source = 'pack' OR (approved_by IS NOT NULL AND approved_by IS DISTINCT FROM author_id))   -- người soạn không tự duyệt
);
-- mỗi khoá: tối đa MỘT bản đang dùng và MỘT bản nháp (nháp sửa tại chỗ; bản đang dùng vẫn chạy trong lúc nháp chờ duyệt)
CREATE UNIQUE INDEX ai_skills_one_active ON core.ai_skills (tenant_id, key) WHERE status = 'active';
CREATE UNIQUE INDEX ai_skills_one_draft ON core.ai_skills (tenant_id, key) WHERE status = 'draft';
-- cách ly theo công ty; ai được xem / sửa / duyệt do quyền tài nguyên ``core.ai_skills`` ở executor (bảng quyền là dữ liệu). Trợ lý đọc bản ĐANG DÙNG
-- cho vai của người hỏi (agent/chat.py) — người hỏi không cần quyền quản lý kỹ năng để được trợ lý dùng kỹ năng
CALL core.secure_tenant('core.ai_skills');
GRANT SELECT, INSERT, UPDATE ON core.ai_skills TO barista_app;

-- kỹ năng đã dùng trong lượt (``khoá@phiên bản``) — để thống kê / truy nguyên; NULL = lượt không dùng kỹ năng (hoặc bản cũ ghi)
ALTER TABLE core.ai_usage ADD COLUMN IF NOT EXISTS skills TEXT[];
