# 10 · Thông báo đẩy + TestFlight cho app iPhone

Quyết định: 0044. Trong app (chuông, dòng nhắc) chạy sẵn không cần gì thêm; phần dưới chỉ
cho **đẩy lên màn khoá iPhone**.

## 1. Apple Developer (một lần)
1. developer.apple.com › Certificates, Identifiers & Profiles › **Identifiers** › tạo App ID trùng `PRODUCT_BUNDLE_IDENTIFIER` của `ios/project.yml` (bản phát hành chung: `com.phenau.barista`), bật **Push Notifications**.
2. **Keys** › tạo khoá mới, tích **Apple Push Notifications service (APNs)** › tải file `AuthKey_XXXXXXXXXX.p8` (chỉ tải được MỘT lần).
   Ghi lại **Key ID** (10 ký tự) và **Team ID** (góc phải trên, 10 ký tự).
3. Cất file `.p8` ngoài repo (vd `~/.secrets/<bản cài>/AuthKey_….p8`, quyền 600). Không commit, không dán vào chat.

## 2. Core
```
BARISTA_APNS_KEY_FILE=/đường/dẫn/AuthKey_XXXXXXXXXX.p8
BARISTA_APNS_KEY_ID=XXXXXXXXXX
BARISTA_APNS_TEAM_ID=YYYYYYYYYY
BARISTA_APNS_TOPIC=com.phenau.barista   # = bundle ID của app iPhone
```
Khởi động lại core. Không đặt các biến này ⇒ thông báo vẫn vào hộp trong app, đẩy được đánh dấu `skipped`.

## 3. App Store Connect + TestFlight
1. appstoreconnect.apple.com › Apps › **+ New App** › iOS, tên "khách đầu tiên" (hoặc tên khách đầu tiên chọn), Bundle ID như trên, SKU tuỳ ý (app chung đã có hồ sơ — chỉ cần khi dựng app riêng).
2. Máy dev: `cd ios && xcodegen generate`, mở `BaristaPhone.xcodeproj` › target BaristaPhone › Signing & Capabilities › chọn Team (đặt luôn
   `DEVELOPMENT_TEAM` trong `ios/project.yml` để lần sau không phải chọn). Kiểm có capability **Push Notifications**.
3. Tăng `CURRENT_PROJECT_VERSION` trong `ios/project.yml` mỗi lần tải lên (Apple từ chối số trùng).
4. Product › Archive (đích "Any iOS Device") › Distribute App › **App Store Connect** › Upload. Xcode tự đổi `aps-environment` sang
   production khi xuất bản.
5. App Store Connect › TestFlight: thêm người thử nội bộ (tài khoản trong nhóm) — không cần Apple duyệt. Người ngoài nhóm cần duyệt
   bản beta lần đầu (thường 1 ngày).
6. Bản phát hành (Release) mặc định trỏ máy chủ chung `API.shippedBase` (`macos/Sources/BaristaDesk/API.swift`); công ty có bản cài riêng đổi ở
   **Cài đặt máy chủ** (lắc máy ở màn đăng nhập). Bản thật phải là `https://` (A2).

## 4. Thử
- Đăng nhập trên iPhone ⇒ cho phép thông báo ⇒ Quản trị xem `core.devices` có dòng của người đó (`env = production` với TestFlight,
  `sandbox` với bản chạy từ Xcode).
- Người khác giao việc cho người đó ⇒ màn khoá hiện "Anh/chị có việc mới", bấm ⇒ app mở thẳng bảng việc.
- Trình giả lập (không cần khoá APNs): kéo file `.apns` vào cửa sổ Simulator, hoặc
  `xcrun simctl push booted com.phenau.barista thu.apns` với nội dung
  `{"aps":{"alert":{"title":"Barista","body":"Anh/chị có việc mới"},"badge":1},"open":{"query":"flow.tasks","input":{}}}`.

## Sự cố
- Không nhận đẩy: xem `core.notifications.push_state` của thông báo đó.
  - `skipped`: chưa cấu hình APNs, hoặc người nhận chưa có máy (chưa đăng nhập trên iPhone / chưa cho phép thông báo).
  - `failed`: xem log `barista.push` (JWT sai Key ID / Team ID, topic khác Bundle ID).
  - Máy bị tắt (`core.devices.disabled_at`): APNs báo mã hết hiệu lực, hoặc đã đăng xuất. Mở app lại là đăng ký lại.
- Bản chạy từ Xcode nhận được nhưng TestFlight không (hoặc ngược lại): sai `env` — Xcode = sandbox, TestFlight = production; app tự
  gửi đúng loại theo cấu hình build (Debug / Release).
