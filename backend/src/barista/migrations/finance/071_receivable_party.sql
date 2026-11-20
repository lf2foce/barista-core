-- module: acct — lý do: 0177 — BÊN NỢ của khoản phải thu là MỘT ĐỊNH DANH (``party_id``, cùng cách ``acct.payables.party_id``), không còn là chữ tự do:
-- 28 tên chủ đầu tư lặp ở 3.512 dòng, tên chủ nhà ký gửi (0171) sẽ vào cùng cột; hai bên trùng tên gộp làm một khi lọc / đối soát / xuất sổ, đổi tên
-- thì đứt nhóm. Cột ``party`` GIỮ làm tên hiện (bản chụp của ``parties.display_name``, MỘT nơi ghi: ``acct.issue_receivable``). Khoá ngoại KHÔNG trỏ
-- chéo module (parties) — kiểm qua hợp đồng nội bộ ``parties.names`` / ``ensure_counterparty``. Chạy bù dòng cũ KHÔNG ở đây (migration không được ghi
-- bảng parties): lệnh nội bộ ``acct.link_receivable_parties`` (vòng quét) khớp tên → định danh qua ``parties.ensure_counterparty``.
-- Chỉ THÊM cột cho phép trống + chỉ mục từng phần ⇒ bản cũ chạy song song (không đọc cột mới, vẫn ghi ``party`` chữ) ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';

ALTER TABLE acct.receivables ADD COLUMN party_id uuid;
COMMENT ON COLUMN acct.receivables.party_id IS 'Bên nợ = định danh trong parties (không khoá ngoại chéo module, 0177); trống = khoản chưa chạy bù / không có tên bên trả';
COMMENT ON COLUMN acct.receivables.party IS 'Tên hiện của bên nợ — bản chụp parties.display_name lúc ghi, MỘT nơi ghi (acct.issue_receivable); nguồn sự thật là party_id (0177)';
CREATE INDEX idx_receivables_party ON acct.receivables (tenant_id, party_id) WHERE party_id IS NOT NULL;
-- vòng quét chạy bù chọn công ty còn dòng chưa gắn: chỉ mục nhỏ, rỗng dần khi chạy bù xong
CREATE INDEX idx_receivables_party_todo ON acct.receivables (tenant_id) WHERE party_id IS NULL AND party <> '';
