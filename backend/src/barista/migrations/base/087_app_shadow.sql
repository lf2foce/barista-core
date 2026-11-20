-- module: core
-- 087 (0207): APP CHỈ GHI NHẬN — người vận hành bật cho một app từ xa ⇒ mọi lệnh app gửi chạy như NẠP LỊCH SỬ (0185): dữ liệu vào đủ (lead, chặng…),
-- luật chia vẫn ghi người sẽ nhận, nhưng không báo ai, không tính hạn, không phát webhook — để chạy song song hệ đang dùng mà không làm phiền người.
-- Không phải ô đầu vào của app (app không tự bật / tắt được): chỉ cửa nhà vận hành đổi, có ghi người + lý do. Cột có mặc định ⇒ không cần dừng dịch vụ.
ALTER TABLE core.remote_apps ADD COLUMN shadow boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN core.remote_apps.shadow IS 'Chỉ ghi nhận (0207): lệnh của app chạy như nạp lịch sử — không báo, không tính hạn, không webhook';
GRANT UPDATE (shadow) ON core.remote_apps TO barista_app;   -- chỉ lệnh cửa nhà vận hành ghi (connect.operator_shadow, tài nguyên core.operator)
