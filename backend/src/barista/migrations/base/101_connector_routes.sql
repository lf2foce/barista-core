-- module: connectors
-- 101 (bổ sung ADR 0244, api 1.66, 19/11/2026) — ĐỊNH TUYẾN WEBHOOK CHUNG: nhà cung cấp chỉ cho MỘT địa chỉ webhook cho cả app (Meta: mọi Trang của mọi
-- công ty về một địa chỉ) ⇒ bản cài có MỘT cửa chung theo bộ nối; tin chỉ nói "khoá định tuyến X (vd mã Trang) có việc mới". Bảng này nói khoá đó thuộc
-- kết nối nào. Kết nối KHAI khoá lúc lưu (chưa xác minh); khoá chỉ có hiệu lực sau khi một lượt kéo bằng CHÍNH token của kết nối đọc được nó
-- (``verified_at``) ⇒ công ty không giữ chỗ được khoá của công ty khác chỉ bằng cách gõ mã. Mỗi khoá chỉ MỘT kết nối đã xác minh (chỉ mục duy nhất
-- từng phần). Cửa chung chỉ ĐÁNH THỨC kết nối sở hữu khoá — không ghi dữ liệu khách xuyên công ty. Chỉ THÊM ⇒ bản cũ không đọc ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

CREATE TABLE connectors.routes (
    tenant_id uuid NOT NULL DEFAULT core.tenant(),
    connection_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*$'),
    route_key text NOT NULL CHECK (route_key ~ '^[A-Za-z0-9_.:-]{1,120}$'),
    verified_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, connection_id, route_key),
    FOREIGN KEY (tenant_id, connection_id) REFERENCES connectors.connections (tenant_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX connector_routes_one_owner ON connectors.routes (kind, route_key) WHERE verified_at IS NOT NULL;
CREATE INDEX idx_connector_routes_key ON connectors.routes (kind, route_key);
CALL core.secure_child('connectors.routes', 'connectors.connections', 'connection_id');
GRANT SELECT, INSERT, UPDATE, DELETE ON connectors.routes TO barista_app;
