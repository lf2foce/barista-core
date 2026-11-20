-- module: crm
-- ADR 0246 — LÝ DO THUA LÀ DANH MỤC CỦA CÔNG TY: người đánh dấu mất khách CHỌN một lý do trong danh sách (ADR 0225: chọn, không gõ), ghi chú ngắn vẫn tuỳ chọn.
-- Lý do thành chiều đếm được ("vì sao mình mất khách") thay vì chữ tự do mỗi người viết một kiểu. Bộ mặc định trung tính đi kèm gói (Pack.records), công ty
-- đổi tên / thêm / tắt (không xoá — lead cũ còn trỏ tới).
-- ``crm.leads.lost_reason_key`` = lý do của LẦN MẤT GẦN NHẤT; giữ nguyên khi lead mở lại (khách quay lại) — lịch sử từng lần mất ở crm.stage_events.data.
-- Chữ ``lost_reason`` cũ GIỮ NGUYÊN (không ánh xạ ngược, không xoá): lead mất trước danh mục vẫn đọc được lý do người đã gõ.
-- CHỈ THÊM: bảng mới, cột cho phép NULL, khoá ngoại MATCH SIMPLE (cột trống ⇒ không kiểm) ⇒ bản cũ chạy song song không đọc / ghi phần mới, không bảo trì.

SET LOCAL lock_timeout = '10s';

CREATE TABLE crm.lost_reasons (
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    key text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]{1,39}$'),
    label text NOT NULL CHECK (btrim(label) <> '' AND length(label) <= 80),
    sort smallint NOT NULL DEFAULT 100,
    needs_note boolean NOT NULL DEFAULT false,      -- vd "Lý do khác": chọn lý do này thì phải ghi chú vài chữ
    active boolean NOT NULL DEFAULT true,
    updated_by uuid REFERENCES core.users(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);
CALL core.secure_tenant('crm.lost_reasons');
GRANT SELECT, INSERT ON crm.lost_reasons TO barista_app;
GRANT UPDATE (label, sort, needs_note, active, updated_by, updated_at) ON crm.lost_reasons TO barista_app;   -- mã không đổi, không xoá (chỉ tắt)
COMMENT ON TABLE crm.lost_reasons IS 'Lý do mất khách do công ty đặt (ADR 0246): chọn khi đánh dấu lead mất; bộ mặc định đi kèm gói (Pack.records)';

ALTER TABLE crm.leads ADD COLUMN lost_reason_key text;
ALTER TABLE crm.leads ADD CONSTRAINT leads_lost_reason_key_fkey FOREIGN KEY (tenant_id, lost_reason_key)
    REFERENCES crm.lost_reasons (tenant_id, key);   -- mọi dòng đang có là NULL ⇒ kiểm tức thì
COMMENT ON COLUMN crm.leads.lost_reason_key IS 'Lý do (danh mục crm.lost_reasons) của lần mất GẦN NHẤT — giữ khi lead mở lại; lead đang mất hay không xem ở stage (ADR 0246)';
