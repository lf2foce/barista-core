-- module: leadflow — lý do: 0140 — nạp lead từ app từ xa trả {party_id, lead_id}; lần gửi lặp (cùng nhà cung cấp + mã ngoài) phải trả CÙNG
-- party_id mà không đọc bảng của module khách (ranh giới module) ⇒ giữ party_id cạnh lead_id ở sổ mã ngoài.
-- CHỈ THÊM: cột cho phép NULL (dòng cũ / bản cũ chạy song song không ghi cột này — lần lặp của dòng cũ trả party_id null) ⇒ không cần cửa sổ bảo trì.
ALTER TABLE leadflow.external_refs ADD COLUMN party_id uuid;
COMMENT ON COLUMN leadflow.external_refs.party_id IS 'Người (danh bạ) của lead lúc nạp — trả lại cho bên gửi khi gửi lặp (0140)';
