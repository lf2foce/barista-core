-- module: core
-- 0163: MỨC DÙNG theo gói. Nhật ký thao tác (core.audit_log) chỉ có LỆNH — người chỉ xem (giám đốc đọc báo cáo, kế toán duyệt xem) không để lại dấu, nên đếm
-- "người đang dùng gói" chỉ từ nhật ký là thiếu. Bộ đếm này ghi LƯỢT ĐỌC thành công: (công ty, người, gói, ngày, kênh) → số lượt — chỉ số đếm, không nội dung,
-- không tên truy vấn. Executor gom trong bộ nhớ rồi ghi từng lô (kernel/usage.py): không thêm câu SQL nào vào đường đọc, ghi hỏng ⇒ bỏ lô, không ảnh hưởng truy vấn.
-- ``via``: human = người bấm; agent = trợ lý làm thay (tách để trợ lý không làm phồng số người).
-- Bảng mới, chỉ thêm ⇒ bản cũ chạy song song không ảnh hưởng. Pack '' = module không thuộc gói nào (hiện "Lõi").
CREATE TABLE core.usage_reads (
    tenant_id uuid NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    day date NOT NULL,
    pack text NOT NULL DEFAULT '' CHECK (length(pack) <= 41),
    user_id uuid NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
    via text NOT NULL CHECK (via IN ('human', 'agent')),
    queries integer NOT NULL DEFAULT 0 CHECK (queries >= 0),
    PRIMARY KEY (tenant_id, day, pack, user_id, via)
);
CALL core.secure_tenant('core.usage_reads');
GRANT SELECT ON core.usage_reads TO barista_app;
