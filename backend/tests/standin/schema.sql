-- module: orderdesk
-- BẢNG CỦA GÓI THẾ VAI (tests/standin/pack.py) — chỉ có trong DB test, do fixture của tests/standin/conftest.py chạy một lần mỗi DB; KHÔNG nằm trong thư
-- mục migration của lõi. Viết như migration đầu của một gói ngành thật: schema riêng, bảng qua core.secure_table / secure_child, hàm "thấy đối tượng".
-- Dòng đăng ký loại đối tượng (core.subject_kinds) do fixture thêm / gỡ quanh từng file test — sổ đó phải khớp registry (tests/test_meta.py).
CREATE SCHEMA orderdesk;
GRANT USAGE ON SCHEMA orderdesk TO barista_app;

CREATE TABLE orderdesk.orders (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    code          TEXT NOT NULL,
    title         TEXT NOT NULL,
    customer      TEXT NOT NULL,
    line          TEXT NOT NULL DEFAULT 'TV',                     -- dòng dịch vụ (chiều của tập dữ liệu)
    owner_id      UUID NOT NULL REFERENCES core.users(id),
    team_id       UUID REFERENCES core.teams(id),
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'signed', 'cancelled')),
    amount_vnd    BIGINT NOT NULL CHECK (amount_vnd > 0),
    fee_vnd       BIGINT NOT NULL CHECK (fee_vnd >= 0),            -- phí công ty thu của khách khi ký (khoản phải thu bên Tài chính)
    bonus_vnd     BIGINT NOT NULL CHECK (bonus_vnd >= 0),          -- thưởng người phụ trách, về ví theo phí đã thu
    earned_vnd    BIGINT NOT NULL DEFAULT 0 CHECK (earned_vnd >= 0),
    created_on    DATE NOT NULL DEFAULT core.today(),
    signed_on     DATE,
    signed_at     TIMESTAMPTZ,
    cancel_reason TEXT NOT NULL DEFAULT '',
    earn_refs     TEXT[] NOT NULL DEFAULT '{}',                 -- tham chiếu các nghĩa vụ thưởng đã ghi (để điều chỉnh khi huỷ)
    UNIQUE (tenant_id, code)
);
CALL core.secure_table('orderdesk.orders', 'orderdesk', 'owner_id', 'team_id');
-- core.secure_table ghi chú "resource=orderdesk"; bảng còn trong DB khi gói thế vai đã gỡ khỏi registry (giữa các file test) ⇒ ghi chú trung tính
COMMENT ON TABLE orderdesk.orders IS 'gói thế vai của test (tests/standin) — tài nguyên orderdesk';
GRANT SELECT, INSERT, UPDATE ON orderdesk.orders TO barista_app;

CREATE TABLE orderdesk.stages (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    order_id   UUID NOT NULL REFERENCES orderdesk.orders(id) ON DELETE CASCADE,
    seq        INT NOT NULL CHECK (seq >= 1),
    label      TEXT NOT NULL,
    due_on     DATE,
    amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
    paid_vnd   BIGINT NOT NULL DEFAULT 0 CHECK (paid_vnd >= 0),
    paid_on    DATE,
    UNIQUE (order_id, seq)
);
CALL core.secure_child('orderdesk.stages', 'orderdesk.orders', 'order_id');
GRANT SELECT, INSERT, UPDATE ON orderdesk.stages TO barista_app;

CREATE FUNCTION orderdesk.subject_visible_order(id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $_$ SELECT EXISTS (SELECT 1 FROM orderdesk.orders o WHERE o.id = $1) $_$;
