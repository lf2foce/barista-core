-- module: memory
-- 003 (0091): TRÍ NHỚ DOANH NGHIỆP — mục nhớ là DỮ LIỆU có nguồn, không phải model "tự nhớ": phạm vi (của một người · gắn một đối tượng nghiệp vụ ·
-- cả công ty), loại (dữ kiện / sở thích / cách làm / bài học), nguồn (vết nhật ký thao tác — 0086 — nơi sinh ra nó), trạng thái, hạn còn đúng.
-- Ai thấy dòng nào do Postgres quyết:
--   person  — CHỈ chính người đó (như cuộc trò chuyện: quản trị cũng không đọc). Là dữ liệu CÔNG VIỆC: người đó nghỉ (hết thành viên) ⇒ ai
--             giữ quyền ``memory.legacy`` phạm vi cả công ty (mặc định giám đốc) thấy để chuyển thành trí nhớ chung / xoá;
--   subject — ai thấy ĐỐI TƯỢNG thì thấy: hỏi thẳng module chủ đối tượng qua ``core.subject_visible`` (hàm thấy-được module khai — như
--             trao đổi / việc nhờ gắn đối tượng). Không chép người phụ trách: đổi người / đổi thành viên phòng là đổi quyền ngay;
--   company — mọi thành viên đọc; ghi / bỏ do lệnh kiểm (người quản lý tri thức công ty) — như mọi module, hành động kiểm ở cửa chạy lệnh,
--             RLS giữ phạm vi.
CREATE SCHEMA memory;
GRANT USAGE ON SCHEMA memory TO barista_app;

CREATE TABLE memory.items (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    scope        TEXT NOT NULL CHECK (scope IN ('person', 'subject', 'company')),
    kind         TEXT NOT NULL CHECK (kind IN ('fact', 'preference', 'procedure', 'lesson')),
    text         TEXT NOT NULL CHECK (btrim(text) <> '' AND length(text) <= 1000),
    search       TSVECTOR NOT NULL,                       -- chữ bỏ dấu (kernel/text.fold) — tìm "khach tre" ra "khách trễ"
    owner_id     UUID REFERENCES core.users(id),          -- chỉ phạm vi person: chủ nhân
    subject_kind TEXT CHECK (subject_kind ~ '^[a-z_]+\.[a-z_]+$'),
    subject_id   UUID,
    subject_title TEXT NOT NULL DEFAULT '',               -- tên đối tượng lúc ghi — để hiển thị, KHÔNG dùng để quyết quyền
    sources      JSONB NOT NULL DEFAULT '[]',             -- [{trace}] — nơi sinh ra (lượt trò chuyện / đề xuất sáng / việc theo dõi…)
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    valid_until  DATE,                                    -- quá hạn ⇒ không còn được gợi lại, vào hàng "cần xem lại"
    reviewed_at  TIMESTAMPTZ,                             -- người xem lại bấm "giữ" — tính lại hạn xem lại từ đây
    created_by   UUID REFERENCES core.users(id),
    via          TEXT NOT NULL DEFAULT 'human' CHECK (via IN ('human', 'agent')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at   TIMESTAMPTZ,
    retired_by   UUID REFERENCES core.users(id),
    CHECK ((scope = 'person') = (owner_id IS NOT NULL)),
    CHECK ((scope = 'subject') = (subject_kind IS NOT NULL AND subject_id IS NOT NULL)),
    CHECK (scope = 'subject' OR (subject_kind IS NULL AND subject_id IS NULL)),
    CHECK (via = 'human' OR jsonb_array_length(sources) > 0)   -- trợ lý soạn ⇒ luôn truy được lượt / việc đã sinh ra nó
);
CREATE INDEX idx_memory_search ON memory.items USING gin (search) WHERE status = 'active';
CREATE INDEX idx_memory_subject ON memory.items (tenant_id, subject_kind, subject_id) WHERE subject_id IS NOT NULL;
CREATE INDEX idx_memory_owner ON memory.items (tenant_id, owner_id) WHERE scope = 'person';

ALTER TABLE memory.items ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory.items FORCE ROW LEVEL SECURITY;
CREATE POLICY p_scope ON memory.items
    USING (tenant_id = core.tenant() AND (
        (scope = 'person' AND (owner_id = core.me() OR (core.scope('memory.legacy') = 'all' AND NOT EXISTS (
            SELECT 1 FROM core.memberships ms WHERE ms.tenant_id = core.tenant() AND ms.user_id = owner_id AND ms.active))))
        OR scope = 'company'
        OR (scope = 'subject' AND core.subject_visible(subject_kind, subject_id))))
    WITH CHECK (tenant_id = core.tenant() AND (
        (scope = 'person' AND (owner_id = core.me() OR (core.scope('memory.legacy') = 'all' AND NOT EXISTS (
            SELECT 1 FROM core.memberships ms WHERE ms.tenant_id = core.tenant() AND ms.user_id = owner_id AND ms.active))))
        OR scope = 'company'
        OR (scope = 'subject' AND core.subject_visible(subject_kind, subject_id))));
-- XOÁ THẬT chỉ cho mục "của tôi" (chính chủ bỏ = xoá — dữ liệu cá nhân, Nghị định 13); mục gắn đối tượng / cả công ty chỉ "bỏ" (giữ để tra nguồn)
CREATE POLICY p_delete_person ON memory.items AS RESTRICTIVE FOR DELETE USING (scope = 'person');
GRANT SELECT, INSERT, UPDATE, DELETE ON memory.items TO barista_app;

-- Mỗi lần một mục được đưa ra (trợ lý / người nhớ lại) — chỉ thêm. Mục lâu không ai dùng ⇒ hàng "cần xem lại" (giữ / bỏ), không tự xoá.
-- ``trace`` = lượt đã dùng nó ⇒ nối được "dùng điều nhớ này ⇒ đề xuất được xác nhận / bị bỏ" (0086).
CREATE TABLE memory.uses (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    item_id    UUID NOT NULL REFERENCES memory.items(id) ON DELETE CASCADE,
    trace      TEXT NOT NULL DEFAULT '',
    at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_memory_uses_item ON memory.uses (item_id, at DESC);
CALL core.secure_child('memory.uses', 'memory.items', 'item_id');
GRANT SELECT, INSERT ON memory.uses TO barista_app;
