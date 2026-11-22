import SwiftUI
import UserNotifications

/// Barista trên iPhone: cùng trợ lý, cùng thẻ, cùng đăng nhập với app Mac — mã dùng chung ở ../macos/Sources/BaristaDesk. File này chỉ là điểm vào
/// + phần riêng của điện thoại (cài đặt máy chủ). Bố cục một ngăn (chat toàn màn hình, nút nổi "Khung làm việc · N") đã có sẵn ở MainView
/// cho cửa sổ hẹp; bảng nhiều cột hiện thành danh sách thẻ (DataTable bản iOS).
@main
struct BaristaPhoneApp: App {
    @UIApplicationDelegateAdaptor(PushDelegate.self) private var push
    @State private var showServer = false

    var body: some Scene {
        WindowGroup {
            RootView()
                .onReceive(NotificationCenter.default.publisher(for: .baristaOpenServerSettings)) { _ in showServer = true }
                .onShake { showServer = true }
                .sheet(isPresented: $showServer) { ServerSettings() }
        }
    }
}

/// Thông báo đẩy (quyết định 0044): xin quyền, lấy mã máy APNs (Desk gửi lên core sau khi đăng nhập), bấm thông báo ⇒ mở đúng thẻ.
/// Đẩy chỉ mang một dòng chung chung + mã — nội dung app đọc lại qua quyền người dùng.
final class PushDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        if ProcessInfo.processInfo.environment["BARISTA_NO_PUSH"] == "1" { return true }   // dev: chụp màn hình trình giả lập, không hỏi quyền
        Task { @MainActor in
            let ok = (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])) ?? false
            if ok { application.registerForRemoteNotifications() }
        }
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in
            PushInbox.token = hex
            NotificationCenter.default.post(name: .baristaPushToken, object: hex)
        }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        print("APNs: không đăng ký được — \(error.localizedDescription)")
    }

    /// Đang mở app: không bật banner (app đã tự hiện dòng nhắc từ kênh thông báo), chỉ cập nhật danh sách + số trên biểu tượng.
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.list, .badge]
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        await MainActor.run {
            guard let target = PushInbox.target(info) else { return }
            PushInbox.pendingOpen = target   // app mở nguội: Desk xử lý sau khi tải xong; đang chạy: nhận ngay qua thông báo dưới
            NotificationCenter.default.post(name: .baristaOpenNotice, object: nil)
        }
    }
}

/// Địa chỉ máy chủ: để trống = ``API.shippedBase`` (bản phát hành: máy chủ chung; bản dev: localhost). Công ty có bản cài riêng nhập https://… của mình;
/// dev trên iPhone thật nhập IP máy chạy core.
private struct ServerSettings: View {
    @Environment(\.dismiss) private var dismiss
    @AppStorage("baseURL") private var url = ""
    @AppStorage("demoKey") private var demoKey = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Máy chủ Barista") {
                    TextField(API.shippedBase, text: $url)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Text("Để trống = \(API.shippedBase). Chỉ đổi khi công ty anh/chị có máy chủ Barista riêng (địa chỉ https:// do quản trị gửi).")
                        .font(.t12).foregroundStyle(.secondary)
                }
                Section("Bản demo") {
                    SecureField("Mã truy cập demo", text: $demoKey)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Text("Chỉ cần với bản demo trên máy chủ (vd https://api.vi-du.test) để đăng nhập chọn vai. Bản thật đăng nhập Google, để trống.")
                        .font(.t12).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Cài đặt")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Xong") { dismiss() } } }
        }
        .presentationDetents([.medium])
    }
}

// MARK: - Lắc máy ⇒ mở cài đặt máy chủ (iPhone không có thanh menu như Mac)

extension UIWindow {
    override open func motionEnded(_ motion: UIEvent.EventSubtype, with event: UIEvent?) {
        if motion == .motionShake { NotificationCenter.default.post(name: .baristaOpenServerSettings, object: nil) }
        super.motionEnded(motion, with: event)
    }
}

extension View {
    func onShake(_ action: @escaping () -> Void) -> some View {
        onReceive(NotificationCenter.default.publisher(for: .baristaOpenServerSettings)) { _ in action() }
    }
}
