-- module: crm
-- 0185 — DỮ LIỆU NẠP TỪ HỆ KHÁC LÀ THAM CHIẾU. ``crm.leads.loaded_at``: lead tạo trong một lần nạp lịch sử (``ctx.historical``) — không hẹn "gọi lại hôm nay" lúc tạo;
-- lead CHƯA có trạng thái chia mà người phụ trách đã nghỉ thì vòng quét chia lead không tự chuyển (``crm.departed_leads(skip_loaded)``). Có trạng thái chia thì
-- trạng thái chia quyết (``leadflow.lead_state.imported_at`` — crm/079).
-- CHỈ THÊM: cột cho phép trống ⇒ bản cũ chạy song song không vướng, không bảo trì.

SET LOCAL lock_timeout = '10s';

ALTER TABLE crm.leads ADD COLUMN loaded_at timestamptz;
COMMENT ON COLUMN crm.leads.loaded_at IS 'Lead tạo trong một lần nạp lịch sử từ hệ khác (0185) — tham chiếu: không hẹn gọi lại lúc tạo, vòng quét không tự chuyển khi người phụ trách đã nghỉ. NULL = lead tạo ở Barista';
