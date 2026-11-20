-- module: core
-- 081 (ADR 0193): HẠNG TRỢ LÝ AI THEO VAI — chính sách là dữ liệu. Mỗi công ty có các HẠNG (model dùng, model người trong hạng tự chọn thêm, hạn mức
-- chi theo NGƯỜI mỗi tháng, có được nói chuyện bằng giọng không); mỗi VAI thuộc một hạng (vai chưa gán ⇒ hạng mặc định của công ty). Bộ mặc định đi kèm
-- gói (``Pack(records=…)`` + ``Role(ai_tier=…)``), cài qua ``admin.install_defaults`` — chỉ thêm; quản trị sửa ở Quản trị › Hạng trợ lý AI.
-- Chi theo người đếm ở ``core.ai_person_spend`` (cộng cùng giao dịch ghi lượt, cạnh chi của công ty ``core.ai_spend`` — 0092 giữ nguyên).
-- CHỈ THÊM bảng mới (không sửa bảng đang chạy) ⇒ bản cũ chạy song song không đọc / ghi gì ở đây, không cần dừng dịch vụ.

SET LOCAL lock_timeout = '10s';

CREATE TABLE core.ai_tiers (
    tenant_id          UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    key                TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{1,39}$'),
    label              TEXT NOT NULL CHECK (btrim(label) <> ''),
    -- chuỗi model kiểu "nhà_cung_cấp:tên" (agent/llm.py hiểu); NULL = model mặc định của bản cài. Nhà cung cấp chưa có khoá ⇒ dùng model mặc định (có log)
    model              TEXT CHECK (model IS NULL OR model ~ '^[a-z][a-z0-9-]*:[^\s:]+$'),
    choices            TEXT[] NOT NULL DEFAULT '{}',              -- model người trong hạng được TỰ CHỌN thay model của hạng (trống = không chọn)
    person_monthly_usd NUMERIC(10, 2) CHECK (person_monthly_usd > 0), -- trần chi AI của MỖI NGƯỜI trong hạng / tháng; NULL = không trần theo người
    voice              BOOLEAN NOT NULL DEFAULT false,            -- được mở nói chuyện bằng giọng (Gemini Live)
    is_default         BOOLEAN NOT NULL DEFAULT false,            -- hạng của vai chưa gán hạng (đúng một hạng mặc định / công ty)
    sort               INT NOT NULL DEFAULT 100,
    updated_by         UUID REFERENCES core.users(id),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);
CREATE UNIQUE INDEX ai_tiers_one_default ON core.ai_tiers (tenant_id) WHERE is_default;
CALL core.secure_tenant('core.ai_tiers');
GRANT SELECT, INSERT, UPDATE, DELETE ON core.ai_tiers TO barista_app;

CREATE TABLE core.ai_role_tiers (
    tenant_id UUID NOT NULL DEFAULT core.tenant(),
    role      TEXT NOT NULL,
    tier      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, role),
    FOREIGN KEY (tenant_id, role) REFERENCES core.roles(tenant_id, key) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, tier) REFERENCES core.ai_tiers(tenant_id, key) ON DELETE CASCADE   -- xoá hạng ⇒ vai về hạng mặc định
);
CALL core.secure_tenant('core.ai_role_tiers');
GRANT SELECT, INSERT, UPDATE, DELETE ON core.ai_role_tiers TO barista_app;

-- model người tự chọn trong danh sách hạng cho phép (``core.choose_ai_model``) — chỉ còn hiệu lực khi model đó vẫn nằm trong ``choices`` của hạng hiện tại
CREATE TABLE core.ai_choices (
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL REFERENCES core.users(id),
    model      TEXT NOT NULL CHECK (model ~ '^[a-z][a-z0-9-]*:[^\s:]+$'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
);
CALL core.secure_tenant('core.ai_choices');
-- của riêng từng người: đọc / ghi chỉ dòng của mình (quyền hệ thống và phiên chủ — auth.load — vẫn đọc được)
CREATE POLICY p_own ON core.ai_choices AS RESTRICTIVE
    USING (user_id = (SELECT core.me()) OR (SELECT core.is_system()))
    WITH CHECK (user_id = (SELECT core.me()) OR (SELECT core.is_system()));
GRANT SELECT, INSERT, UPDATE, DELETE ON core.ai_choices TO barista_app;

CREATE TABLE core.ai_person_spend (
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL REFERENCES core.users(id),
    month      DATE NOT NULL CHECK (month = date_trunc('month', month)::date),   -- ngày 1 của tháng (giờ công ty)
    usd        NUMERIC(14, 6) NOT NULL DEFAULT 0 CHECK (usd >= 0),
    stopped_at TIMESTAMPTZ,                                                         -- đã báo người này hết hạn mức (một lần / tháng)
    PRIMARY KEY (tenant_id, user_id, month)
);
CALL core.secure_tenant('core.ai_person_spend');
-- người dùng ghi dòng CỦA MÌNH (lượt của họ); xem: dòng của mình, hoặc người xem chi phí AI cả công ty (cùng quyền màn Chi phí trợ lý AI)
CREATE POLICY p_person ON core.ai_person_spend AS RESTRICTIVE
    USING (user_id = (SELECT core.me()) OR (SELECT core.is_system()) OR (SELECT core.scope('core.ai_usage')) = 'all')
    WITH CHECK (user_id = (SELECT core.me()) OR (SELECT core.is_system()));
GRANT SELECT, INSERT, UPDATE ON core.ai_person_spend TO barista_app;
