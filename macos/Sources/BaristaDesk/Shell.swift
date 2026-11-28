import AuthenticationServices
import SwiftUI

/// Chọn vai (dev) → khung 4 phân hệ do manifest mô tả.
struct RootView: View {
    @AppStorage("email") private var email = ""
    @AppStorage("tenant") private var tenant = ""
    @State private var token: String? = SessionStore.load()

    var body: some View {
        if let token, !tenant.isEmpty {
            // đăng nhập thật: phiên trong Keychain, quyền do core nạp lại mỗi request
            MainView(api: API(base: API.defaultBase, email: email, tenant: tenant, token: token)) { signOut() }
                .id(token + tenant)
        } else if token == nil, !email.isEmpty, !tenant.isEmpty {
            MainView(api: API(base: API.defaultBase, email: email, tenant: tenant)) { email = "" }   // dev: đổi vai
                .id(email + tenant)
        } else {
            LoginView(onPick: { login in
                tenant = login.tenant_id
                email = login.email
            }, onSession: { grant, tenantId in
                SessionStore.save(grant.token)
                email = grant.email
                tenant = tenantId
                token = grant.token
            })
        }
    }

    private func signOut() {
        if let token {
            let api = API(base: API.defaultBase, email: email, tenant: tenant, token: token)
            Task { await api.logout() }
        }
        SessionStore.delete()
        token = nil
        tenant = ""
        email = ""
    }
}

/// Vòng tròn chữ cái đầu, nền cam chuyển sắc (kiểu barista_bds).
struct PersonAvatar: View {
    let name: String
    var size: CGFloat = 36

    var body: some View {
        Text(String(name.split(separator: " ").last?.first ?? "?"))
            .font(.system(size: size * 0.42, weight: .semibold)).foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(Circle().fill(LinearGradient(colors: [Color(light: 0xFFA45C, dark: 0xFFA45C), Color(light: 0xF0642D, dark: 0xF0642D)],
                                                      startPoint: .top, endPoint: .bottom)))
    }
}

struct LoginView: View {
    let onPick: (Login) -> Void
    let onSession: (SessionGrant, String) -> Void
    @Environment(\.webAuthenticationSession) private var webAuth
    @State private var config: AuthConfig?
    @State private var logins: [Login] = []
    @State private var error: String?
    @State private var signingIn = false
    @State private var grant: SessionGrant?   // đăng nhập xong nhưng thuộc nhiều công ty ⇒ chọn

    private var groups: [(String, [Login])] {
        var order: [String] = []
        for l in logins { let g = l.group ?? "Khác"; if !order.contains(g) { order.append(g) } }
        return order.map { g in (g, logins.filter { ($0.group ?? "Khác") == g }) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                #if os(iOS)
                // iPhone / iPad: màn đầu tiên khách và người duyệt App Store thấy — logo, tên app, một câu; địa chỉ máy chủ xuống cuối, chữ nhỏ
                VStack(spacing: 10) {
                    Image("Logo").resizable().scaledToFit().frame(width: 84, height: 84)
                        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                    Text("Barista").font(.system(size: 28, weight: .semibold))
                    Text("Mỗi người một trợ lý AI.")
                        .font(.t14).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity).padding(.top, 36).padding(.bottom, 8)
                #else
                VStack(alignment: .leading, spacing: 4) {
                    Text("Barista Desk").font(.t20)
                    Text("Mỗi người một trợ lý — thấy đúng phần dữ liệu của mình.").font(.t14).foregroundStyle(.secondary)
                }
                #endif
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle").font(.t14).foregroundStyle(.red)
                }
                if let grant {
                    tenantPicker(grant)
                } else if let g = config?.google {
                    VStack(alignment: .leading, spacing: 8) {
                        Button { signIn(g.client_id) } label: {
                            Label(signingIn ? "Đang đăng nhập…" : "Đăng nhập bằng Google", systemImage: "person.badge.key.fill")
                                .frame(minWidth: 240)
                        }
                        .buttonStyle(.borderedProminent).controlSize(.large).disabled(signingIn)
                        Text("Dùng tài khoản Google mà quản trị đã mời. Chưa được mời thì nhờ quản trị thêm email của bạn.")
                            .font(.t12).foregroundStyle(.secondary)
                    }
                }
                if grant == nil, config?.email == true {
                    EmailSignIn { session in
                        if session.tenants.count == 1, let only = session.tenants.first { onSession(session, only.id) } else { grant = session }
                    }
                }
                if config?.dev ?? true, grant == nil, !logins.isEmpty {
                    if config?.google != nil || config?.email == true {
                        // máy chủ thật mở chọn vai = bản demo công khai (dữ liệu mẫu) ⇒ tên sản phẩm, không chữ "dev" trước mặt người xem / người duyệt App Store
                        Text(API.isLocalServer ? "Đăng nhập dev — chỉ trên máy chạy core" : "Barista Cloud").font(.t12m).foregroundStyle(.secondary).padding(.top, 8)
                    }
                    ForEach(groups, id: \.0) { group, rows in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(group).font(.t12m).foregroundStyle(.secondary)
                            VStack(spacing: 0) {
                                ForEach(Array(rows.enumerated()), id: \.element.id) { i, l in
                                    if i > 0 { Divider().padding(.leading, 62) }
                                    RoleRow(login: l) { onPick(l) }
                                }
                            }
                            .background(RoundedRectangle(cornerRadius: 12).fill(Color.surface))
                        }
                    }
                }
                if config != nil, config?.google == nil, config?.email != true, !(config?.dev ?? false) {
                    Text("Core chưa cấu hình đăng nhập Google (BARISTA_GOOGLE_CLIENT_IDS, BARISTA_GOOGLE_APP_CLIENT_ID).")
                        .font(.t12).foregroundStyle(.secondary).textSelection(.enabled)
                }
                #if os(iOS)
                // chân màn: chỉ chính sách quyền riêng tư — đổi máy chủ (công ty có máy chủ riêng) bằng lắc máy, không hiện địa chỉ trước mặt người dùng
                Link("Chính sách quyền riêng tư", destination: AIConsent.privacyURL).font(.t12)
                    .frame(maxWidth: .infinity).padding(.top, 12)
                #endif
            }
            .padding(28)
            .padding(.top, titlebarInset)
            .frame(maxWidth: 640)
            .frame(maxWidth: .infinity)
        }
        #if os(iOS)
        .background(
            LinearGradient(colors: [Color(red: 0.12, green: 0.25, blue: 0.69).opacity(0.22), Color(red: 0.08, green: 0.72, blue: 0.65).opacity(0.10), Color.canvas],
                           startPoint: .top, endPoint: .center)
                .background(Color.canvas)
                .ignoresSafeArea()
        )
        #else
        .background(Color.canvas)
        #endif
        .task { await load() }
    }

    private func tenantPicker(_ g: SessionGrant) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Chọn công ty").font(.t12m).foregroundStyle(.secondary)
            VStack(spacing: 0) {
                ForEach(Array(g.tenants.enumerated()), id: \.element.id) { i, t in
                    if i > 0 { Divider().padding(.leading, 14) }
                    Button { onSession(g, t.id) } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(t.name).font(.t14m)
                                Text(t.role_label).font(.t12).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Image(systemName: "chevron.right").font(.system(size: 11, weight: .semibold)).foregroundStyle(.tertiary)
                        }
                        .padding(.horizontal, 14).padding(.vertical, 10).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .background(RoundedRectangle(cornerRadius: 12).fill(Color.surface))
        }
    }

    private func load() async {
        config = try? await API.authConfig()
        if config == nil || config?.dev == true {
            do { logins = try await API(base: API.defaultBase, email: "", tenant: "").logins(); error = nil }
            catch { if config == nil { self.error = "Không kết nối được core: \(error.localizedDescription)" } }
        }
    }

    private func signIn(_ clientID: String) {
        signingIn = true
        error = nil
        Task {
            defer { signingIn = false }
            do {
                let g = try await GoogleSignIn.signIn(clientID: clientID, session: webAuth)
                let session = try await API.loginGoogle(idToken: g.idToken, nonce: g.nonce)
                if session.tenants.count == 1, let only = session.tenants.first { onSession(session, only.id) } else { grant = session }
            } catch is CancellationError {
            } catch let e as ASWebAuthenticationSessionError where e.code == .canceledLogin {
            } catch {
                self.error = error.localizedDescription
            }
        }
    }
}

/// Đăng nhập bằng mã gửi qua email (0205 §5c) — email bất kỳ quản trị đã mời (Gmail, email công ty…). Hai bước: email ⇒ "Gửi mã", rồi mã 6 số ⇒ phiên.
/// Chưa được mời thì core trả lời y hệt nhưng không gửi thư ⇒ câu nhắc ghi rõ "nếu email đã được mời".
private struct EmailSignIn: View {
    let onSession: (SessionGrant) -> Void
    @AppStorage("loginEmail") private var email = ""
    @State private var code = ""
    @State private var sent = false
    @State private var busy = false
    @State private var error: String?
    @FocusState private var codeFocused: Bool

    private var trimmed: String { email.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        #if os(iOS)
        phone
        #else
        desk
        #endif
    }

    #if os(iOS)
    /// iPhone / iPad: thẻ trắng bo góc, ô to, nút full chiều ngang (màn đầu tiên khách + người duyệt App Store thấy).
    private var phone: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(sent ? "Nhập mã trong email" : "Đăng nhập").font(.system(size: 20, weight: .semibold))
            if !sent {
                // gợi ý vẽ tay, verbatim: Text("…@…") bị đọc như markdown ⇒ thành link xanh (trông như email đã điền sẵn), bỏ qua foregroundStyle
                ZStack(alignment: .leading) {
                    if email.isEmpty { Text(verbatim: "email@company.com").foregroundStyle(Color(uiColor: .placeholderText)).allowsHitTesting(false) }
                    TextField("", text: $email)
                        .keyboardType(.emailAddress).textContentType(.username).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .foregroundStyle(.primary)
                }
                    .font(.system(size: 17)).padding(.horizontal, 16).frame(height: 52)
                    .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.canvas))
                    .onSubmit { start() }
                Button { start() } label: {
                    Text(busy ? "Đang gửi…" : "Nhận mã đăng nhập").font(.system(size: 17, weight: .semibold)).frame(maxWidth: .infinity).frame(height: 52)
                }
                .buttonStyle(.borderedProminent).buttonBorderShape(.roundedRectangle(radius: 14)).disabled(busy || !trimmed.contains("@"))
            } else {
                Text("Đã gửi mã 6 số tới \(trimmed). Mã hết hạn sau 10 phút.").font(.t14).foregroundStyle(.secondary)
                TextField("••••••", text: $code)
                    .keyboardType(.numberPad).textContentType(.oneTimeCode).focused($codeFocused)
                    .font(.system(size: 28, weight: .semibold, design: .rounded)).kerning(10).multilineTextAlignment(.center)
                    .frame(height: 60).background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.canvas))
                    .onChange(of: code) { _, v in
                        let digits = String(v.filter(\.isNumber).prefix(6))
                        if digits != v { code = digits }
                        if digits.count == 6, !busy { verify() }
                    }
                Button { verify() } label: {
                    Text(busy ? "Đang kiểm…" : "Đăng nhập").font(.system(size: 17, weight: .semibold)).frame(maxWidth: .infinity).frame(height: 52)
                }
                .buttonStyle(.borderedProminent).buttonBorderShape(.roundedRectangle(radius: 14)).disabled(busy || code.count != 6)
                Button("Đổi email / gửi lại mã") { sent = false; code = ""; error = nil }.font(.t14).frame(maxWidth: .infinity)
            }
            if let error { Label(error, systemImage: "exclamationmark.triangle").font(.t12).foregroundStyle(Color.bad) }
        }
        .padding(20)
        .background(RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Color.surface))
        .shadow(color: .black.opacity(0.06), radius: 16, y: 6)
    }
    #endif

    private var desk: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Đăng nhập bằng email").font(.t12m).foregroundStyle(.secondary)
            HStack(spacing: 8) {
                TextField("email@company.com", text: $email)
                    .textFieldStyle(.roundedBorder).disabled(sent).onSubmit { if !sent { start() } }
                    #if os(iOS)
                    .keyboardType(.emailAddress).textContentType(.username).textInputAutocapitalization(.never).autocorrectionDisabled()
                    #endif
                if sent {
                    Button("Đổi email") { sent = false; code = ""; error = nil }.buttonStyle(.bordered)
                } else {
                    Button(busy ? "Đang gửi…" : "Gửi mã") { start() }
                        .buttonStyle(.borderedProminent).disabled(busy || !trimmed.contains("@"))
                }
            }
            if sent {
                HStack(spacing: 8) {
                    TextField("Mã 6 số", text: $code)
                        .textFieldStyle(.roundedBorder).focused($codeFocused).onSubmit { verify() }
                        .onChange(of: code) { _, v in
                            let digits = String(v.filter(\.isNumber).prefix(6))
                            if digits != v { code = digits }
                            if digits.count == 6, !busy { verify() }   // dán / tự điền mã từ thư ⇒ đăng nhập luôn
                        }
                        #if os(iOS)
                        .keyboardType(.numberPad).textContentType(.oneTimeCode)
                        #endif
                    Button(busy ? "Đang kiểm…" : "Đăng nhập") { verify() }
                        .buttonStyle(.borderedProminent).disabled(busy || code.count != 6)
                }
                Text("Nếu \(trimmed) đã được mời, mã vừa tới hộp thư (hết hạn sau 10 phút). Không thấy thì xem thư rác, hoặc bấm Đổi email để gửi lại.")
                    .font(.t12).foregroundStyle(.secondary)
            } else {
                Text("Dùng email mà quản trị đã mời — Gmail hay email công ty đều được.").font(.t12).foregroundStyle(.secondary)
            }
            if let error { Label(error, systemImage: "exclamationmark.triangle").font(.t12).foregroundStyle(Color.bad) }
        }
    }

    private func start() {
        guard trimmed.contains("@"), !busy else { return }
        busy = true; error = nil
        Task {
            defer { busy = false }
            do {
                try await API.startEmail(trimmed)
                sent = true
                codeFocused = true
            } catch { self.error = error.localizedDescription }
        }
    }

    private func verify() {
        guard code.count == 6, !busy else { return }
        busy = true; error = nil
        Task {
            defer { busy = false }
            do { onSession(try await API.verifyEmail(trimmed, code: code)) }
            catch { self.error = error.localizedDescription; code = "" }
        }
    }
}

private struct RoleRow: View {
    let login: Login
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 12) {
                PersonAvatar(name: login.name)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(login.name).font(.t14m)
                        Text(login.role).font(.t12).foregroundStyle(.secondary)
                    }
                    if let hint = login.hint, !hint.isEmpty { Text(hint).font(.t12).foregroundStyle(.secondary) }
                    if let ask = login.ask, !ask.isEmpty {
                        Text("Thử hỏi: “\(ask)”").font(.t12).foregroundStyle(Color.bad)
                    }
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.right").font(.system(size: 11, weight: .semibold)).foregroundStyle(.tertiary).padding(.top, 10)
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .background(hover ? Color.primary.opacity(0.04) : .clear)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

struct MainView: View {
    @State private var desk: Desk

    init(api: API, onSwitch: @escaping () -> Void) {
        let d = Desk(api: api)
        d.switchPersona = onSwitch
        _desk = State(initialValue: d)
    }

    var body: some View {
        Group {
            if let error = desk.loadError {
                ContentUnavailableView {
                    Label("Không tải được", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(error)
                } actions: {
                    Button(desk.api.token == nil ? "Chọn lại vai" : "Đăng nhập lại") { desk.switchPersona() }
                    Button("Thử lại") { Task { await desk.load() } }
                }
            } else {
                #if os(macOS)
                DeskShell()   // thanh bên trái | cột theo mục chọn | khung làm việc (Rail.swift)
                #else
                PhoneShell()   // iPhone / iPad: thanh tab Trợ lý · Việc · Tin nhắn + khung làm việc (ios/Sources/PhoneShell.swift)
                #endif
            }
        }
        .environment(desk)
        .sheet(isPresented: Binding(get: { desk.askingAIConsent }, set: { if !$0 && desk.askingAIConsent { desk.decideAIConsent(false) } })) {
            AIConsentSheet { desk.decideAIConsent($0) }
        }
        .onReceive(NotificationCenter.default.publisher(for: .baristaPushToken)) { n in
            if let t = n.object as? String { Task { await desk.registerPush(token: t) } }
        }
        .onReceive(NotificationCenter.default.publisher(for: .baristaOpenNotice)) { _ in
            if let t = PushInbox.pendingOpen { PushInbox.pendingOpen = nil; Task { await desk.openTargetOrTalk(query: t.query, input: t.input, title: "") } }
        }
        .onDisappear { desk.stopNotices() }
        .task {
            #if os(macOS)
            VoiceWindows.shared.bind(desk)   // chế độ Siri nổi (quả cầu / nút sóng âm)
            #endif
            await desk.load()
            desk.startNotices()
            if let t = PushInbox.token { await desk.registerPush(token: t) }
            if let t = PushInbox.pendingOpen { PushInbox.pendingOpen = nil; await desk.openTargetOrTalk(query: t.query, input: t.input, title: "") }
            // dev / demo: BARISTA_ASK="câu hỏi" ⇒ tự gửi một câu khi mở (quay demo, kiểm giao diện không cần gõ)
            if let q = ProcessInfo.processInfo.environment["BARISTA_ASK"], !q.isEmpty { desk.send(q) }
            if ProcessInfo.processInfo.environment["BARISTA_VOICE"] == "1" { desk.startVoiceMode() }  // dev: bật giọng nói (kèm BARISTA_SAY="câu hỏi")
            if ProcessInfo.processInfo.environment["BARISTA_CANVAS"] == "1" { desk.narrowShowsCanvas = true }  // dev: cửa sổ hẹp mở thẳng khung làm việc
            // dev / nghiệm thu: BARISTA_OPEN='truy_vấn {"tham":"số"}' ⇒ mở thẳng một thẻ (chụp màn hình trình giả lập không cần bấm)
            if let raw = ProcessInfo.processInfo.environment["BARISTA_OPEN"], let sp = raw.firstIndex(of: " ") ?? Optional(raw.endIndex) {
                let input = (try? JSONDecoder().decode(Input.self, from: Data(raw[sp...].utf8))) ?? [:]
                await desk.openTargetOrTalk(query: String(raw[..<sp]), input: input, title: "")
            }
        }
    }
}
