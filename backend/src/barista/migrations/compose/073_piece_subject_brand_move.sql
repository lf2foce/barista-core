-- module: compose, admin, core — lý do: ADR 0249 lát 2 (chép nhận diện sang admin.brand, giao tệp logo ở core.files) — ẤN PHẨM GẮN HỒ SƠ + nhận diện thương hiệu về cài đặt công ty.
-- (1) Ấn phẩm ghi VỀ hồ sơ nào (``subject_kind`` + ``subject_id`` — loại do module / gói chủ hồ sơ khai, như ``core.field_changes``) và cho khách nào
--     (``customer_kind`` + ``customer_id``); ``recipe`` = công thức đã dựng ({kind, subject}) để "làm bản cho khách khác" dựng lại đúng cách; ``source_id`` =
--     ấn phẩm gốc khi là bản làm lại. KHÔNG khoá ngoại chéo module (hồ sơ / khách ở module khác) — quyền xem kiểm lúc dựng bằng quyền người soạn.
-- (2) Dòng chia sẻ chép khách + hồ sơ của ấn phẩm: người cầm link chỉ đọc được đúng dòng chia sẻ (``p_link``) — lượt mở báo về khách mà không mở bản soạn.
-- (3) Nhận diện thương hiệu: chép ``compose.brand`` sang ``admin.brand`` (bảng của cài đặt công ty, base/107 — chạy trước vì gói nền nạp trước), giao tệp logo cho
--     module Quản trị (sổ tệp 0122: module tạo tệp mới mở được — đổi ``core.files.module`` của ĐÚNG các tệp logo), rồi KHOÁ GHI bảng cũ (chỉ còn đọc):
--     bản cũ còn chạy lúc chuyển đọc được bảng cũ (logo không mở được nữa ⇒ tệp xuất không logo trong vài phút), sửa nhận diện ở bản cũ bị từ chối thay vì ghi vào
--     chỗ không ai đọc. Bảng cũ chưa xoá: còn trỏ tệp logo (khoá ngoại — tệp không bị vòng dọn coi là mồ côi khi hai bảng cùng trỏ); xoá ở lần dọn sau khi
--     không bản nào đọc nó.
-- CHỈ THÊM cột có mặc định / bảng cũ chỉ đọc ⇒ không cần cửa sổ bảo trì.
ALTER TABLE compose.sessions
    ADD COLUMN subject_kind   text NOT NULL DEFAULT '' CHECK (subject_kind = '' OR subject_kind ~ '^[a-z_]+\.[a-z_]+$'),
    ADD COLUMN subject_id     uuid,
    ADD COLUMN subject_label  text NOT NULL DEFAULT '' CHECK (length(subject_label) <= 200),
    ADD COLUMN customer_kind  text NOT NULL DEFAULT '' CHECK (customer_kind = '' OR customer_kind ~ '^[a-z_]+\.[a-z_]+$'),
    ADD COLUMN customer_id    uuid,
    ADD COLUMN customer_label text NOT NULL DEFAULT '' CHECK (length(customer_label) <= 200),
    ADD COLUMN recipe         jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(recipe) = 'object'),
    ADD COLUMN source_id      uuid REFERENCES compose.sessions(id) ON DELETE SET NULL;
CREATE INDEX idx_compose_sessions_subject ON compose.sessions (tenant_id, subject_kind, subject_id) WHERE subject_id IS NOT NULL;
CREATE INDEX idx_compose_sessions_customer ON compose.sessions (tenant_id, customer_kind, customer_id) WHERE customer_id IS NOT NULL;

ALTER TABLE compose.shares
    ADD COLUMN customer_kind text NOT NULL DEFAULT '',
    ADD COLUMN customer_id   uuid,
    ADD COLUMN subject_kind  text NOT NULL DEFAULT '',
    ADD COLUMN subject_id    uuid;

INSERT INTO admin.brand (tenant_id, primary_color, font, footer, logo_file_id, logo_filename, updated_at)
SELECT tenant_id, primary_color, font, footer, logo_file_id, left(logo_filename, 120), updated_at FROM compose.brand
ON CONFLICT (tenant_id) DO NOTHING;
UPDATE core.files SET module = 'admin'
 WHERE module = 'compose' AND id IN (SELECT logo_file_id FROM compose.brand WHERE logo_file_id IS NOT NULL);
REVOKE INSERT, UPDATE ON compose.brand FROM barista_app;
COMMENT ON TABLE compose.brand IS 'ĐÃ DỜI (ADR 0249): nhận diện thương hiệu ở admin.brand — bảng này chỉ còn đọc, xoá ở lần dọn sau';
