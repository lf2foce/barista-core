-- module: meet
-- 0114: cuộc gọi gắn vào một đối tượng (hoặc phòng trao đổi), link là của công ty (Google Meet / Zoom). Ai thấy đối tượng mới thấy cuộc gọi — kiểm ở DB
-- lúc truy cập, không chép quyền sang đây. Nhật ký người tham dự chỉ thêm, ghi rõ nguồn (hiện chỉ có "bấm nút").
CREATE SCHEMA meet;
GRANT USAGE ON SCHEMA meet TO barista_app;

CREATE TABLE meet.settings (
 tenant_id UUID PRIMARY KEY DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 room_url TEXT NOT NULL DEFAULT '' CHECK(room_url='' OR room_url ~ '^https://'),
 updated_by UUID REFERENCES core.users(id), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CALL core.secure_tenant('meet.settings');
GRANT SELECT,INSERT ON meet.settings TO barista_app;
GRANT UPDATE(room_url,updated_by,updated_at) ON meet.settings TO barista_app;

CREATE TABLE meet.calls (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 subject_kind TEXT NOT NULL, subject_id UUID NOT NULL,
 title TEXT NOT NULL CHECK(btrim(title)<>''),
 provider TEXT NOT NULL DEFAULT 'external',
 join_url TEXT NOT NULL CHECK(join_url ~ '^https://'),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','ended')),
 started_by UUID NOT NULL REFERENCES core.users(id), started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 ended_at TIMESTAMPTZ, ended_by UUID REFERENCES core.users(id),
 end_reason TEXT CHECK(end_reason IN ('person','stale')),
 confirmed_by UUID REFERENCES core.users(id), confirmed_at TIMESTAMPTZ,
 confirmed_user_ids UUID[] NOT NULL DEFAULT '{}', confirmed_others TEXT NOT NULL DEFAULT '', confirmed_note TEXT NOT NULL DEFAULT '',
 CHECK((status='ended')=(ended_at IS NOT NULL)), CHECK((status='ended')=(end_reason IS NOT NULL)),
 CHECK(confirmed_at IS NULL OR status='ended'), CHECK((confirmed_at IS NULL)=(confirmed_by IS NULL))
);
-- Một đối tượng chỉ có một cuộc gọi đang diễn ra: hai người cùng bấm Gọi thì vào chung, không tách làm hai.
CREATE UNIQUE INDEX calls_one_active ON meet.calls(tenant_id,subject_kind,subject_id) WHERE status='active';
CREATE INDEX calls_subject ON meet.calls(tenant_id,subject_kind,subject_id,started_at DESC);
CREATE INDEX calls_stale ON meet.calls(started_at) WHERE status='active';
ALTER TABLE meet.calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE meet.calls FORCE ROW LEVEL SECURITY;
COMMENT ON TABLE meet.calls IS 'resource=meet';
CREATE POLICY p_subject ON meet.calls USING (tenant_id=core.tenant() AND core.subject_visible(subject_kind,subject_id))
 WITH CHECK (tenant_id=core.tenant() AND core.subject_visible(subject_kind,subject_id));
-- Vòng quét đóng cuộc gọi bị bỏ quên: hàm thấy-được của phòng trả false khi không có người gọi, và gói của đối tượng có thể đã tắt.
CREATE POLICY p_system ON meet.calls USING (tenant_id=core.tenant() AND core.is_system())
 WITH CHECK (tenant_id=core.tenant() AND core.is_system());
CREATE POLICY p_no_link ON meet.calls AS RESTRICTIVE FOR SELECT USING (core.link_ref() IS NULL);
GRANT SELECT,INSERT ON meet.calls TO barista_app;
-- Đối tượng và link đã gắn thì không đổi; cột sửa được chỉ là phần kết thúc và xác nhận.
GRANT UPDATE(status,ended_at,ended_by,end_reason,confirmed_by,confirmed_at,confirmed_user_ids,confirmed_others,confirmed_note) ON meet.calls TO barista_app;

CREATE TABLE meet.participants (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 call_id UUID NOT NULL REFERENCES meet.calls(id) ON DELETE CASCADE,
 user_id UUID NOT NULL REFERENCES core.users(id),
 event TEXT NOT NULL CHECK(event IN ('join','leave')),
 source TEXT NOT NULL CHECK(source='click'),
 at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX participants_call ON meet.participants(call_id,id);
CALL core.secure_child('meet.participants','meet.calls','call_id');
-- Chỉ ghi được việc bấm của chính mình; không có quyền UPDATE / DELETE nên nhật ký chỉ thêm kể cả khi handler viết sai.
CREATE POLICY p_self ON meet.participants AS RESTRICTIVE FOR INSERT WITH CHECK (user_id=core.me());
GRANT SELECT,INSERT ON meet.participants TO barista_app;
