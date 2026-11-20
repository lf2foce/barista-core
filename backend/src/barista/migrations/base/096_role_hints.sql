-- module: core
-- 096 (ADR 0239 phần 4, barista-docs): CÂU GỢI Ý CỦA TRỢ LÝ THEO VAI là DỮ LIỆU công ty. Bộ MẶC ĐỊNH vẫn do gói khai (Role.suggest / showcase,
-- Pack.hints) và đọc thẳng từ mã — bảng này chỉ giữ phần CÔNG TY ĐỔI: tắt một câu mặc định, sửa chữ một câu mặc định (bản gốc của gói giữ nguyên ⇒
-- nâng gói không ghi đè, "về mặc định" = xoá dòng), hoặc thêm câu riêng của vai (gắn một công cụ trả lời nó — câu chỉ hiện cho người chạy được công cụ
-- đó). Câu chữ KHÔNG cấp quyền: công cụ vẫn kiểm quyền khi gọi. CHỈ THÊM (bảng mới rỗng) ⇒ bản cũ chạy song song không đọc / ghi gì ở đây.

SET LOCAL lock_timeout = '10s';

CREATE TABLE core.role_hints (
    tenant_id   UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,                                   -- khoá core.roles
    key         TEXT NOT NULL CHECK (length(key) BETWEEN 1 AND 200),   -- khoá câu mặc định của gói (kernel/ai_hints.py) hoặc "c:<mã>" = câu riêng
    enabled     BOOLEAN NOT NULL DEFAULT true,
    text        TEXT CHECK (text IS NULL OR length(text) BETWEEN 3 AND 300),   -- NULL = chữ mặc định của gói
    tool        TEXT,                                            -- câu riêng: thao tác trả lời câu (tên ``module.tên``); câu mặc định: NULL
    updated_by  UUID REFERENCES core.users(id),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, role, key),
    CHECK (key NOT LIKE 'c:%' OR (tool IS NOT NULL AND text IS NOT NULL))
);
-- cách ly theo công ty; ai được sửa do quyền tài nguyên ``core.ai_tools`` ở executor (cùng màn với Công cụ trợ lý theo vai — 0218). Gợi ý của người
-- hỏi đọc dòng của vai mình bằng giao dịch của chính họ (không cần quyền quản lý).
CALL core.secure_tenant('core.role_hints');
GRANT SELECT, INSERT, UPDATE, DELETE ON core.role_hints TO barista_app;
