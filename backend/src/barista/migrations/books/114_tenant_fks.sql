-- module: books — lý do: ADR 0258 — khoá ngoại giữa hai bảng cùng mang tenant_id phải mang cả tenant_id (thủ tục core.tenant_fks của base/114): DB tự giữ dòng con
-- và dòng cha cùng công ty. Giữ tên, hành vi khi xoá và tính trì hoãn; chỉ siết ràng buộc ⇒ không cần bảo trì. Chạy lại không sao.
SET LOCAL lock_timeout = '10s';
CALL core.tenant_fks(ARRAY['books']);
