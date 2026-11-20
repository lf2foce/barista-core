import SwiftUI
#if os(iOS)
import UserNotifications
#endif

/// Thông báo (quyết định 0044): hộp của từng người do server ghi; app nghe tín hiệu SSE (chỉ "có mới", không nội dung) rồi đọc lại hộp
/// qua quyền người dùng. iPhone thêm đẩy APNs — bấm thông báo trên màn khoá mở đúng thẻ như bấm trong app.

struct Notice: Decodable, Identifiable, Hashable {
    let id: Int
    let kind, title, body: String
    let open: CardPage.Open?
    let created_at: String
    let read: Bool
}

struct NoticeInbox: Decodable {
    let unread: Int
    let items: [Notice]
}

extension Notification.Name {
    /// iPhone: mã thiết bị APNs vừa có (object: chuỗi hex).
    static let baristaPushToken = Notification.Name("baristaPushToken")
    /// Bấm một thông báo đẩy (object: ["query": String, "input": [String: Any]]).
    static let baristaOpenNotice = Notification.Name("baristaOpenNotice")
}

/// Đẩy tới trước khi có phiên (app vừa mở, chưa đăng nhập xong): giữ lại, ``Desk`` xử lý sau khi tải xong.
@MainActor enum PushInbox {
    static var token: String? {
        get { UserDefaults.standard.string(forKey: "apnsToken") }
        set { UserDefaults.standard.set(newValue, forKey: "apnsToken") }
    }
    static var pendingOpen: (query: String, input: Input)?

    /// userInfo của một thông báo APNs ⇒ (truy vấn, tham số) để mở.
    static func target(_ userInfo: [AnyHashable: Any]) -> (query: String, input: Input)? {
        guard let open = userInfo["open"] as? [String: Any], let query = open["query"] as? String else { return nil }
        let raw = open["input"] as? [String: Any] ?? [:]
        let data = (try? JSONSerialization.data(withJSONObject: raw)) ?? Data("{}".utf8)
        return (query, (try? JSONDecoder().decode(Input.self, from: data)) ?? [:])
    }
}

/// Người dùng không còn đọc được luồng (đổi người phụ trách, bị mời khỏi phòng) — kênh tín hiệu dừng hẳn, không nối lại.
struct TalkGone: Error {}
/// Phiên đăng nhập hết hạn / bị thu hồi — kênh dừng, người dùng đăng nhập lại; KHÔNG phải mất quyền đọc luồng (tin đang hiện giữ nguyên).
struct TalkAuth: Error {}

extension API {
    private func post(_ path: String, _ body: Input, key: String? = nil) async throws -> Data {
        var req = URLRequest(url: base.appending(path: path))
        req.httpMethod = "POST"
        identify(&req)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let key { req.setValue(key, forHTTPHeaderField: "Idempotency-Key") }
        req.httpBody = try JSONEncoder().encode(body)
        let (d, r) = try await URLSession.shared.data(for: req)
        guard let code = (r as? HTTPURLResponse)?.statusCode, code < 500 else { throw APIError(message: "Máy chủ lỗi") }
        return d
    }

    func notices(limit: Int = 60) async throws -> NoticeInbox {
        let env = try JSONDecoder().decode(Envelope<NoticeInbox>.self, from: try await post("/v1/queries/core.notifications", ["limit": .number(Double(limit))]))
        guard env.status == "ok", let out = env.output else { throw APIError(message: env.error) }
        return out
    }

    func markNoticesRead(_ ids: [Int]) async {
        _ = try? await post("/v1/commands/core.read_notifications", ["ids": .array(ids.map { .number(Double($0)) })], key: UUID().uuidString)
    }

    func registerDevice(token: String, sandbox: Bool, active: Bool) async {
        _ = try? await post("/v1/commands/core.register_device",
                            ["token": .string(token), "env": .string(sandbox ? "sandbox" : "production"), "active": .bool(active)],
                            key: UUID().uuidString)
    }

    /// Tín hiệu "hộp thông báo có thay đổi" (SSE). Kết thúc khi mất kết nối — bên gọi tự nối lại.
    func noticeSignals() -> AsyncThrowingStream<Void, Error> { signals("/v1/notifications/stream") }

    /// Tín hiệu "luồng trao đổi có tin mới" (0046) — chỉ mã, app tự đọc lại luồng qua quyền người dùng. Người dùng mất quyền đọc luồng ⇒ server
    /// báo `gone` (hoặc 404 khi nối) ⇒ kênh kết thúc bằng lỗi ``TalkGone`` — bên gọi thôi nối lại, thôi hiện tin (0115).
    func talkSignals(_ threadId: String) -> AsyncThrowingStream<Void, Error> { signals("/v1/talk/\(threadId)/stream", goneOn404: true) }

    /// Tín hiệu hộp tin của chính người dùng (0115): luồng mình theo dõi có tin / sửa / thu hồi, hoặc mình đọc ở máy khác. Chỉ mang mã luồng —
    /// server kiểm lại quyền đọc từng luồng trước khi báo.
    func talkInboxSignals() -> AsyncThrowingStream<Void, Error> { signals("/v1/talk/stream") }

    /// Gửi MỘT tệp vào trao đổi (0115): một tệp = một tin, chữ đang gõ thành chú thích. multipart/form-data — chú thích nằm trong THÂN, không lên
    /// URL / nhật ký truy cập. Server kiểm cỡ + loại theo nội dung; cùng khoá ⇒ một tin.
    func talkUpload(_ data: Data, filename: String, subjectKind: String, subjectId: String, caption: String, replyTo: Int?,
                    key: String) async throws -> CommandResult {
        var req = URLRequest(url: base.appending(path: "/v1/talk/files"))
        req.httpMethod = "POST"
        req.timeoutInterval = 120
        identify(&req)
        let boundary = "barista-\(UUID().uuidString)"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.setValue(key, forHTTPHeaderField: "Idempotency-Key")
        var fields: [(String, String)] = [("subject_kind", subjectKind), ("subject_id", subjectId)]
        if !caption.isEmpty { fields.append(("body", caption)) }
        if let replyTo { fields.append(("reply_to", String(replyTo))) }
        var body = Data()
        for (name, value) in fields {
            body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".utf8))
        }
        // tên tệp trong header phần: bỏ dấu nháy / xuống dòng (server vẫn làm sạch + ép đuôi theo loại nhận diện)
        let safe = filename.filter { $0 != "\"" && $0 != "\r" && $0 != "\n" }
        body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(safe)\"\r\nContent-Type: application/octet-stream\r\n\r\n".utf8))
        body.append(data)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        req.httpBody = body
        let (d, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 413, let detail = try? JSONDecoder().decode([String: String].self, from: d)["detail"] { throw APIError(message: detail) }
        do { return try JSONDecoder().decode(CommandResult.self, from: d) } catch { throw APIError(message: "Không gửi được tệp (\(code))") }
    }

    private func signals(_ path: String, goneOn404: Bool = false) -> AsyncThrowingStream<Void, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var req = URLRequest(url: base.appending(path: path))
                    req.timeoutInterval = 120   // server gửi dòng giữ kết nối mỗi ~25 giây
                    identify(&req)
                    let (bytes, resp) = try await URLSession.shared.bytes(for: req)
                    let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
                    if goneOn404 && code == 404 { throw TalkGone() }
                    if code == 401 { throw TalkAuth() }
                    guard code == 200 else { throw APIError(message: "Không nối được kênh tín hiệu") }   // 503: lỗi tạm thời ⇒ bên gọi nối lại sau
                    for try await line in bytes.lines {
                        if line == "event: changed" { continuation.yield(()) }
                        else if line == "event: gone" { throw TalkGone() }
                        else if line == "event: auth" { throw TalkAuth() }
                        else if line == "event: retry" { break }   // lỗi tạm thời phía server: kết thúc êm, bên gọi nối lại sau một lúc
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

extension Desk {
    /// Nghe kênh thông báo suốt phiên (nối lại khi rớt, chờ tăng dần tới 30 giây). Gọi một lần sau ``load()``.
    func startNotices() {
        startTalk()
        guard noticeLoop == nil else { return }
        noticeLoop = Task { [weak self] in
            var wait = 2.0
            while !Task.isCancelled {
                guard let signals = self?.api.noticeSignals() else { return }
                do {
                    for try await _ in signals {
                        wait = 2
                        await self?.refreshNotices(announce: true)
                    }
                } catch {}
                try? await Task.sleep(for: .seconds(wait))
                wait = min(wait * 2, 30)
                await self?.refreshNotices(announce: true)   // mất kết nối lúc có thông báo ⇒ vẫn không sót
            }
        }
    }

    func stopNotices() {
        noticeLoop?.cancel()
        noticeLoop = nil
        talkLoop?.cancel()
        talkLoop = nil
    }

    /// Nghe kênh hộp tin (0115) suốt phiên: tin mới / sửa / thu hồi ở luồng mình theo dõi, hoặc đọc ở máy khác ⇒ đọc lại ``talk.inbox`` (số trên
    /// menu, dòng tin cuối). Gói Trao đổi tắt / không quyền ⇒ server trả 404, vòng nghe chờ lâu rồi thử lại (bật gói giữa phiên vẫn bắt được).
    func startTalk() {
        guard talkLoop == nil else { return }
        talkLoop = Task { [weak self] in
            var wait = 2.0
            while !Task.isCancelled {
                guard let signals = self?.api.talkInboxSignals() else { return }
                do {
                    for try await _ in signals {
                        wait = 2
                        await self?.refreshTalk()
                    }
                } catch is TalkAuth {
                    return   // phiên hết hạn: thôi nghe tới khi đăng nhập lại (bắt đầu phiên mới gọi lại startTalk)
                } catch {}
                try? await Task.sleep(for: .seconds(wait))
                wait = min(wait * 2, 60)
            }
        }
    }

    /// Đọc lại hộp. ``announce``: thông báo MỚI (chưa thấy lần trước) hiện lên một dòng nhắc ở đáy — lần tải đầu thì không.
    func refreshNotices(announce: Bool) async {
        guard let inbox = try? await api.notices() else { return }
        let fresh = inbox.items.filter { $0.id > lastNoticeId && !$0.read }
        if announce, lastNoticeId > 0, let first = fresh.first {
            show(fresh.count > 1 ? "\(first.title) (+\(fresh.count - 1))" : first.title)
        }
        lastNoticeId = max(lastNoticeId, inbox.items.map(\.id).max() ?? 0)
        notices = inbox.items
        unread = inbox.unread
        await refreshTalk()
        #if os(iOS)
        try? await UNUserNotificationCenter.current().setBadgeCount(inbox.unread)   // số trên biểu tượng app = số chưa đọc thật
        #endif
    }

    func openNotice(_ n: Notice) {
        showNotices = false
        if !n.read {
            Task { await api.markNoticesRead([n.id]); await refreshNotices(announce: false) }
        }
        // nhắc tên / trao đổi mới ⇒ mở đúng luồng ở "Tin nhắn" (Mac: hồ sơ tự bung ra bên phải; iPhone: tab Tin nhắn), như bấm dòng đó trong danh sách
        if let o = n.open, o.query == "talk.thread", let item = talk?.items.first(where: { $0.subject_id == o.input["subject_id"]?.text }) {
            openTalk(item)
            return
        }
        #if os(macOS)
        if section == .messages { messagesPane = .list }   // mở thẻ ⇒ bên phải về khung làm việc
        #endif
        if let o = n.open { Task { await openTarget(query: o.query, input: o.input, title: n.title) } }
    }

    func markAllNoticesRead() {
        Task { await api.markNoticesRead([]); await refreshNotices(announce: false) }
    }

    /// Mở thứ một thông báo chỉ tới: màn ghim (không tham số) hoặc thẻ của đúng bản ghi — vẫn bằng quyền người xem.
    func openTarget(query: String, input: Input, title: String, reveal: Bool = true) async {
        if input.isEmpty, let spec = screen(for: query) ?? (manifest?.inbox?.query == query ? manifest?.inbox : nil) {
            withAnimation(.snappy) { open(.screen(spec), show: reveal) }
            return
        }
        do {
            let page = try await api.query(query, input)
            let key = input.keys.sorted().map { "\($0)=\(input[$0]?.text ?? "")" }.joined(separator: "&")
            let card = ChatCard(id: "open:\(query)?\(key)", kind: "page", title: page.title, subtitle: nil, query: query, input: input,
                                page: page, command: nil, fields: nil)
            withAnimation(.snappy) { open(.card(card), show: reveal) }
        } catch {
            show(error.localizedDescription, isError: true)
        }
    }

    /// iPhone: đăng ký máy nhận đẩy cho người đang đăng nhập (gọi mỗi lần mở app — Apple có thể đổi mã).
    func registerPush(token: String) async {
        pushToken = token
        #if DEBUG
        let sandbox = true
        #else
        let sandbox = false
        #endif
        await api.registerDevice(token: token, sandbox: sandbox, active: true)
    }

    /// Đăng xuất / đổi người: máy này thôi nhận thông báo của người cũ.
    func unregisterPush() async {
        stopNotices()
        guard let token = pushToken else { return }
        await api.registerDevice(token: token, sandbox: true, active: false)
    }
}

/// Chuông ở đầu cột trợ lý: số chưa đọc; bấm ⇒ danh sách (Mac: khung nổi; iPhone: tấm trượt lên).
struct NoticeBell: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        @Bindable var desk = desk
        Button { desk.showNotices.toggle() } label: {
            // chuông trong ô vuông gradient + MỘT số đỏ (bell.badge tự có chấm đỏ ⇒ báo hai lần)
            IconTile(symbol: desk.unread > 0 ? "bell.fill" : "bell", tint: .orange, size: 30)
                .overlay(alignment: .topTrailing) {
                    if desk.unread > 0 {
                        Text(desk.unread > 99 ? "99+" : "\(desk.unread)")
                            .font(.system(size: 10, weight: .bold)).monospacedDigit().foregroundStyle(.white)
                            .padding(.horizontal, 4).frame(minWidth: 16, minHeight: 16)
                            .background(Capsule().fill(Color.bad))
                            .offset(x: 6, y: -4)
                    }
                }
        }
        .buttonStyle(.plain)
        .help(desk.unread > 0 ? "\(desk.unread) thông báo chưa đọc" : "Thông báo")
        .popover(isPresented: $desk.showNotices, arrowEdge: .bottom) { NoticeList().environment(desk) }
    }
}

struct NoticeList: View {
    @Environment(Desk.self) private var desk
    /// Nằm trong cột "Tin nhắn" (Mac): đầu cột do cột vẽ, danh sách giãn theo cột.
    var embedded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !embedded {
                HStack {
                    Text("Thông báo").font(.t16)
                    Spacer()
                    if desk.unread > 0 { Button("Đánh dấu đã đọc hết") { desk.markAllNoticesRead() }.buttonStyle(.borderless).font(.t12) }
                }
                .padding(.horizontal, 16).padding(.vertical, 12)
                Divider()
            }
            if desk.notices.isEmpty {
                ContentUnavailableView("Chưa có thông báo", systemImage: "bell.slash",
                                       description: Text("Việc mới, lead mới, phiếu chờ duyệt, hồ sơ bị trả lại sẽ hiện ở đây."))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(desk.notices) { n in
                            Button { desk.openNotice(n) } label: { row(n) }.buttonStyle(.plain)
                            Divider().padding(.leading, 52)
                        }
                    }
                }
            }
        }
        #if os(macOS)
        .frame(width: embedded ? nil : 400, height: embedded ? nil : 480)
        .frame(maxWidth: embedded ? .infinity : nil, maxHeight: embedded ? .infinity : nil)
        #else
        .presentationDetents([.medium, .large])
        #endif
        .task { await desk.refreshNotices(announce: false) }
    }

    private func row(_ n: Notice) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: symbol(n.kind))
                .font(.system(size: 13, weight: .semibold)).foregroundStyle(.white)
                .frame(width: 28, height: 28)
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(tint(n.kind)))
            VStack(alignment: .leading, spacing: 3) {
                Text(n.title).font(n.read ? .t14 : .t14m).foregroundStyle(.primary).multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                if !n.body.isEmpty {
                    Text(n.body).font(.t12).foregroundStyle(.secondary).lineLimit(4).fixedSize(horizontal: false, vertical: true)
                }
                Text(Fmt.datetime(n.created_at)).font(.t12).foregroundStyle(.tertiary)
            }
            Spacer(minLength: 4)
            if !n.read { Circle().fill(Color.accent).frame(width: 8, height: 8).padding(.top, 6) }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .contentShape(Rectangle())
        .background(n.read ? Color.clear : Color.accent.opacity(0.05))
    }

    private func symbol(_ kind: String) -> String {
        switch kind {
        case "lead", "lead_queue": "person.crop.circle.badge.plus"
        case "lead_recalled": "arrow.uturn.backward.circle"
        case "approval", "approval_done": "checkmark.seal"
        case "returned": "exclamationmark.arrow.triangle.2.circlepath"
        case "due": "clock.badge.exclamationmark"
        default: "checklist"
        }
    }

    private func tint(_ kind: String) -> Color {
        switch kind {
        case "lead", "lead_queue": .accent
        case "lead_recalled", "returned", "due": .bad
        case "approval", "approval_done": .warn
        default: .ok
        }
    }
}
