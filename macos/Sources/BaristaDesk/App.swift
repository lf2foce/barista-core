import AppKit
import SwiftUI

/// `--check-contract [url]`: đăng nhập lần lượt từng vai dev, tải manifest, chạy mọi màn + mọi form bằng chính model Swift —
/// đổi output phía server mà quên app thì lỗi ở đây trước khi người dùng thấy.
@main
enum Entry {
    static func main() {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--snapshot-voice") {
            // dev: dựng khối Siri nổi + ngăn thẻ NGOÀI màn hình bằng dữ liệu thật rồi xuất PNG — không hiện cửa sổ, không bật micro
            let out = URL(fileURLWithPath: args.dropFirst(i + 1).first ?? ".")
            let app = NSApplication.shared
            app.setActivationPolicy(.prohibited)   // không biểu tượng Dock, không cửa sổ nào hiện
            Task { @MainActor in
                await VoiceSnapshot.run(into: out)
                exit(0)
            }
            app.run()   // AppKit cần luồng chính thật (dispatchMain chạy hàng đợi chính trên luồng khác)
        }
        if let i = args.firstIndex(of: "--render-icon") {
            let out = URL(fileURLWithPath: args.dropFirst(i + 1).first ?? ".")
            let app = NSApplication.shared
            app.setActivationPolicy(.prohibited)
            Task { @MainActor in
                AppIconArt.render(into: out)
                exit(0)
            }
            app.run()
        }
        if let i = args.firstIndex(of: "--snapshot-shell") {
            // dev: dựng cửa sổ chính (thanh bên trái + cột + khung làm việc) ngoài màn hình rồi xuất PNG
            let out = URL(fileURLWithPath: args.dropFirst(i + 1).first ?? ".")
            let app = NSApplication.shared
            app.setActivationPolicy(.prohibited)
            if ProcessInfo.processInfo.environment["BARISTA_DARK"] == "1" { app.appearance = NSAppearance(named: .darkAqua) }
            Task { @MainActor in
                await ShellSnapshot.run(into: out)
                exit(0)
            }
            app.run()
        }
        guard let i = args.firstIndex(of: "--check-contract") else {
            BaristaDeskApp.main()
            return
        }
        let base = args.dropFirst(i + 1).first.flatMap(URL.init(string:)) ?? API.defaultBase
        Task {
            let ok = await ContractCheck.run(base: base)
            exit(ok ? 0 : 1)
        }
        dispatchMain()
    }
}

struct BaristaDeskApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        WindowGroup("Barista Desk") {
            RootView()
                .frame(minWidth: 760, minHeight: 600)
                .ignoresSafeArea()
        }
        .windowStyle(.hiddenTitleBar)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)  // chạy bằng `swift run` vẫn có biểu tượng Dock + nhận bàn phím
        if let icon = AppIconArt.image() { NSApp.applicationIconImage = icon }   // không có .app ⇒ đặt icon lúc chạy
        NSApp.activate()
    }

    /// Đóng cửa sổ cuối ⇒ thoát — TRỪ khi đang ở chế độ Siri nổi: cửa sổ chính chỉ lui đi (orderOut), khối Siri + ngăn thẻ là panel
    /// nên AppKit coi như "hết cửa sổ" và sẽ thoát app ngay khi bấm quả cầu.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { !VoiceWindows.shared.active }
}

enum ContractCheck {
    /// Trang MỞ TỪ màn (dòng đầu tiên của bảng → trang chi tiết / thẻ) và từ thẻ (nút "mở", dòng danh sách) — một tầng — cũng giải mã
    /// được bằng model Swift. Trả số lỗi.
    static func deep(_ api: API, _ who: String, _ title: String, _ page: Page, open: OpenSpec?) async -> Int {
        var targets: [(String, Input)] = []
        var failures = 0
        switch page {
        case .table(let t):
            if let o = open, let row = t.rows.first {
                var input: Input = [:]
                for (field, column) in o.bind { input[field] = row.values[column] }
                targets.append((o.query, input))
            }
        case .card(let c):
            for o in c.open { targets.append((o.query, o.input)) }
            for case .list(_, let items) in c.blocks { for it in items { if let o = it.open { targets.append((o.query, o.input)) } } }
            for case .tiles(_, let items) in c.blocks { for it in items { if let o = it.open { targets.append((o.query, o.input)) } } }
            for case .timeline(let t) in c.blocks { for g in t.groups { for it in g.items { if let o = it.open { targets.append((o.query, o.input)) } } } }
            // khối tải sau (ui.b_later / ui.part): tải đúng như app tải, trang con cũng phải giải mã được và không mang khối lạ
            for case .later(let l) in c.blocks {
                do {
                    let sub: Page
                    if let part = l.part { sub = try await api.part(l.query, l.input, part: part) } else { sub = try await api.query(l.query, l.input) }
                    if case .card(let sc) = sub { for case .unknown in sc.blocks { failures += 1; print("✗ \(who) · \(title) → phần \(l.part ?? l.query): khối app chưa biết vẽ") } }
                } catch {
                    failures += 1
                    print("✗ \(who) · \(title) → phần \(l.part ?? l.query): \(error.localizedDescription)")
                }
            }
            for case .board(_, let board) in c.blocks {
                for col in board.columns { for it in col.items { if let o = it.open { targets.append((o.query, o.input)) } } }
            }
            // server và app phát hành cùng nhau: khối server gửi mà app không biết vẽ = quên sửa app (app thật thì im lặng bỏ qua)
            for case .unknown in c.blocks {
                failures += 1
                print("✗ \(who) · \(title): thẻ có khối app chưa biết vẽ")
            }
        case .detail: break
        }
        var seen = Set<String>()
        for (query, input) in targets where seen.insert(query).inserted {  // mỗi loại trang kiểm một lần
            do { _ = try await api.query(query, input) } catch {
                failures += 1
                print("✗ \(who) · \(title) → mở \(query): \(error.localizedDescription)")
            }
        }
        return failures
    }

    static func run(base: URL) async -> Bool {
        var failures = 0
        do {
            let logins = try await API(base: base, email: "", tenant: "").logins()
            guard let tenant = logins.first?.tenant_id else { print("Không có tài khoản dev"); return false }
            for login in logins where login.tenant_id == tenant {
                let api = API(base: base, email: login.email, tenant: login.tenant_id)
                let m = try await api.manifest()
                var screens = m.workspaces.flatMap(\.screens)
                if let inbox = m.inbox { screens.append(inbox) }
                screens += m.desk ?? []   // màn việc chỉ ở bàn việc (0094)
                var forms = Set<String>()
                for s in screens {
                    do {
                        // như app mở màn: phần nhanh + khối tải sau, bảng một trang (server cũ bỏ qua tham số ⇒ trang đủ)
                        let page = try await api.page(s.query, s.input ?? [:], rows: (0, ScreenView.pageRows))   // màn app tự ghép mang mã app (0098)
                        failures += await deep(api, login.email, s.title, page, open: s.open)
                    } catch {
                        failures += 1
                        print("✗ \(login.email) · \(s.title) (\(s.query)): \(error.localizedDescription)")
                    }
                    forms.formUnion((s.actions + s.row_actions).map(\.command))
                }
                for f in forms.sorted() {
                    do { _ = try await api.form(f) } catch {
                        failures += 1
                        print("✗ \(login.email) · form \(f): \(error.localizedDescription)")
                    }
                }
                // hộp tin (cột "Tin nhắn") + một luồng mỗi nhóm (tin riêng / phòng / hồ sơ) — không phải màn nên vòng trên không chạm tới
                if let inbox: TalkInbox = try? await api.data("talk.inbox") {
                    var kinds = Set<String>()
                    for item in inbox.items where kinds.insert(item.section ?? "").inserted {
                        do {
                            let page = try await api.query("talk.thread", ["subject_kind": .string(item.subject_kind), "subject_id": .string(item.subject_id)])
                            guard case .card(let c) = page, c.blocks.contains(where: { if case .thread = $0 { true } else { false } }) else {
                                throw APIError(message: "không có khối trao đổi")
                            }
                        } catch {
                            failures += 1
                            print("✗ \(login.email) · trao đổi \(item.subject_kind): \(error.localizedDescription)")
                        }
                    }
                }
                print("✓ \(login.name) (\(login.role)): \(m.workspaces.map(\.title).joined(separator: ", ")) — \(screens.count) màn, \(forms.count) form")
            }
        } catch {
            print("✗ \(error.localizedDescription)")
            return false
        }
        print(failures == 0 ? "Hợp đồng khớp." : "\(failures) lỗi.")
        return failures == 0
    }
}
