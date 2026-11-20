-- module: talk
-- 0115 · Trao đổi dùng hằng ngày: tin riêng 1–1, tệp đính kèm, trả lời / cảm xúc / sửa / thu hồi có vết, ghim, tìm trong tin nhắn.
-- CHỈ THÊM (cột cho phép NULL, bảng mới, chính sách mới, nới một ràng buộc): bản cũ chạy song song lúc chuyển vẫn đọc / ghi đúng ⇒ không cần cửa
-- sổ bảo trì (không vào deployment.json). Quyền đọc của mọi bảng mới đi theo luồng (bảng con của talk.messages ⇒ talk.threads ⇒
-- core.subject_visible): mất quyền xem đối tượng là mất luôn tệp / cảm xúc / lịch sử sửa của tin trong đó, ngay lúc đó.

-- Chuẩn hoá chữ để tìm không phụ thuộc gõ dấu (giống kernel/text.fold): bỏ dấu tổ hợp (chữ dạng NFD), đổi chữ có dấu dựng sẵn (NFC) sang
-- không dấu, đ → d, chữ thường. Không dựa vào lower() cho chữ có dấu (DB locale C chỉ hạ chữ ASCII).
CREATE FUNCTION talk.fold(t text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $$ SELECT lower(translate(regexp_replace(t, '[̀-ͯ]', '', 'g'),
        'àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴĐ',
        'aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyydaaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyd')) $$;

ALTER TABLE talk.messages
    ADD COLUMN reply_to bigint REFERENCES talk.messages(id),
    ADD COLUMN has_files boolean NOT NULL DEFAULT false,
    ADD COLUMN edited_at timestamp with time zone,
    ADD COLUMN recalled_at timestamp with time zone,
    ADD COLUMN pinned_at timestamp with time zone,
    ADD COLUMN pinned_by uuid REFERENCES core.users(id),
    ADD COLUMN body_fold text GENERATED ALWAYS AS (talk.fold(body)) STORED;

-- tin chỉ có tệp (không chú thích) được phép thân rỗng; tin chữ vẫn phải có chữ như cũ
ALTER TABLE talk.messages DROP CONSTRAINT messages_body_check;
ALTER TABLE talk.messages ADD CONSTRAINT messages_body_check CHECK (((btrim(body) <> ''::text) OR has_files) AND (length(body) <= 4000));

CREATE INDEX idx_talk_messages_pinned ON talk.messages USING btree (thread_id, pinned_at) WHERE pinned_at IS NOT NULL;

-- Ai xem được NỘI DUNG GỐC của một tin (tệp, lịch sử sửa): tin chưa thu hồi ⇒ ai đọc được luồng; đã thu hồi ⇒ người gửi, hoặc người có quyền
-- "Xem lại tin đã thu hồi" (talk.audit) mà VẪN đọc được luồng, theo PHẠM VI của quyền đó: all = mọi tin thu hồi trong luồng mình đọc được;
-- team = tin của người cùng đội mình (core.my_teams); own = như không có quyền (tin của chính mình vốn đã xem được). Đọc talk.messages bằng RLS
-- người hỏi ⇒ không thấy luồng là false.
CREATE FUNCTION talk.may_see_message(mid bigint) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$ SELECT EXISTS (SELECT 1 FROM talk.messages m WHERE m.id = mid
                         AND (m.recalled_at IS NULL OR m.author_id = core.me() OR core.scope('talk.audit') = 'all'
                              OR (core.scope('talk.audit') = 'team' AND EXISTS (SELECT 1 FROM core.team_members tm
                                                                               WHERE tm.user_id = m.author_id AND tm.team_id = ANY(core.my_teams()))))) $$;

-- ── Tin riêng 1–1: đúng HAI người, mỗi cặp một cuộc; chỉ hai người đó thấy (cả dòng cuộc trò chuyện lẫn tin) ──
CREATE TABLE talk.directs (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    user_a uuid NOT NULL REFERENCES core.users(id),
    user_b uuid NOT NULL REFERENCES core.users(id),
    created_by uuid REFERENCES core.users(id),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT directs_pair_check CHECK (user_a < user_b),
    CONSTRAINT directs_pair_key UNIQUE (tenant_id, user_a, user_b)
);
CALL core.secure_tenant('talk.directs');
CREATE POLICY p_party ON talk.directs AS RESTRICTIVE USING (core.me() IN (user_a, user_b)) WITH CHECK (core.me() IN (user_a, user_b));
CREATE INDEX idx_talk_directs_b ON talk.directs USING btree (tenant_id, user_b);

CREATE FUNCTION talk.subject_visible_direct(id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $_$ SELECT EXISTS (SELECT 1 FROM talk.directs d WHERE d.id = $1 AND core.me() IN (d.user_a, d.user_b)) $_$;

INSERT INTO core.subject_kinds (kind, visible_fn, pack) VALUES ('talk.direct', 'talk.subject_visible_direct', 'talk');

-- ── Tệp đính kèm: cùng cách lưu với file gốc của kho tài liệu (bytea trong Postgres, giới hạn cỡ ở DB), nhưng QUYỀN theo luồng ──
CREATE TABLE talk.attachments (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    message_id bigint NOT NULL REFERENCES talk.messages(id) ON DELETE CASCADE,
    filename text NOT NULL,
    content_type text NOT NULL,
    size_bytes integer NOT NULL,
    sha256 text NOT NULL,
    data bytea NOT NULL,
    created_by uuid NOT NULL REFERENCES core.users(id),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attachments_filename_check CHECK ((btrim(filename) <> ''::text) AND (length(filename) <= 200)),
    CONSTRAINT attachments_size_check CHECK ((size_bytes > 0) AND (octet_length(data) = size_bytes) AND (size_bytes <= 20971520))
);
CALL core.secure_child('talk.attachments', 'talk.messages', 'message_id');
CREATE POLICY p_recalled ON talk.attachments AS RESTRICTIVE FOR SELECT USING (talk.may_see_message(message_id));
CREATE INDEX idx_talk_attachments_message ON talk.attachments USING btree (message_id);

-- ── Cảm xúc: một người một loại một lần trên một tin (bộ chọn ít, do module khai) ──
CREATE TABLE talk.reactions (
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    message_id bigint NOT NULL REFERENCES talk.messages(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
    emoji text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    PRIMARY KEY (message_id, user_id, emoji),
    CONSTRAINT reactions_emoji_check CHECK ((emoji <> ''::text) AND (length(emoji) <= 16))
);
CALL core.secure_child('talk.reactions', 'talk.messages', 'message_id');

-- ── Vết sửa / thu hồi: bản TRƯỚC khi đổi. Không xoá; đọc theo talk.may_see_message (thu hồi ⇒ chỉ người gửi + talk.audit) ──
CREATE TABLE talk.message_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    message_id bigint NOT NULL REFERENCES talk.messages(id) ON DELETE CASCADE,
    kind text NOT NULL,
    body text NOT NULL,
    card jsonb,
    mentions uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    changed_by uuid NOT NULL REFERENCES core.users(id),
    changed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT message_versions_kind_check CHECK ((kind = ANY (ARRAY['edit'::text, 'recall'::text])))
);
CALL core.secure_child('talk.message_versions', 'talk.messages', 'message_id');
CREATE POLICY p_recalled ON talk.message_versions AS RESTRICTIVE FOR SELECT USING (talk.may_see_message(message_id));
CREATE INDEX idx_talk_message_versions_message ON talk.message_versions USING btree (message_id, changed_at);

GRANT SELECT, INSERT ON talk.directs, talk.attachments, talk.message_versions TO barista_app;
GRANT SELECT, INSERT, DELETE ON talk.reactions TO barista_app;
-- tin vẫn chỉ THÊM, trừ đúng các cột sửa / thu hồi / ghim (luồng, người gửi, giờ gửi không bao giờ đổi)
GRANT UPDATE (body, mentions, card, edited_at, recalled_at, pinned_at, pinned_by) ON talk.messages TO barista_app;
GRANT UPDATE (is_owner) ON talk.room_members TO barista_app;
