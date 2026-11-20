-- module: kpi
-- 010 (0101): KPI & XẾP HẠNG — chỉ tiêu là DỮ LIỆU: một dòng mỗi (kỳ = tháng, chỉ số, người HOẶC đội); số thực tế KHÔNG lưu — tính lúc xem từ
-- chỉ số module khai (``Module.metric``, cùng định nghĩa với mục tiêu kế hoạch); CHỐT KỲ = ảnh chụp chỉ tiêu + thực tế (chỉ thêm) để số trả
-- thưởng không đổi khi dữ liệu cũ được sửa sau. Hiện tên người khác trên bảng xếp hạng là CHÍNH SÁCH công ty. Chỉ THÊM.
CREATE SCHEMA kpi;
GRANT USAGE ON SCHEMA kpi TO barista_app;

CREATE TABLE kpi.targets (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    period     DATE NOT NULL CHECK (extract(day FROM period) = 1),       -- ngày đầu tháng
    metric     TEXT NOT NULL CHECK (metric ~ '^[a-z_]+\.[a-z_]+$'),     -- khoá chỉ số (registry.METRICS)
    user_id    UUID REFERENCES core.users(id) ON DELETE CASCADE,         -- chỉ tiêu CỦA MỘT NGƯỜI (đội = đội của người đó lúc đặt — để trưởng đội thấy)
    team_id    UUID REFERENCES core.teams(id) ON DELETE CASCADE,         -- hoặc chỉ tiêu CỦA MỘT ĐỘI (user_id trống) — cộng cả cây đội con
    target     NUMERIC(18, 2) NOT NULL CHECK (target > 0),
    set_by     UUID REFERENCES core.users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (user_id IS NOT NULL OR team_id IS NOT NULL)
);
CREATE UNIQUE INDEX uq_kpi_person ON kpi.targets (tenant_id, period, metric, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_kpi_team ON kpi.targets (tenant_id, period, metric, team_id) WHERE user_id IS NULL;
CALL core.secure_table('kpi.targets', 'kpi', 'user_id', 'team_id');
GRANT SELECT, INSERT, UPDATE, DELETE ON kpi.targets TO barista_app;

-- Chốt kỳ: ảnh chụp từng dòng chỉ tiêu lúc chốt (thực tế tính bằng chỉ số tại thời điểm đó). Chỉ thêm — chốt lại = dòng mới (closed_at mới hơn).
CREATE TABLE kpi.closings (
    id         BIGSERIAL PRIMARY KEY,
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    period     DATE NOT NULL,
    metric     TEXT NOT NULL,
    user_id    UUID REFERENCES core.users(id),
    team_id    UUID REFERENCES core.teams(id),
    target     NUMERIC(18, 2) NOT NULL,
    actual     NUMERIC(18, 2) NOT NULL,
    closed_by  UUID NOT NULL REFERENCES core.users(id),
    closed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_kpi_closings ON kpi.closings (tenant_id, period, closed_at DESC);
CALL core.secure_table('kpi.closings', 'kpi', 'user_id', 'team_id');
GRANT SELECT, INSERT ON kpi.closings TO barista_app;
GRANT USAGE ON SEQUENCE kpi.closings_id_seq TO barista_app;

-- Chính sách hiển thị: bảng xếp hạng có hiện TÊN người khác cho người phạm vi "của mình" không (mặc định: không — chỉ thấy hạng của mình).
CREATE TABLE kpi.policy (
    tenant_id  UUID PRIMARY KEY DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    show_names BOOLEAN NOT NULL DEFAULT false,
    updated_by UUID REFERENCES core.users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CALL core.secure_tenant('kpi.policy');
GRANT SELECT, INSERT, UPDATE ON kpi.policy TO barista_app;
