-- module: core
-- 0087 (cập nhật 18/11/2026) — GẤP CHỮ VIỆT cho tìm nhanh ⌘K và ô lọc danh sách: "nguyen thi" khớp "Nguyễn Thị", "DANG" khớp "Đặng".
-- ``core.vn_fold(t)`` = chữ thường + bỏ dấu (extension ``unaccent``, có trong contrib của mọi bản Postgres dùng; "trusted" ⇒ chủ DB tạo được, không cần
-- superuser). Khớp từng TỪ bằng ``LIKE`` trên kết quả (``kernel/search.SearchTerms``) — Python gấp từ khoá bằng ``kernel/text.fold``, cùng kết quả với
-- chữ Việt (đ → d, dấu dựng sẵn lẫn dấu tổ hợp).
--
-- Vì sao STABLE, không IMMUTABLE, và không chỉ mục trigram (đo trên bản sao dữ liệu thật 18/11/2026, ghi ở ADR 0087 mục cập nhật):
--  * ``unaccent(regdictionary, text)`` là STABLE. Hàm bọc khai STABLE thì planner NHÚNG thẳng biểu thức vào câu truy vấn (~0,25 µs/dòng); khai
--    IMMUTABLE thì không nhúng được (gọi hàm từng dòng, ~1,2 µs/dòng — chậm 5 lần) — và cũng không mua được gì: xem ý dưới.
--  * Chỉ mục GIN trigram trên biểu thức gấp KHÔNG được dùng dưới RLS: ``LIKE`` / ``%`` của pg_trgm không LEAKPROOF nên Postgres buộc kiểm chính sách
--    RLS trước, điều kiện tìm sau (EXPLAIN bằng barista_app ra Seq Scan dù có chỉ mục). Đánh dấu LEAKPROOF cần superuser — chủ DB của bản cài không có.
--    Vì vậy KHÔNG tạo chỉ mục trigram (tốn ghi, không ai đọc); cái giới hạn tốc độ là quét RLS theo công ty, như mọi màn danh sách.
--  * Tên đủ schema (``public.unaccent`` + từ điển ``public.unaccent``) ⇒ không phụ thuộc search_path của phiên.
-- CHỈ THÊM (extension + hàm mới) ⇒ bản cũ chạy song song không vướng; không cần cửa sổ bảo trì.

CREATE EXTENSION IF NOT EXISTS unaccent SCHEMA public;

CREATE FUNCTION core.vn_fold(t text) RETURNS text
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT lower(public.unaccent('public.unaccent'::regdictionary, t)) $$;

COMMENT ON FUNCTION core.vn_fold(text) IS 'Chữ thường, bỏ dấu (đ → d) để tìm không phụ thuộc gõ dấu — ADR 0087; khớp với kernel/text.fold cho chữ Việt';
