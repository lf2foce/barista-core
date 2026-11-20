-- module: core — lý do: 0144 mức tự chủ đặt sẵn (Thận trọng / Cân bằng / Toàn quyền): lệnh mới admin.set_agent_preset ghi công tắc + chính sách
-- trong MỘT giao dịch ⇒ nới cả mức = MỘT phiếu chủ tài khoản. Luật duyệt cho lệnh này ở MỌI công ty đang có chuỗi "access_change" (deploy chỉ chạy
-- migration; công ty mới nhận luật qua admin.install_defaults — core_ops). Công ty đã cố ý bỏ luật (có nhật ký cài) giữ nguyên; lệnh tự từ chối
-- khi nới mà không có luật (fail closed — admin._rule_required). Chỉ thêm dòng, không đổi bảng.
INSERT INTO core.approval_rules (tenant_id, command, chain)
SELECT c.tenant_id, 'admin.set_agent_preset', 'access_change' FROM core.approval_chains c
WHERE c.key = 'access_change'
  AND NOT EXISTS (SELECT 1 FROM core.pack_installs i WHERE i.tenant_id = c.tenant_id AND i.kind = 'rule' AND i.key = 'admin.set_agent_preset')
ON CONFLICT DO NOTHING;
INSERT INTO core.pack_installs (tenant_id, kind, key, pack, version)
SELECT c.tenant_id, 'rule', 'admin.set_agent_preset', 'base', NULL FROM core.approval_chains c
WHERE c.key = 'access_change' ON CONFLICT DO NOTHING;
