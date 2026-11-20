-- module: core — lý do: 0144 (chốt 09/11) — MỘT công tắc công ty cho tự chủ theo LỚP: bật ⇒ mọi lệnh rủi ro thấp ĐẢO ĐƯỢC (promotable + undone_by) trợ lý
-- làm ngay trong lượt có người, kèm Hoàn tác; trần số lần / ngày riêng cho công tắc. Mặc định TẮT; bật = nới ⇒ chuỗi "access_change" (admin.set_agent_trust).
-- Preset (Thận trọng / Cân bằng / Toàn quyền) chỉ là giao diện ghi vào cột này + core.agent_policies — không máy móc mới. Chỉ thêm cột có mặc định.
ALTER TABLE core.agent_trust
    ADD COLUMN autorun_low_risk boolean NOT NULL DEFAULT false,
    ADD COLUMN autorun_daily_cap integer NOT NULL DEFAULT 20 CHECK (autorun_daily_cap >= 1);
