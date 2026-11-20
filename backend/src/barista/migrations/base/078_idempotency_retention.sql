-- module: core
-- 0177 §2 — KHOÁ CHỐNG LẶP chỉ có ích trong CỬA SỔ phát lại (client gửi lại cùng khoá khi mất phản hồi; nhập lại cùng file) — không giữ mãi. Cửa sổ là DỮ LIỆU của từng
-- công ty (mặc định 90 ngày, cùng hạn giữ nội dung dòng nhập — 0174 §4; trống = giữ mãi). Vòng quét ``core.purge_idempotency`` xoá khoá quá cửa sổ theo lô. KHÔNG chạm
-- nhật ký thao tác (``core.audit_log``) và khoá chống lặp của SỔ CÁI (``ledger.journals.idempotency_key`` — mãi mãi, bất biến 4).
-- CHỈ THÊM: cột có mặc định + quyền xoá đúng bảng khoá (RLS vẫn khoá theo công ty) + chỉ mục BRIN nhỏ (bảng chỉ thêm, ``created_at`` tăng theo dòng ghi) ⇒ bản cũ
-- chạy song song không đọc cột mới, không vướng quyền, không bảo trì.

SET LOCAL lock_timeout = '10s';

ALTER TABLE core.tenants ADD COLUMN idempotency_keep_days integer DEFAULT 90
    CHECK (idempotency_keep_days IS NULL OR idempotency_keep_days BETWEEN 7 AND 3650);
COMMENT ON COLUMN core.tenants.idempotency_keep_days IS 'Cửa sổ phát lại của khoá chống lặp (ngày, 0177); NULL = giữ mãi. Quá cửa sổ ⇒ vòng quét xoá khoá; sổ cái / nhật ký thao tác không bị xoá';
GRANT UPDATE (idempotency_keep_days) ON core.tenants TO barista_app;

GRANT DELETE ON core.idempotency TO barista_app;
CREATE INDEX idx_idempotency_created ON core.idempotency USING brin (created_at);
