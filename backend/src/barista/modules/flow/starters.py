"""Quy trình MẪU dựng sẵn (rà UI 20/11/2026: form "Thêm quy trình" đòi người mới tự viết cú pháp #/-/>/~ mà không có ví dụ nào để bắt đầu).

Mỗi mẫu là chữ ở dạng form "Các chặng" (``cases.parse_template``) — bấm một mẫu ⇒ form "Thêm quy trình" mở sẵn tên + các chặng, người sửa rồi lưu. Chỉ dùng dòng
``#`` (chặng) và ``-`` (mục do người xác nhận): không bộ kiểm tự động, không gói nào cần có ⇒ dùng được ở mọi công ty. Mẫu theo CÔNG VIỆC chung, không theo ngành."""

STARTERS: tuple[tuple[str, str, str], ...] = (
    ("Giao hàng & thu tiền", "Từ nhận đơn tới nhận đủ tiền",
     "# Nhận đơn\n- Xác nhận hàng và số lượng với khách\n- Chốt giá\n# Giao hàng\n- Giao hàng\n- Chụp phiếu giao có chữ ký\n# Thu tiền\n- Gửi bảng quyết toán cho khách\n- Nhận đủ tiền"),
    ("Hồ sơ hợp đồng", "Soạn, ký, lưu một hợp đồng",
     "# Soạn\n- Soạn hợp đồng từ mẫu\n- Khách xem lại\n# Ký\n- Hai bên ký\n# Lưu\n- Lưu bản ký vào kho tài liệu"),
    ("Chăm khách sau bán", "Hỏi thăm, xử lý phàn nàn, xin giới thiệu",
     "# Hỏi thăm\n- Gọi hỏi khách đã dùng ổn chưa\n# Xử lý\n- Ghi nhận phàn nàn nếu có\n- Báo khách cách xử lý\n# Giữ quan hệ\n- Xin khách giới thiệu thêm"),
    ("Tuyển người mới", "Từ nhận hồ sơ tới ngày đi làm",
     "# Nhận hồ sơ\n- Đọc hồ sơ\n- Hẹn phỏng vấn\n# Phỏng vấn\n- Phỏng vấn\n- Quyết định nhận hay không\n# Nhận việc\n- Gửi thư mời\n- Chuẩn bị chỗ làm và tài khoản"),
)
