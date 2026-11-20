-- module: compose — lý do: 0224 — tờ giới thiệu có BỐ CỤC thật + link chia sẻ có đếm lượt xem.
-- (1) ``layout``: trang mẫu chọn cho bản soạn ({} = bố cục cũ, xuất như trước; {"template": "<khoá>"} = bố cục theo trang mẫu — mẫu là dữ liệu trong code,
--     khối chọn chỗ đứng bằng trường ``slot`` nằm TRONG ``blocks``). Cột có mặc định ⇒ bản cũ còn chạy lúc chuyển không đọc / không ghi cột này vẫn chạy.
-- (2) Tệp xuất thêm dạng ``html`` (trang web một tệp, mang theo ảnh + màu thương hiệu).
-- (3) ``compose.shares``: một lần chia sẻ = ảnh chụp trang HTML lúc chia sẻ + hạn + đếm lượt xem; link công khai (0064) trỏ vào dòng này
--     (``core.public_links`` target ``compose.share``). Khách mở link chỉ thấy đúng dòng này (RLS ``p_link``), không thấy bản soạn / kho tài liệu.
--     Thân trang nằm ở CỘT ``html`` (không phải kho tệp 0122): sổ tệp ``core.files`` đóng với người qua link (``p_no_link`` — link chỉ được mở đúng một dòng của
--     module). Trần 8 MB (ảnh đã thu nhỏ khi dựng trang) — ngoại lệ có chủ đích của 0224, xem §5.
-- CHỈ THÊM ⇒ không cần cửa sổ bảo trì.
ALTER TABLE compose.sessions ADD COLUMN layout jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(layout) = 'object');
ALTER TABLE compose.versions ADD COLUMN layout jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(layout) = 'object');

ALTER TABLE compose.exports DROP CONSTRAINT IF EXISTS exports_format_check;   -- IF EXISTS: chạy lại an toàn
ALTER TABLE compose.exports ADD CONSTRAINT exports_format_check CHECK (format IN ('docx', 'pdf', 'pptx', 'html'));

CREATE TABLE compose.shares (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    session_id      uuid NOT NULL REFERENCES compose.sessions(id) ON DELETE CASCADE,
    version         int NOT NULL,                                  -- phiên bản bản soạn lúc chụp
    title           text NOT NULL,
    recipient       text NOT NULL DEFAULT '' CHECK (length(recipient) <= 120),   -- nhãn người nhận (người gửi tự ghi, vd tên khách) — chỉ để nhớ link này gửi ai
    html            text NOT NULL CHECK (length(html) <= 8000000),   -- trang HTML đã chụp (một tệp, không script)
    expires_at      timestamptz NOT NULL,
    views           int NOT NULL DEFAULT 0 CHECK (views >= 0),
    first_viewed_at timestamptz,
    last_viewed_at  timestamptz,
    revoked_at      timestamptz,                                   -- người chia sẻ thu hồi (link công khai cũng bị thu hồi ở core.public_links)
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_compose_shares_session ON compose.shares (session_id, created_at DESC);
CALL core.secure_child('compose.shares', 'compose.sessions', 'session_id');
-- người cầm link (via="link"): chỉ đọc + đếm lượt đúng dòng của link, không gì khác
CREATE POLICY p_link ON compose.shares FOR SELECT
    USING (tenant_id = (SELECT core.tenant()) AND (SELECT core.link_ref()) IS NOT NULL AND id = (SELECT core.link_ref()));
CREATE POLICY p_link_count ON compose.shares FOR UPDATE
    USING (tenant_id = (SELECT core.tenant()) AND (SELECT core.link_ref()) IS NOT NULL AND id = (SELECT core.link_ref()))
    WITH CHECK (tenant_id = (SELECT core.tenant()) AND (SELECT core.link_ref()) IS NOT NULL AND id = (SELECT core.link_ref()));
GRANT SELECT, INSERT, DELETE ON compose.shares TO barista_app;
GRANT UPDATE (views, first_viewed_at, last_viewed_at, revoked_at) ON compose.shares TO barista_app;
