# 09 · Đăng nhập Google — cấu hình, gỡ liên kết, sự cố

Quyết định: 0040.

## Cấu hình lần đầu
1. Google Cloud Console › APIs & Services › **OAuth consent screen**: loại *Internal* nếu công ty dùng Google Workspace (chỉ người trong tên miền),
   *External* nếu có người dùng Gmail cá nhân.
2. **Credentials › Create OAuth client ID › iOS** (loại này cũng dùng cho app Mac): Bundle ID `vn.barista.desk`. Ghi lại client ID
   (dạng `123-abc.apps.googleusercontent.com`).
3. Đặt cho core (biến môi trường hoặc `.env`, dạng JSON cho danh sách):
   ```
   BARISTA_GOOGLE_CLIENT_IDS='["123-abc.apps.googleusercontent.com"]'   # mọi client của bản cài (thêm web / iOS sau)
   BARISTA_GOOGLE_APP_CLIENT_ID='123-abc.apps.googleusercontent.com'    # client app Mac dùng
   BARISTA_GOOGLE_HOSTED_DOMAINS='["tencongty.vn"]'                     # tuỳ chọn — chỉ khi mọi người dùng Workspace
   BARISTA_SESSION_TTL_HOURS=12
   ```
4. Khởi động lại core; mở app ⇒ nút "Đăng nhập bằng Google".

## Thêm / khoá người
Quản trị › Người dùng › *Thêm người dùng* (email phải là email Google họ dùng). Khoá: *Sửa người dùng* › bỏ "Đang làm việc" — có hiệu lực ngay
request sau, không cần chờ phiên hết hạn.

## Email công ty cấp lại cho người mới / nhân viên đổi tài khoản Google
Người mới đăng nhập sẽ gặp "Email này đã gắn với một tài khoản Google khác". Quản trị kiểm đúng người, rồi Người dùng › dòng đó ›
**Gỡ liên kết Google** (thu hồi luôn mọi phiên cũ). Lần đăng nhập sau gắn tài khoản mới. KHÔNG gỡ nếu chưa chắc ai đang đăng nhập — người
mới sẽ thấy ví hoa hồng / lịch sử của người cũ.

## Sự cố
- "Không xác thực được tài khoản Google": xem log core (`barista.http_api`) — thường do `aud` (client ID chưa nằm trong `BARISTA_GOOGLE_CLIENT_IDS`),
  đồng hồ máy chủ lệch (> 60 giây), hoặc tài khoản ngoài tên miền khi đã đặt `BARISTA_GOOGLE_HOSTED_DOMAINS`.
- "Email này chưa được mời": thêm người dùng đúng email Google của họ.
- App báo "Phiên đăng nhập đã hết hạn": bình thường sau 12 giờ — bấm "Đăng nhập lại".
