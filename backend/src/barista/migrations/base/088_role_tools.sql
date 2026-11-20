-- module: core
-- 088 (ADR 0218, barista-docs): BỘ CÔNG CỤ TRỢ LÝ THEO VAI là DỮ LIỆU công ty — vai mang sẵn nhóm (khoá module) / công cụ lẻ (tên thao tác) nào
-- mỗi lượt. Một dòng / vai; vai không có dòng ⇒ trợ lý tự tuyển như trước (0095). Bộ không cấp quyền: công cụ thật = bộ ∩ quyền người hỏi ∩ chế độ
-- trợ lý (agent/toolset.py). Gói mang mặc định (Role.tools / Pack.tools) cài qua admin.install_defaults; quản trị sửa ở Quản trị › Công cụ trợ lý theo vai.
-- CHỈ THÊM (bảng mới rỗng) ⇒ bản cũ chạy song song không đọc / ghi gì ở đây, không cần dừng dịch vụ.

SET LOCAL lock_timeout = '10s';

CREATE TABLE core.role_tools (
    tenant_id   UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,                                   -- khoá core.roles
    tools       TEXT[] NOT NULL DEFAULT '{}' CHECK (cardinality(tools) <= 200),   -- khoá module (cả nhóm) hoặc tên thao tác; trần dùng thật ở code (ai_tools.MAX_ITEMS)
    updated_by  UUID REFERENCES core.users(id),                  -- NULL = mặc định của gói (admin.install_defaults)
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, role)
);
-- cách ly theo công ty; ai được sửa do quyền tài nguyên ``core.ai_tools`` ở executor. Trợ lý đọc dòng của vai người hỏi bằng giao dịch của họ
-- (người hỏi không cần quyền quản lý bộ công cụ để trợ lý dùng bộ của vai mình)
CALL core.secure_tenant('core.role_tools');
GRANT SELECT, INSERT, UPDATE, DELETE ON core.role_tools TO barista_app;
