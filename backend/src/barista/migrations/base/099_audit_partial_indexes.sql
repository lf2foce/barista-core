-- module: core
-- 099 (quét màn hình, 18/11/2026): hai màn nhìn vào nhật ký thao tác bằng điều kiện HIẾM trên bảng LỚN — "Sức khoẻ dữ liệu" / "Tổng quan điều hành" đếm lệnh
-- ``status = 'error'`` 7 ngày qua, "Hành động của trợ lý" lọc ``via = 'agent' OR trace <> ''``. Chỉ có chỉ mục (tenant_id, at): ngay sau một đợt nạp lịch sử (mỗi
-- dòng nạp một dòng nhật ký — ~520.000 dòng trong 7 ngày trên bản demo) Postgres đọc cả 520.000 dòng ra rồi mới lọc ⇒ mỗi truy vấn ~3 giây, màn đầu tiên của
-- giám đốc chậm theo. Chỉ mục từng phần chỉ chứa dòng hiếm ấy (vài trăm dòng) ⇒ vài mili giây, không đổi dữ liệu / cột ⇒ bản cũ chạy song song không bị ảnh hưởng.
-- Điều kiện chỉ mục từng phần PHẢI trùng chữ với điều kiện của truy vấn (``modules/admin.py``) thì Postgres mới dùng.
SET LOCAL lock_timeout = '10s';
CREATE INDEX idx_audit_errors ON core.audit_log (tenant_id, at DESC) WHERE status = 'error';
CREATE INDEX idx_audit_agent_trail ON core.audit_log (tenant_id, at DESC) WHERE via = 'agent' OR trace <> '';
