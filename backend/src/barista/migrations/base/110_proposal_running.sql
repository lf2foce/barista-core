-- module: core — lý do: lệnh có bước chuẩn bị gọi ra hệ ngoài (op.prepare) cần chiếm thẻ đề xuất NGUYÊN TỬ trước khi nhả kết nối DB để gọi mạng:
-- trạng thái 'running' khoá chặt thẻ trong lúc gọi ngoài (Bỏ hoặc Xác nhận thứ hai bị chặn ⇒ hệ ngoài nhận 0 lệnh nếu Bỏ thắng, tối đa 1 lệnh nếu Xác nhận thắng);
-- 'failed' ghi lại khi bước gọi ngoài hoặc bước lưu sau đó lỗi (thẻ nói đúng trạng thái, bấm lại không gọi hệ ngoài lần hai). CHỈ NỚI ràng buộc CHECK ⇒ không bảo trì.
ALTER TABLE core.proposals DROP CONSTRAINT proposals_status_check;
ALTER TABLE core.proposals ADD CONSTRAINT proposals_status_check
    CHECK (status = ANY (ARRAY['open'::text, 'running'::text, 'done'::text, 'dismissed'::text, 'expired'::text, 'failed'::text]));
