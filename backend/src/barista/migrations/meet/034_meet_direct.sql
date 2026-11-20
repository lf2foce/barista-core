-- module: meet
-- 0114 (bổ sung 07/11/2026): nhà cung cấp "direct" — gọi trực tiếp 1–1 bằng WebRTC ngang hàng trong trình duyệt, không máy chủ media.
-- Máy chủ chỉ CHUYỂN TÍN HIỆU (offer / answer / ICE) giữa đúng hai ghế của một cuộc gọi, kiểm lại quyền xem đối tượng mỗi lần đọc (RLS của
-- meet.calls). Tiếng / hình không bao giờ đi qua đây. Người ngoài (phụ huynh, học viên) vào bằng link hẹp có hạn (kiểu 0064), chỉ đúng cuộc gọi đó.
-- Chỉ THÊM: bản cũ đang chạy không bao giờ tạo cuộc gọi "direct" nên ràng buộc nới cho "direct" không đổi gì với nó.

ALTER TABLE meet.calls DROP CONSTRAINT calls_join_url_check;
-- cuộc gọi trực tiếp không có link ngoài: trang gọi là của chính bản cài (tính lúc đọc từ BARISTA_WEB_URL, đổi tên miền không làm hỏng dòng cũ)
ALTER TABLE meet.calls ADD CONSTRAINT calls_join_url_check CHECK (CASE WHEN provider = 'direct' THEN join_url = '' ELSE join_url ~ '^https://' END);
ALTER TABLE meet.calls ADD CONSTRAINT calls_provider_check CHECK (provider IN ('external', 'direct'));

-- ── Lời mời người ngoài: một dòng = một link hẹp (core.public_links, target "meet.invite", ref = mã lời mời) ──
CREATE TABLE meet.invites (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 call_id UUID NOT NULL REFERENCES meet.calls(id) ON DELETE CASCADE,
 name TEXT NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 120),
 party_id UUID,   -- định danh trong parties (không khoá ngoại chéo module): có thì kiểm sự đồng ý khi người đó là trẻ em (0112)
 created_by UUID NOT NULL REFERENCES core.users(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL,
 revoked_at TIMESTAMPTZ,
 -- không gắn định danh thì người mời phải XÁC NHẬN người được mời đã đủ tuổi thành niên (vết: người mời + lúc mời); trẻ em phải có định danh (0112)
 adult_declared BOOLEAN NOT NULL DEFAULT false,
 CHECK (party_id IS NOT NULL OR adult_declared)
);
-- 1–1: mỗi cuộc gọi tối đa một lời mời còn hiệu lực
CREATE UNIQUE INDEX invites_one_open ON meet.invites(call_id) WHERE revoked_at IS NULL;
CALL core.secure_child('meet.invites', 'meet.calls', 'call_id');
-- phiên của link chỉ thấy chính lời mời của mình (không thấy lời mời khác của cùng cuộc gọi, kể cả lời đã thu hồi)
CREATE POLICY p_link_self ON meet.invites AS RESTRICTIVE FOR SELECT USING (core.link_ref() IS NULL OR id = core.link_ref());
GRANT SELECT, INSERT ON meet.invites TO barista_app;
GRANT UPDATE (revoked_at) ON meet.invites TO barista_app;

-- Cuộc gọi mà phiên link hiện tại được vào. SECURITY DEFINER (chủ là role migration, BYPASSRLS): chính sách của meet.calls gọi hàm này, còn
-- meet.invites lại lấy quyền từ meet.calls — đọc bằng quyền người gọi thì hai chính sách gọi nhau vô tận.
CREATE FUNCTION meet.link_call() RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
 SELECT i.call_id FROM meet.invites i WHERE i.id = core.link_ref() AND i.tenant_id = core.tenant() AND i.revoked_at IS NULL
$$;
REVOKE ALL ON FUNCTION meet.link_call() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION meet.link_call() TO barista_app;

CREATE POLICY p_link ON meet.calls FOR SELECT USING (tenant_id = core.tenant() AND core.link_ref() IS NOT NULL AND id = meet.link_call());
DROP POLICY p_no_link ON meet.calls;
CREATE POLICY p_no_link ON meet.calls AS RESTRICTIVE FOR SELECT USING (core.link_ref() IS NULL OR id = meet.link_call());

-- ── Ghế: đúng hai. "a" là người bắt đầu; "b" là người thứ hai ĐÃ ĐỊNH: thành viên người bắt đầu chọn (lúc gọi, hoặc sau đó), hoặc người ngoài giữ chỗ
-- bằng lời mời. Thành viên khác thấy cuộc gọi KHÔNG tự ngồi được ghế b. Gọi nhóm cần máy chủ media nên không có ghế thứ ba. ──
CREATE TABLE meet.seats (
 call_id UUID NOT NULL REFERENCES meet.calls(id) ON DELETE CASCADE,
 seat TEXT NOT NULL CHECK (seat IN ('a', 'b')),
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 user_id UUID REFERENCES core.users(id),
 invite_id UUID REFERENCES meet.invites(id),
 taken_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (call_id, seat),
 CHECK ((user_id IS NULL) <> (invite_id IS NULL)),
 CHECK (seat = 'b' OR invite_id IS NULL),
 UNIQUE (call_id, user_id), UNIQUE (call_id, invite_id)
);
CALL core.secure_child('meet.seats', 'meet.calls', 'call_id');
GRANT SELECT, INSERT, DELETE ON meet.seats TO barista_app;

-- Ghế của người đang hỏi (thành viên theo core.me(), người ngoài theo link). Đọc bằng quyền người hỏi: không thấy cuộc gọi ⇒ không có ghế.
CREATE FUNCTION meet.my_seat(c uuid) RETURNS text LANGUAGE sql STABLE AS $$
 SELECT s.seat FROM meet.seats s
 WHERE s.call_id = c AND ((core.link_ref() IS NULL AND s.user_id = core.me()) OR (core.link_ref() IS NOT NULL AND s.invite_id = core.link_ref()))
$$;

-- ghế a: chính người bắt đầu; ghế b: chỉ người đang ngồi ghế a đặt (thành viên được chọn, hoặc chỗ của lời mời)
CREATE POLICY p_take ON meet.seats AS RESTRICTIVE FOR INSERT
 WITH CHECK (core.me() IS NOT NULL AND CASE WHEN seat = 'a' THEN user_id = core.me() ELSE meet.my_seat(call_id) = 'a' END);
-- bỏ ghế b: chỗ của lời mời (thu hồi) hoặc người ngồi ghế a trả chỗ của thành viên đã chọn. Ghế a giữ tới hết cuộc gọi.
CREATE POLICY p_free ON meet.seats AS RESTRICTIVE FOR DELETE USING (seat = 'b' AND (invite_id IS NOT NULL OR meet.my_seat(call_id) = 'a'));

-- ── Tín hiệu: chuyển tiếp, không lưu lâu (vòng quét xoá khi cuộc gọi kết thúc / sau 2 giờ). Thân chứa SDP / ICE (có địa chỉ IP) nên chỉ người
-- nhận đọc được. Người nhận ghi theo NGƯỜI (thành viên / lời mời) lúc gửi, không theo ghế: thu hồi rồi mời người khác vào ghế b thì người mới không
-- đọc được thư gửi người cũ. ──
CREATE TABLE meet.signals (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 call_id UUID NOT NULL REFERENCES meet.calls(id) ON DELETE CASCADE,
 from_seat TEXT NOT NULL CHECK (from_seat IN ('a', 'b')),
 to_user UUID REFERENCES core.users(id),
 to_invite UUID REFERENCES meet.invites(id),
 kind TEXT NOT NULL CHECK (kind IN ('hello', 'description', 'candidate', 'bye')),
 body JSONB NOT NULL CHECK (jsonb_typeof(body) = 'object' AND octet_length(body::text) <= 16000),
 at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK ((to_user IS NULL) <> (to_invite IS NULL))
);
CREATE INDEX signals_inbox ON meet.signals(call_id, id);
CREATE INDEX signals_age ON meet.signals(at);
CALL core.secure_child('meet.signals', 'meet.calls', 'call_id');
CREATE POLICY p_inbox ON meet.signals AS RESTRICTIVE FOR SELECT USING (core.is_system()
 OR (core.link_ref() IS NULL AND to_user = core.me()) OR (core.link_ref() IS NOT NULL AND to_invite = core.link_ref()));
-- gửi từ ghế của mình, tới đúng người đang ngồi ghế kia
CREATE POLICY p_sender ON meet.signals AS RESTRICTIVE FOR INSERT WITH CHECK (from_seat = meet.my_seat(call_id) AND EXISTS (
 SELECT 1 FROM meet.seats s WHERE s.call_id = signals.call_id AND s.seat <> signals.from_seat
 AND (s.user_id = signals.to_user OR s.invite_id = signals.to_invite)));
GRANT SELECT, INSERT, DELETE ON meet.signals TO barista_app;

-- ── Có mặt theo MÁY BÁO VỀ: mỗi bên tự báo nối được / mất nối / kết thúc của chính mình. Giờ tính là giờ máy chủ nhận (at); giờ máy báo
-- (client_at) chỉ được dùng để RÚT NGẮN một khoảng khi máy báo mất nối muộn (khoá màn hình), không bao giờ kéo dài. Chỉ thêm. ──
CREATE TABLE meet.presence (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 tenant_id UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
 call_id UUID NOT NULL REFERENCES meet.calls(id) ON DELETE CASCADE,
 seat TEXT NOT NULL CHECK (seat IN ('a', 'b')),
 user_id UUID REFERENCES core.users(id),
 invite_id UUID REFERENCES meet.invites(id),
 event TEXT NOT NULL CHECK (event IN ('connected', 'disconnected', 'ended')),
 source TEXT NOT NULL DEFAULT 'client' CHECK (source = 'client'),
 detail TEXT NOT NULL DEFAULT '' CHECK (length(detail) <= 40),
 client_at TIMESTAMPTZ,
 at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK ((user_id IS NULL) <> (invite_id IS NULL))
);
CREATE INDEX presence_call ON meet.presence(call_id, id);
CALL core.secure_child('meet.presence', 'meet.calls', 'call_id');
-- chỉ báo cho chính mình, đúng ghế của mình
CREATE POLICY p_self ON meet.presence AS RESTRICTIVE FOR INSERT
 WITH CHECK (seat = meet.my_seat(call_id) AND CASE WHEN core.link_ref() IS NULL THEN user_id = core.me() AND invite_id IS NULL
                                                    ELSE invite_id = core.link_ref() AND user_id IS NULL END);
GRANT SELECT, INSERT ON meet.presence TO barista_app;

-- Đánh thức luồng tín hiệu đang mở (kernel/live.py, kênh chung "barista_signal", tin chỉ mang mã cuộc gọi — không mang nội dung).
CREATE FUNCTION meet.wake() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME = 'calls' THEN
  PERFORM pg_notify('barista_signal', 'meet:' || NEW.tenant_id::text || ':' || NEW.id::text);
 ELSE
  PERFORM pg_notify('barista_signal', 'meet:' || NEW.tenant_id::text || ':' || NEW.call_id::text);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER signals_wake AFTER INSERT ON meet.signals FOR EACH ROW EXECUTE FUNCTION meet.wake();
-- đổi ghế (chọn / trả người thứ hai, thu hồi lời mời) ⇒ kênh đang mở đọc lại ngay: người bị bỏ nhận "gone", người còn lại nhận "peer"
CREATE FUNCTION meet.wake_seat() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
  PERFORM pg_notify('barista_signal', 'meet:' || OLD.tenant_id::text || ':' || OLD.call_id::text);
  RETURN OLD;
 END IF;
 PERFORM pg_notify('barista_signal', 'meet:' || NEW.tenant_id::text || ':' || NEW.call_id::text);
 RETURN NEW;
END $$;
CREATE TRIGGER seats_wake AFTER INSERT OR DELETE ON meet.seats FOR EACH ROW EXECUTE FUNCTION meet.wake_seat();
CREATE TRIGGER invites_wake AFTER UPDATE OF revoked_at ON meet.invites FOR EACH ROW EXECUTE FUNCTION meet.wake_seat();
CREATE TRIGGER calls_wake AFTER UPDATE OF status ON meet.calls FOR EACH ROW WHEN (NEW.provider = 'direct' AND OLD.status IS DISTINCT FROM NEW.status)
 EXECUTE FUNCTION meet.wake();
