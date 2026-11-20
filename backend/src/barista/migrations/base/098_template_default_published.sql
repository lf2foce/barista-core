-- module: papers
-- 098 (rà form soạn từ mẫu, 18/11/2026): BỘ MẪU MẶC ĐỊNH của gói (``Pack(records=…)`` → ``papers.create_template`` với ``publish``) được cài lúc tạo công ty /
-- nâng cấp, chạy bằng quyền hệ thống — chưa có NGƯỜI nào phát hành. Phiên bản phát hành bởi bộ mặc định ghi ``published_by`` TRỐNG (không gán tên một người
-- không làm việc đó); phát hành bởi người vẫn ghi tên người (``papers.publish_template``). Nới ràng buộc, không đổi dữ liệu / cột ⇒ bản cũ chạy song song
-- (không bao giờ ghi ``published_by`` trống) không bị ảnh hưởng ⇒ không bảo trì.
SET LOCAL lock_timeout = '10s';
ALTER TABLE papers.template_versions DROP CONSTRAINT template_versions_published_check;
ALTER TABLE papers.template_versions ADD CONSTRAINT template_versions_published_check CHECK (status = 'draft' OR published_at IS NOT NULL);
