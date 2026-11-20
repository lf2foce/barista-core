# Runbook 06 · Nối nguồn tài liệu ngoài (Google Drive) — khi làm

Theo quyết định 0016 / 0017. Bộ kết nối chạy quyền hệ thống, chỉ gọi `kb.sync_external` (tài liệu + danh sách quyền hiệu lực,
ghi đè cả danh sách) và `admin.sync_groups` (thành viên nhóm). Làm một luồng nhỏ trước: một thư mục, vài tài liệu, ba vai;
kiểm thu hồi chia sẻ, chuyển thư mục, đổi thành viên nhóm (đã có test giả lập trong `tests/test_external_acl.py`) rồi mới mở
cả công ty.
