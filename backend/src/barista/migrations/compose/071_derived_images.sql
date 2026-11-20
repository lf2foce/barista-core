-- module: compose — lý do: 0224 §8 (14/11/2026) — ảnh TRÍCH từ tài liệu PDF của dự án (tờ giới thiệu chủ đầu tư, mặt bằng…) để đưa vào tờ giới thiệu khi kho chưa có ảnh riêng.
-- Mỗi dòng = một ảnh trích ra, cất ở kho tệp (0122, ``core.files``) và NHỚ tài liệu nguồn (``document_id`` — không khoá ngoại: khoá ngoại không trỏ chéo module).
-- Quyền KHÔNG nằm ở bảng này: khi dựng / xuất, ảnh chỉ được dùng nếu người soạn đọc được tài liệu nguồn (``kb.documents`` dưới RLS kệ tài liệu của họ).
-- Bảng chỉ là bộ nhớ đệm — xoá hết dòng thì lần dựng sau trích lại. CHỈ THÊM ⇒ không cần cửa sổ bảo trì.
CREATE TABLE compose.derived_images (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    document_id uuid NOT NULL,                                  -- tài liệu nguồn (kb.documents) — không khoá ngoại chéo module
    page        int NOT NULL CHECK (page >= 1),
    idx         int NOT NULL CHECK (idx >= 0),                  -- thứ tự trong trang
    width       int NOT NULL CHECK (width > 0),
    height      int NOT NULL CHECK (height > 0),
    sha256      text NOT NULL,                                  -- băm nội dung ảnh đã chuẩn hoá: một ảnh lặp ở nhiều trang chỉ giữ một lần
    filename    text NOT NULL,
    file_id     uuid NOT NULL UNIQUE REFERENCES core.files(id),
    extracted_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, document_id, sha256)
);
CREATE INDEX idx_compose_derived_doc ON compose.derived_images (tenant_id, document_id, page, idx);
CALL core.secure_tenant('compose.derived_images');
GRANT SELECT, INSERT, DELETE ON compose.derived_images TO barista_app;
