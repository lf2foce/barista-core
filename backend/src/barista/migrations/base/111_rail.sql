-- module: core — ADR 0255 lát 1 (thanh bên theo vai + ghim phân hệ + "Tất cả mục"). Hai việc nhỏ, đều CHỈ THÊM / NỚI (bản cũ chạy song song không gãy):
-- (1) ghim theo người nhận khoá màn TRỐNG = ghim cả PHÂN HỆ lên thanh bên (``core.pin`` / ``core.unpin``): nới CHECK của ``core.user_pins.key``;
--     bản cũ không bao giờ ghi khoá trống và bỏ qua dòng khoá trống khi đọc.
-- (2) ``core.role_rail``: phần CÔNG TY ĐỔI của "phân hệ hiện sẵn trên thanh bên theo vai". Mặc định do GÓI khai (``Pack.rail``, đọc từ mã) — bảng này chỉ giữ chỗ
--     công ty đặt lại (một dòng / vai, danh sách thay thế hoàn toàn; xoá dòng = về mặc định của gói) nên nâng gói không ghi đè lựa chọn đã lưu. Không cấp quyền.

SET LOCAL lock_timeout = '10s';

ALTER TABLE core.user_pins DROP CONSTRAINT user_pins_key_check;
ALTER TABLE core.user_pins ADD CONSTRAINT user_pins_key_check CHECK (key ~ '^([a-z0-9_-]{1,80})?$');

CREATE TABLE core.role_rail (
    tenant_id   UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,                                   -- khoá core.roles
    workspaces  TEXT[] NOT NULL CHECK (cardinality(workspaces) BETWEEN 1 AND 6),   -- khoá phân hệ, theo thứ tự hiện; trần dùng thật ở code (registry.RAIL_MAX)
    updated_by  UUID REFERENCES core.users(id),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, role)
);
-- cách ly theo công ty; ai được sửa do quyền tài nguyên ``core.admin`` ở executor. Thanh bên của người xem đọc dòng của VAI mình bằng giao dịch của chính họ
-- (không cần quyền quản lý).
CALL core.secure_tenant('core.role_rail');
GRANT SELECT, INSERT, UPDATE, DELETE ON core.role_rail TO barista_app;
