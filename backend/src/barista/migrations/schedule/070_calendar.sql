-- module: schedule, core — lý do: 0158 — lịch chung; quyền mặc định mới của gói Lịch (xem bận / rảnh cho vai bán hàng, xem chi tiết `schedule.details`) cài cho công ty có trước như 055 (chỉ thêm dòng quyền + nhật ký cài).
-- 0158: MỘT đối tượng lịch cho mọi thứ chiếm thời gian. ``schedule.events`` thêm loại lịch (dữ liệu công ty), chế độ riêng tư, chuỗi lặp; hai kiểu
-- người tham gia mới cạnh nguồn lực (giữ cứng — ``bookings`` + ràng buộc loại trừ như cũ): NGƯỜI THAM DỰ trong công ty (mời, trả lời, hiện bận, KHÔNG
-- chặn cứng) và BÊN NGOÀI (mã định danh ở ``parties``, không khoá ngoại chéo module). Nguồn lực nhận nhiều lượt đặt cùng lúc (``slots``) — chặn khi đầy
-- bằng trigger giữ lượt theo nguồn lực; nguồn lực một lượt (mặc định, mọi dòng đang có) vẫn do ràng buộc loại trừ chặn y như trước.
-- Chỉ THÊM (cột có mặc định, bảng mới, hàm mới); ràng buộc no_double_booking dựng lại với điều kiện ``NOT shared`` — dòng đang có ``shared = false`` ⇒
-- cùng nghĩa. CẦN DỪNG DỊCH VỤ (deployment.json): dựng lại ràng buộc loại trừ + chính sách RLS của lịch dưới khoá độc quyền, và bản cũ không biết
-- người tham dự / riêng tư (chi tiết lịch nay chỉ cho người trong lịch + người có quyền `schedule.details`).
SET LOCAL lock_timeout = '10s';

-- ── Loại lịch: danh mục của CÔNG TY (gói góp bộ mặc định qua Pack(records=…) → schedule.save_kind) ──
CREATE TABLE schedule.kinds (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 key TEXT NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{0,39}$'),
 label TEXT NOT NULL CHECK (btrim(label) <> '' AND length(label) <= 80),
 active BOOLEAN NOT NULL DEFAULT true,
 sort INT NOT NULL DEFAULT 100,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, key)
);
CALL core.secure_tenant('schedule.kinds');
GRANT SELECT, INSERT, UPDATE ON schedule.kinds TO barista_app;

-- ── Chuỗi lặp (RFC 5545, tập con: DAILY / WEEKLY, INTERVAL, BYDAY, UNTIL, COUNT). Lần lặp SINH THÀNH DÒNG schedule.events trong một tầm (90 ngày,
-- vòng quét kéo dài) ⇒ ràng buộc loại trừ vẫn chặn từng lần. Mẫu người / phòng / người tham dự / bên ngoài để sinh lần mới. ──
CREATE TABLE schedule.series (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 title TEXT NOT NULL CHECK (btrim(title) <> ''),
 kind TEXT NOT NULL DEFAULT '',
 visibility TEXT NOT NULL DEFAULT 'normal' CHECK (visibility IN ('normal', 'private')),
 team_id UUID REFERENCES core.teams (id),
 note TEXT NOT NULL DEFAULT '',
 rrule TEXT NOT NULL CHECK (rrule ~ '^FREQ=(DAILY|WEEKLY)'),
 dtstart TIMESTAMPTZ NOT NULL,
 duration_min INT NOT NULL CHECK (duration_min BETWEEN 1 AND 10080),
 resource_ids UUID[] NOT NULL DEFAULT '{}',
 attendee_ids UUID[] NOT NULL DEFAULT '{}',
 party_ids UUID[] NOT NULL DEFAULT '{}',
 allow_outside BOOLEAN NOT NULL DEFAULT false,
 horizon_until TIMESTAMPTZ NOT NULL,              -- đã sinh lần lặp tới đây
 status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended', 'cancelled')),
 created_by UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id)
);
CALL core.secure_table('schedule.series', 'schedule', 'created_by', 'team_id');
GRANT SELECT, INSERT, UPDATE ON schedule.series TO barista_app;

ALTER TABLE schedule.events
 ADD COLUMN kind TEXT NOT NULL DEFAULT '',
 ADD COLUMN visibility TEXT NOT NULL DEFAULT 'normal' CHECK (visibility IN ('normal', 'private')),
 ADD COLUMN series_id UUID,
 ADD COLUMN occurrence_at TIMESTAMPTZ,              -- giờ bắt đầu GỐC theo quy tắc lặp (mã lần lặp — RECURRENCE-ID); giữ nguyên khi dời một lần
 ADD COLUMN detached BOOLEAN NOT NULL DEFAULT false, -- lần lặp đã sửa riêng ("chỉ lần này"): sửa cả chuỗi không đè lên
 ADD CONSTRAINT events_series_fk FOREIGN KEY (tenant_id, series_id) REFERENCES schedule.series (tenant_id, id),
 ADD CONSTRAINT events_occurrence CHECK ((series_id IS NULL) = (occurrence_at IS NULL));
CREATE UNIQUE INDEX uq_events_occurrence ON schedule.events (tenant_id, series_id, occurrence_at) WHERE series_id IS NOT NULL;

-- ── Người tham dự trong công ty: mời / nhận / từ chối / có thể; hiện bận, không chặn cứng ──
CREATE TABLE schedule.attendees (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 event_id UUID NOT NULL,
 user_id UUID NOT NULL REFERENCES core.users (id),
 rsvp TEXT NOT NULL DEFAULT 'invited' CHECK (rsvp IN ('invited', 'accepted', 'declined', 'tentative')),
 responded_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, event_id, user_id),
 FOREIGN KEY (tenant_id, event_id) REFERENCES schedule.events (tenant_id, id)
);
CREATE INDEX idx_attendees_user ON schedule.attendees (tenant_id, user_id);

-- ── Bên ngoài (khách, phụ huynh, đối tác): mã định danh ở parties — không khoá ngoại chéo module, không trả lời mời ──
CREATE TABLE schedule.event_parties (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 event_id UUID NOT NULL,
 party_id UUID NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, event_id, party_id),
 FOREIGN KEY (tenant_id, event_id) REFERENCES schedule.events (tenant_id, id)
);

-- ── Nhiều lượt đặt cùng lúc: ``slots`` (lượt), khác ``capacity`` (sức chứa người — chỉ để lọc). slots = 1 ⇒ ràng buộc loại trừ như cũ ──
ALTER TABLE schedule.resources ADD COLUMN slots INT NOT NULL DEFAULT 1 CHECK (slots BETWEEN 1 AND 10000);
ALTER TABLE schedule.bookings ADD COLUMN shared BOOLEAN NOT NULL DEFAULT false;   -- chép: nguồn lực nhiều lượt (slots > 1) lúc giữ chỗ
ALTER TABLE schedule.bookings DROP CONSTRAINT no_double_booking,
 ADD CONSTRAINT no_double_booking EXCLUDE USING gist (tenant_id WITH =, resource_id WITH =, during WITH &&) WHERE (active AND NOT shared);

-- Lượt đang giữ cao nhất của MỘT nguồn lực trong một khoảng (đỉnh chồng nhau đạt ở đầu khoảng hoặc lúc một lượt khác bắt đầu trong khoảng).
CREATE FUNCTION schedule.load_of(tenant UUID, rid UUID, span TSTZRANGE, except_event UUID, except_booking UUID) RETURNS INT
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 WITH b AS (SELECT during FROM schedule.bookings WHERE tenant_id = tenant AND resource_id = rid AND active AND during && span
              AND event_id IS DISTINCT FROM except_event AND id IS DISTINCT FROM except_booking)
 SELECT coalesce(max((SELECT count(*) FROM b b2 WHERE b2.during @> p.t)), 0)::int
 FROM (SELECT lower(span) AS t UNION SELECT lower(during) FROM b WHERE lower(during) > lower(span)) p
$$;

-- Nội bộ (tham số công ty): chỉ trigger / hàm SECURITY DEFINER của gói gọi — không ai khác chạy được.
REVOKE EXECUTE ON FUNCTION schedule.load_of(UUID, UUID, TSTZRANGE, UUID, UUID) FROM PUBLIC;
-- Bản cho người gọi: công ty của phiên, cần quyền xem Lịch; chỉ trả một con số.
CREATE FUNCTION schedule.load(rid UUID, span TSTZRANGE, except_event UUID, except_booking UUID) RETURNS INT
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 SELECT CASE WHEN core.scope('schedule') <> 'none' THEN schedule.load_of(core.tenant(), rid, span, except_event, except_booking) ELSE 0 END
$$;

CREATE OR REPLACE FUNCTION schedule.fill_booking() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE e RECORD; r RECORD;
BEGIN
 SELECT starts_at, ends_at, status, team_id INTO e FROM schedule.events WHERE tenant_id = NEW.tenant_id AND id = NEW.event_id;
 SELECT name, user_id, slots INTO r FROM schedule.resources WHERE tenant_id = NEW.tenant_id AND id = NEW.resource_id;
 IF e IS NULL OR r IS NULL THEN RAISE EXCEPTION 'Không thấy sự kiện / nguồn lực của dòng đặt lịch' USING ERRCODE = 'foreign_key_violation'; END IF;
 NEW.during := tstzrange(e.starts_at, e.ends_at);
 NEW.active := e.status <> 'cancelled' AND NEW.released_at IS NULL;
 NEW.team_id := e.team_id;
 NEW.resource_name := r.name;
 NEW.user_id := r.user_id;
 NEW.shared := r.slots > 1;
 RETURN NEW;
END $$;

-- ĐẦY LƯỢT do DB chặn: dòng giữ chỗ của nguồn lực nhiều lượt ⇒ giữ lượt theo nguồn lực (cùng khoá mã Python dùng, tới hết giao dịch), đếm lại bằng
-- ảnh chụp MỚI (hàm plpgsql: mỗi câu một ảnh chụp sau khi đã giữ lượt ⇒ thấy lần đặt vừa commit của giao dịch kia), đầy ⇒ lỗi loại trừ (23P01) như
-- nguồn lực một lượt — mã Python đổi thành một Conflict "đặt trùng".
CREATE FUNCTION schedule.guard_slots() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE cap INT;
BEGIN
 IF NOT NEW.active OR NOT NEW.shared THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('schedule.resource:' || NEW.resource_id::text, 0));
 SELECT slots INTO cap FROM schedule.resources WHERE tenant_id = NEW.tenant_id AND id = NEW.resource_id;
 IF schedule.load_of(NEW.tenant_id, NEW.resource_id, NEW.during, NULL, NEW.id) >= coalesce(cap, 1) THEN
  RAISE EXCEPTION 'Nguồn lực đã đủ lượt trong khoảng giờ này' USING ERRCODE = 'exclusion_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_slots BEFORE INSERT OR UPDATE OF during, active, shared ON schedule.bookings FOR EACH ROW EXECUTE FUNCTION schedule.guard_slots();

-- Đổi số lượt của nguồn lực ⇒ dòng giữ chỗ SẮP TỚI đổi cờ theo (về một lượt mà đang chồng nhau ⇒ ràng buộc loại trừ từ chối việc đổi).
CREATE OR REPLACE FUNCTION schedule.sync_resource() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
 UPDATE schedule.bookings SET resource_name = NEW.name, user_id = NEW.user_id WHERE tenant_id = NEW.tenant_id AND resource_id = NEW.id AND released_at IS NULL;
 IF NEW.slots IS DISTINCT FROM OLD.slots THEN
  UPDATE schedule.bookings SET shared = NEW.slots > 1
  WHERE tenant_id = NEW.tenant_id AND resource_id = NEW.id AND released_at IS NULL AND upper(during) > now() AND shared IS DISTINCT FROM (NEW.slots > 1);
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER sync_resource ON schedule.resources;
CREATE TRIGGER sync_resource AFTER UPDATE OF name, user_id, slots ON schedule.resources FOR EACH ROW EXECUTE FUNCTION schedule.sync_resource();

-- Bận của nguồn lực: đã đủ lượt (một lượt ⇒ có bất kỳ lịch nào chồng giờ) — chỉ trả mã, không lộ lịch.
CREATE OR REPLACE FUNCTION schedule.busy(ids UUID[], span TSTZRANGE, except_event UUID) RETURNS SETOF UUID LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path = pg_catalog AS $$
 SELECT r.id FROM schedule.resources r
 WHERE core.scope('schedule') <> 'none' AND r.tenant_id = core.tenant() AND r.id = ANY(ids)
   AND schedule.load_of(r.tenant_id, r.id, span, except_event, NULL) >= r.slots
$$;

-- ── Ai đọc CHI TIẾT một lịch (kiểu Teams): người tạo, người tham dự, người được xếp (nguồn lực gắn tài khoản) — và với lịch THƯỜNG, người có quyền
-- `schedule.details` (CHÍNH SÁCH công ty trong core.grants: mặc định quản trị + hành chính cả công ty, trưởng nhóm đội mình) trên đội của lịch hoặc đội của nguồn
-- lực lịch đang giữ. Lịch riêng tư: chỉ người trong lịch. Quyền Lịch (`schedule`, phạm vi nào cũng được) chỉ cho xem BẬN / RẢNH của mọi người trong công
-- ty (schedule.blocks — giờ, không tên việc, không người ngoài, không ghi chú). Quyền hệ thống (hợp đồng của module nguồn) vẫn tới.
-- Hàm bỏ qua RLS để không gọi vòng chính sách; chỉ trả đúng / sai. Một chính sách cho cả đọc lẫn ghi: dời / huỷ cũng chỉ trên lịch mình đọc được. ──
CREATE FUNCTION schedule.i_attend(event UUID) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 SELECT core.me() IS NOT NULL AND EXISTS (
   SELECT 1 FROM schedule.attendees a WHERE a.tenant_id = core.tenant() AND a.event_id = event AND a.user_id = core.me())
$$;
CREATE FUNCTION schedule.on_detail_resource(event UUID) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 SELECT core.scope('schedule.details') IN ('unit', 'team', 'all') AND EXISTS (
   SELECT 1 FROM schedule.bookings b JOIN schedule.resources r ON r.tenant_id = b.tenant_id AND r.id = b.resource_id
   WHERE b.tenant_id = core.tenant() AND b.event_id = event AND b.released_at IS NULL AND core.can_see('schedule.details', NULL, r.team_id))
$$;
DROP POLICY p_scope ON schedule.events;
DROP POLICY p_my_event ON schedule.events;
CREATE POLICY p_detail ON schedule.events
 USING (tenant_id = core.tenant() AND (core.is_system() OR (created_by IS NOT NULL AND created_by = core.me()) OR schedule.i_am_on(id) OR schedule.i_attend(id)
        OR (visibility = 'normal' AND (core.can_see('schedule.details', NULL, team_id) OR schedule.on_detail_resource(id)))))
 WITH CHECK (tenant_id = core.tenant() AND (core.is_system() OR (created_by IS NOT NULL AND created_by = core.me()) OR schedule.i_am_on(id) OR schedule.i_attend(id)
        OR (visibility = 'normal' AND (core.can_see('schedule.details', NULL, team_id) OR schedule.on_detail_resource(id)))));
DROP POLICY p_scope ON schedule.series;
CREATE POLICY p_detail ON schedule.series
 USING (tenant_id = core.tenant() AND (core.is_system() OR (created_by IS NOT NULL AND created_by = core.me())
        OR (visibility = 'normal' AND core.can_see('schedule.details', NULL, team_id))))
 WITH CHECK (tenant_id = core.tenant() AND (core.is_system() OR (created_by IS NOT NULL AND created_by = core.me())
        OR (visibility = 'normal' AND core.can_see('schedule.details', NULL, team_id))));
CALL core.secure_child('schedule.attendees', 'schedule.events', 'event_id');
CALL core.secure_child('schedule.event_parties', 'schedule.events', 'event_id');
GRANT SELECT, INSERT, UPDATE, DELETE ON schedule.attendees, schedule.event_parties TO barista_app;

-- Khối bận của người / nguồn lực trong một khoảng, KHÔNG kèm chi tiết: giữ cứng (dòng giữ chỗ đang có) và mềm (đang được mời / đã nhận / có thể).
-- Cho tìm giờ, xem lịch của người khác, lịch tuần của nguồn lực mình quản lý. Người không có quyền xem Lịch ⇒ rỗng.
CREATE FUNCTION schedule.blocks(rids UUID[], uids UUID[], span TSTZRANGE, except_event UUID)
 RETURNS TABLE (event_id UUID, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ, resource_id UUID, user_id UUID, hard BOOLEAN, rsvp TEXT)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 SELECT b.event_id, lower(b.during), upper(b.during), b.resource_id, b.user_id, true, NULL::text FROM schedule.bookings b
 WHERE core.scope('schedule') <> 'none' AND b.tenant_id = core.tenant() AND b.active AND b.during && span AND b.event_id IS DISTINCT FROM except_event
   AND (b.resource_id = ANY(rids) OR b.user_id = ANY(uids))
 UNION ALL
 SELECT a.event_id, e.starts_at, e.ends_at, NULL::uuid, a.user_id, false, a.rsvp FROM schedule.attendees a
 JOIN schedule.events e ON e.tenant_id = a.tenant_id AND e.id = a.event_id
 WHERE core.scope('schedule') <> 'none' AND a.tenant_id = core.tenant() AND e.status <> 'cancelled' AND a.rsvp <> 'declined' AND a.user_id = ANY(uids)
   AND tstzrange(e.starts_at, e.ends_at) && span AND e.id IS DISTINCT FROM except_event
$$;

-- Khoảng giờ của MỘT lịch (thẻ "Bận" khi người xem không được đọc chi tiết): chỉ giờ, lịch chưa huỷ, người có quyền xem Lịch.
CREATE FUNCTION schedule.busy_span(event UUID) RETURNS TSTZRANGE LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 SELECT tstzrange(e.starts_at, e.ends_at) FROM schedule.events e
 WHERE core.scope('schedule') <> 'none' AND e.tenant_id = core.tenant() AND e.id = event AND e.status <> 'cancelled'
$$;

-- Số lịch sắp tới đang đặt của công ty, KỂ CẢ lịch riêng tư người hỏi không đọc được (chặn tắt gói — chỉ một con số).
CREATE FUNCTION schedule.upcoming_count() RETURNS BIGINT LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 SELECT count(*) FROM schedule.events WHERE core.scope('schedule') <> 'none' AND tenant_id = core.tenant() AND status = 'booked' AND ends_at > now()
$$;

-- Quyền mặc định mới (0158) cho công ty có trước — như 055: chỉ vai đang có ở công ty, chỉ khi chưa từng cài (có dòng nhật ký ⇒ giữ quyết định của công ty).
WITH want(role, resource, scope, actions) AS (VALUES
    ('admin',       'schedule.details', 'all',  ARRAY['read']),
    ('office',      'schedule.details', 'all',  ARRAY['read']),
    ('team_lead',   'schedule.details', 'team', ARRAY['read']),
    ('team_lead',   'schedule',         'team', ARRAY['read']),
    ('sale',        'schedule',         'own',  ARRAY['read']),
    ('sales_admin', 'schedule',         'own',  ARRAY['read']),
    ('centre_manager',  'schedule.details', 'team', ARRAY['read']),
    ('edu_coordinator', 'schedule.details', 'unit', ARRAY['read']))
INSERT INTO core.grants(tenant_id, role, resource, scope, actions)
SELECT r.tenant_id, r.key, w.resource, w.scope, w.actions
FROM core.roles r JOIN want w ON w.role = r.key
WHERE NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = r.tenant_id AND i.kind = 'grant' AND i.key = r.key || '|' || w.resource)
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs(tenant_id, kind, key, pack, version)
SELECT r.tenant_id, 'grant', r.key || '|' || w.resource, 'schedule', NULL
FROM core.roles r JOIN (VALUES ('admin', 'schedule.details'), ('office', 'schedule.details'), ('team_lead', 'schedule.details'), ('team_lead', 'schedule'), ('sale', 'schedule'),
                               ('sales_admin', 'schedule'), ('centre_manager', 'schedule.details'), ('edu_coordinator', 'schedule.details'))
                               AS w(role, resource) ON w.role = r.key
ON CONFLICT DO NOTHING;

CALL core.initplan_policies(ARRAY['schedule']);
