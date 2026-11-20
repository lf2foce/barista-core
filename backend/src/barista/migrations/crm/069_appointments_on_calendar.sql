-- module: crm, schedule — lý do: 0158 — GIỜ + NGƯỜI của lịch hẹn chuyển sang lịch chung (một nguồn): mỗi hẹn đang có ⇒ một lịch nguồn 'crm.appointment' (sale phụ trách là người tham dự, khách là bên ngoài, riêng tư) + loại lịch của hẹn khách. Gói Lịch luôn bật (0157). Chạy lại không tạo thêm (NOT EXISTS / ON CONFLICT).
-- CHỈ THÊM: không đổi / xoá cột của crm.appointments — ``at`` ở lại làm BẢN CHÉP giờ bắt đầu (bản cũ còn chạy lúc chuyển đọc nó; code mới ghi khi đặt /
-- dời và khi nghe lịch dời, không đọc). Chạy SAU migration 070 của gói Lịch (gói Khách khai bắt buộc gói Lịch ⇒ nạp sau).
SET LOCAL lock_timeout = '10s';

COMMENT ON COLUMN crm.appointments.at IS 'Bản chép giờ bắt đầu từ lịch chung (schedule.events nguồn crm.appointment — 0158): code đọc giờ từ lịch, cột này chỉ để bản cũ chạy lúc chuyển';

-- Loại lịch của hẹn khách (Pack(records=…) của gói Khách chỉ chạy khi cài mới) — công ty đã có loại cùng mã thì giữ của công ty.
INSERT INTO schedule.kinds (tenant_id, key, label, sort)
SELECT tp.tenant_id, k.key, k.label, k.sort FROM core.tenant_packs tp
CROSS JOIN (VALUES ('customer_meeting', 'Hẹn khách', 20), ('site_visit', 'Tham quan', 30), ('callback', 'Hẹn gọi', 35)) AS k (key, label, sort)
WHERE tp.pack = 'crm'
ON CONFLICT (tenant_id, key) DO NOTHING;

-- Mỗi hẹn ⇒ một lịch. Độ dài mặc định theo loại (gặp 60 phút, tham quan 90, gọi 15) — hẹn cũ không lưu giờ kết thúc. Trạng thái: sắp tới ⇒ đã đặt;
-- huỷ ⇒ đã huỷ; đã gặp / khách không tới ⇒ đã xong (giờ đó đã dùng). Không giữ nguồn lực nào (hẹn cũ không có phòng) ⇒ không thể vướng ràng buộc trùng.
INSERT INTO schedule.events (tenant_id, title, starts_at, ends_at, team_id, status, source, source_ref, created_by, created_at, closed_at, kind, visibility)
SELECT a.tenant_id,
       CASE a.kind WHEN 'meet' THEN 'Hẹn gặp' WHEN 'visit' THEN 'Tham quan' ELSE 'Hẹn gọi' END || ' — ' || c.name,
       a.at,
       a.at + CASE a.kind WHEN 'visit' THEN interval '90 minutes' WHEN 'call' THEN interval '15 minutes' ELSE interval '60 minutes' END,
       l.team_id,
       CASE a.status WHEN 'scheduled' THEN 'booked' WHEN 'cancelled' THEN 'cancelled' ELSE 'done' END,
       'crm.appointment', a.id::text, a.created_by, a.created_at, a.closed_at,
       CASE a.kind WHEN 'meet' THEN 'customer_meeting' WHEN 'visit' THEN 'site_visit' ELSE 'callback' END,
       'private'
FROM crm.appointments a JOIN crm.leads l ON l.tenant_id = a.tenant_id AND l.id = a.lead_id JOIN crm.customers c ON c.tenant_id = l.tenant_id AND c.id = l.customer_id
WHERE NOT EXISTS (SELECT 1 FROM schedule.events e WHERE e.tenant_id = a.tenant_id AND e.source = 'crm.appointment' AND e.source_ref = a.id::text);

-- Người tham dự = sale đang phụ trách lead (đã nhận — hẹn của chính họ); không có người phụ trách ⇒ người đặt hẹn.
INSERT INTO schedule.attendees (tenant_id, event_id, user_id, rsvp, responded_at)
SELECT e.tenant_id, e.id, coalesce(l.owner_id, a.created_by), 'accepted', e.created_at
FROM schedule.events e JOIN crm.appointments a ON a.tenant_id = e.tenant_id AND a.id::text = e.source_ref
JOIN crm.leads l ON l.tenant_id = a.tenant_id AND l.id = a.lead_id
WHERE e.source = 'crm.appointment' AND coalesce(l.owner_id, a.created_by) IS NOT NULL
ON CONFLICT (tenant_id, event_id, user_id) DO NOTHING;

-- Bên ngoài = định danh của khách (parties — không khoá ngoại chéo module).
INSERT INTO schedule.event_parties (tenant_id, event_id, party_id)
SELECT e.tenant_id, e.id, c.party_id
FROM schedule.events e JOIN crm.appointments a ON a.tenant_id = e.tenant_id AND a.id::text = e.source_ref
JOIN crm.leads l ON l.tenant_id = a.tenant_id AND l.id = a.lead_id JOIN crm.customers c ON c.tenant_id = l.tenant_id AND c.id = l.customer_id
WHERE e.source = 'crm.appointment' AND c.party_id IS NOT NULL
ON CONFLICT (tenant_id, event_id, party_id) DO NOTHING;
