-- module: schedule
-- 0113: lịch và nguồn lực dùng chung mọi ngành — người / phòng / thiết bị, giờ rảnh theo tuần, sự kiện giữ nhiều nguồn lực, KHÔNG TRÙNG LỊCH do DB chặn.
-- btree_gist là extension "trusted" (Postgres 13+): chủ database (barista_owner) tự tạo được, không cần superuser; ảnh postgres chính thức có sẵn contrib.
CREATE EXTENSION IF NOT EXISTS btree_gist SCHEMA public;

CREATE SCHEMA schedule;
GRANT USAGE ON SCHEMA schedule TO barista_app;

CREATE TABLE schedule.resources (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 name TEXT NOT NULL CHECK (btrim(name) <> ''),
 nature TEXT NOT NULL CHECK (nature IN ('person', 'thing')),
 kind TEXT NOT NULL CHECK (btrim(kind) <> '' AND kind = lower(btrim(kind))),
 tags TEXT[] NOT NULL DEFAULT '{}',
 capacity INT NOT NULL DEFAULT 1 CHECK (capacity >= 1),
 team_id UUID REFERENCES core.teams (id),
 user_id UUID REFERENCES core.users (id),
 party_id UUID,                                   -- định danh ở parties (không khoá ngoại chéo module)
 active BOOLEAN NOT NULL DEFAULT true,
 note TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id, id),
 CHECK (nature = 'person' OR (user_id IS NULL AND party_id IS NULL))
);
CREATE UNIQUE INDEX uq_resources_user ON schedule.resources (tenant_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_resources_party ON schedule.resources (tenant_id, party_id) WHERE party_id IS NOT NULL;
CREATE INDEX idx_resources_tags ON schedule.resources USING gin (tags);

-- Giờ rảnh theo tuần, giờ Việt Nam, tính bằng phút trong ngày (0–1440; 1440 = hết ngày). Không có dòng nào = không giới hạn giờ.
CREATE TABLE schedule.availability (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 resource_id UUID NOT NULL,
 weekday SMALLINT NOT NULL CHECK (weekday BETWEEN 1 AND 7),   -- 1 = thứ Hai (ISO)
 start_min SMALLINT NOT NULL CHECK (start_min BETWEEN 0 AND 1439),
 end_min SMALLINT NOT NULL CHECK (end_min BETWEEN 1 AND 1440),
 CHECK (end_min > start_min),
 FOREIGN KEY (tenant_id, resource_id) REFERENCES schedule.resources (tenant_id, id)
);
CREATE INDEX idx_availability_resource ON schedule.availability (tenant_id, resource_id);

CREATE TABLE schedule.events (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 title TEXT NOT NULL CHECK (btrim(title) <> ''),
 starts_at TIMESTAMPTZ NOT NULL,
 ends_at TIMESTAMPTZ NOT NULL,
 team_id UUID REFERENCES core.teams (id),        -- nơi diễn ra; quyết định ai xem / ghi theo phạm vi đội
 status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'cancelled', 'done')),
 source TEXT NOT NULL DEFAULT '',                 -- "<module>.<loại>" khi module khác đặt qua hợp đồng schedule.book
 source_ref TEXT NOT NULL DEFAULT '',
 note TEXT NOT NULL DEFAULT '',
 created_by UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 closed_by UUID,
 closed_at TIMESTAMPTZ,
 cancel_reason TEXT NOT NULL DEFAULT '',
 UNIQUE (tenant_id, id),
 CHECK (ends_at > starts_at),
 CHECK ((source = '') = (source_ref = ''))
);
CREATE UNIQUE INDEX uq_events_source ON schedule.events (tenant_id, source, source_ref) WHERE source <> '' AND status <> 'cancelled';
CREATE INDEX idx_events_time ON schedule.events USING gist (tenant_id, tstzrange(starts_at, ends_at));

-- Một dòng = một nguồn lực trong một sự kiện. Giờ / trạng thái / đội CHÉP từ sự kiện bằng trigger (ràng buộc loại trừ chỉ xét được cột của chính bảng);
-- tên / người đăng nhập chép từ nguồn lực để người được xếp lịch đọc được lịch của mình mà không cần quyền xem danh mục nguồn lực.
CREATE TABLE schedule.bookings (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant(),
 event_id UUID NOT NULL,
 resource_id UUID NOT NULL,
 resource_name TEXT NOT NULL DEFAULT '',
 user_id UUID,
 team_id UUID,
 during TSTZRANGE NOT NULL,
 active BOOLEAN NOT NULL DEFAULT true,
 released_at TIMESTAMPTZ,
 released_by UUID,
 release_reason TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id, event_id) REFERENCES schedule.events (tenant_id, id),
 FOREIGN KEY (tenant_id, resource_id) REFERENCES schedule.resources (tenant_id, id),
 CHECK (NOT isempty(during)),
 CHECK (released_at IS NULL OR NOT active),
 -- KHÔNG TRÙNG LỊCH: một nguồn lực không có hai khoảng giờ giao nhau đang giữ chỗ trong cùng công ty — kể cả hai giao dịch chạy cùng lúc.
 CONSTRAINT no_double_booking EXCLUDE USING gist (tenant_id WITH =, resource_id WITH =, during WITH &&) WHERE (active)
);
CREATE UNIQUE INDEX uq_bookings_open ON schedule.bookings (tenant_id, event_id, resource_id) WHERE released_at IS NULL;
CREATE INDEX idx_bookings_user ON schedule.bookings (tenant_id, user_id, during) WHERE released_at IS NULL;
CREATE INDEX idx_bookings_event ON schedule.bookings (tenant_id, event_id);

-- Trigger chạy bằng quyền chủ (bỏ qua RLS): người đặt lịch có thể không thấy dòng của đội khác, nhưng bản chép phải luôn khớp sự kiện.
CREATE FUNCTION schedule.fill_booking() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE e RECORD; r RECORD;
BEGIN
 SELECT starts_at, ends_at, status, team_id INTO e FROM schedule.events WHERE tenant_id = NEW.tenant_id AND id = NEW.event_id;
 SELECT name, user_id INTO r FROM schedule.resources WHERE tenant_id = NEW.tenant_id AND id = NEW.resource_id;
 IF e IS NULL OR r IS NULL THEN RAISE EXCEPTION 'Không thấy sự kiện / nguồn lực của dòng đặt lịch' USING ERRCODE = 'foreign_key_violation'; END IF;
 NEW.during := tstzrange(e.starts_at, e.ends_at);
 NEW.active := e.status <> 'cancelled' AND NEW.released_at IS NULL;
 NEW.team_id := e.team_id;
 NEW.resource_name := r.name;
 NEW.user_id := r.user_id;
 RETURN NEW;
END $$;
CREATE TRIGGER fill_booking BEFORE INSERT ON schedule.bookings FOR EACH ROW EXECUTE FUNCTION schedule.fill_booking();

-- Dời / huỷ sự kiện ⇒ các dòng đặt chỗ đổi theo, lần lượt theo nguồn lực (thứ tự cố định để hai lần dời đồng thời ít khoá chéo nhau).
CREATE FUNCTION schedule.sync_bookings() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE b RECORD;
BEGIN
 FOR b IN SELECT id FROM schedule.bookings WHERE tenant_id = NEW.tenant_id AND event_id = NEW.id AND released_at IS NULL ORDER BY resource_id LOOP
  UPDATE schedule.bookings SET during = tstzrange(NEW.starts_at, NEW.ends_at), active = NEW.status <> 'cancelled', team_id = NEW.team_id WHERE id = b.id;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER sync_bookings AFTER UPDATE OF starts_at, ends_at, status, team_id ON schedule.events FOR EACH ROW EXECUTE FUNCTION schedule.sync_bookings();

CREATE FUNCTION schedule.sync_resource() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
 UPDATE schedule.bookings SET resource_name = NEW.name, user_id = NEW.user_id WHERE tenant_id = NEW.tenant_id AND resource_id = NEW.id AND released_at IS NULL;
 RETURN NEW;
END $$;
CREATE TRIGGER sync_resource AFTER UPDATE OF name, user_id ON schedule.resources FOR EACH ROW EXECUTE FUNCTION schedule.sync_resource();

CALL core.secure_table('schedule.resources', 'schedule', 'user_id', 'team_id');
CALL core.secure_child('schedule.availability', 'schedule.resources', 'resource_id');
CALL core.secure_table('schedule.events', 'schedule', NULL, 'team_id');
CALL core.secure_table('schedule.bookings', 'schedule', 'user_id', 'team_id');

-- Người được xếp vào một sự kiện luôn thấy sự kiện đó và các nguồn lực ĐANG giữ chỗ cùng sự kiện (phòng, người cùng làm) — kể cả khi phạm vi chỉ là
-- "của mình". Dòng đã nhả chỗ (ai bị đổi ra, vì sao) chỉ người quản lý lịch (phạm vi đội / cả công ty) và chính người bị đổi thấy, qua p_scope.
-- Hàm bỏ qua RLS để không gọi vòng chính sách bookings ↔ events; chỉ trả đúng / sai.
CREATE FUNCTION schedule.i_am_on(event UUID) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 SELECT core.me() IS NOT NULL AND core.scope('schedule') <> 'none' AND EXISTS (
   SELECT 1 FROM schedule.bookings b WHERE b.tenant_id = core.tenant() AND b.event_id = event AND b.user_id = core.me() AND b.released_at IS NULL)
$$;
CREATE POLICY p_my_event ON schedule.events FOR SELECT USING (tenant_id = core.tenant() AND schedule.i_am_on(id));
CREATE POLICY p_my_event ON schedule.bookings FOR SELECT USING (tenant_id = core.tenant() AND released_at IS NULL AND schedule.i_am_on(event_id));

-- Rảnh / bận của các nguồn lực người hỏi ĐÃ thấy: chỉ trả mã nguồn lực đang bận, không lộ sự kiện của đội khác.
CREATE FUNCTION schedule.busy(ids UUID[], span TSTZRANGE, except_event UUID) RETURNS SETOF UUID LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 SELECT DISTINCT b.resource_id FROM schedule.bookings b
 WHERE core.scope('schedule') <> 'none' AND b.tenant_id = core.tenant() AND b.active AND b.resource_id = ANY(ids) AND b.during && span
   AND b.event_id IS DISTINCT FROM except_event
$$;

GRANT SELECT, INSERT, UPDATE ON schedule.resources, schedule.events, schedule.bookings TO barista_app;
GRANT SELECT, INSERT, DELETE ON schedule.availability TO barista_app;
