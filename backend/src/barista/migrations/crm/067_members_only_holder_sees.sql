-- module: crm, leadflow — lý do: 0152 sau review độc lập 10/11 — bật "chỉ thành viên" không được giấu lead người đó ĐANG GIỮ (vòng quét vẫn chấm hạn /
-- thu hồi theo người đó). Tách chính sách: ĐỌC — người giữ luôn thấy lead của mình; THÊM — người bị giới hạn không tạo lead cho thứ mình không là thành
-- viên. Sửa / xoá theo phạm vi như cũ (lead đã giữ vẫn ghi chăm sóc được). Giao / chia cho người không là thành viên chặn ở code (leadflow + crm.assign).
-- Chỉ đổi chính sách hạn chế, công tắc mặc định tắt ⇒ không bảo trì.
DROP POLICY p_members_only ON crm.leads;
CREATE POLICY p_members_only_read ON crm.leads AS RESTRICTIVE FOR SELECT
    USING (NOT (SELECT leadflow.members_only()) OR owner_id = (SELECT core.me()) OR offering_code = ''
           OR offering_code = ANY ((SELECT leadflow.my_offerings())::text[]));
CREATE POLICY p_members_only_new ON crm.leads AS RESTRICTIVE FOR INSERT
    WITH CHECK (NOT (SELECT leadflow.members_only()) OR offering_code = '' OR offering_code = ANY ((SELECT leadflow.my_offerings())::text[]));
