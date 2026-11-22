import Observation
import SwiftUI

/// Thứ đang mở trên khung làm việc: thẻ trợ lý vừa tạo, hoặc một màn ghim (bảng / form).
enum CanvasItem: Identifiable {
    case card(ChatCard)
    case screen(ScreenSpec)

    var id: String {
        switch self {
        case .card(let c): "card:\(c.id)"
        case .screen(let s): "screen:\(s.query):\(s.key)"
        }
    }

    var title: String {
        switch self {
        case .card(let c): c.isProposal ? "Đề xuất: \(c.title)" : c.title
        case .screen(let s): s.title
        }
    }

    var style: (symbol: String, tint: Color) {
        switch self {
        case .card(let c): c.isProposal ? ("wand.and.stars", .purple) : ModuleStyle.of(c.op)
        case .screen(let s): (s.icon, s.tintColor)
        }
    }
}

/// Mục chọn trên thanh bên trái (Mac, Rail.swift): trợ lý · việc (bàn việc, 0094) · tin nhắn · một phân hệ của manifest.
enum RailSection: Hashable {
    case assistant, desk, messages
    case workspace(String)
}

/// Cột "Tin nhắn": danh sách, hay đang mở thông báo hệ thống / một luồng trao đổi.
enum MessagesPane: Hashable {
    case list, notices
    case thread(TalkInbox.Item)
}

/// Trạng thái app: một người, một công ty. Trợ lý ở cột trái, khung làm việc ở giữa — như Barista Desk.
@Observable @MainActor
final class Desk {
    let api: API
    var manifest: Manifest?
    var loadError: String?

    var messages: [ChatMessage] = []
    var live: LiveTurn?
    var threadId: String?
    var threads: [ThreadSummary] = []
    var suggestions: [String] = []
    var attention: [Attention] = []
    var morning: [ChatCard] = []   // đề xuất soạn sẵn sáng nay (0074 A)
    var draft = ""
    var draftImages: [Attachment] = []   // ảnh dán / kéo vào ô chat, gửi kèm câu hỏi kế tiếp
    let voice = LiveVoice()
    var focusChat = 0  // tăng ⇒ ô chat nhận con trỏ (vd sau "Hỏi trợ lý về tài liệu này")

    /// Đưa câu mở đầu vào ô chat, con trỏ ở cuối — người gõ tiếp câu hỏi của mình.
    func ask(_ prompt: String) {
        draft = prompt
        focusChat += 1
    }

    var canvas: [CanvasItem] = []
    var activeId: String?
    var confirmed: [String: String] = [:]  // id thẻ đề xuất → kết quả sau khi người bấm
    var proposalEdits: [String: Input] = [:]  // phần người đang sửa trên thẻ đề xuất — giữ khi chuyển thẻ rồi quay lại
    var inboxCount = 0
    /// Tăng mỗi khi một lệnh chạy xong ở bất kỳ đâu (``refreshInbox``) — bàn việc tải lại theo số này.
    var changes = 0
    /// Bàn việc (0094): một lần tải dùng chung cho cột "Việc" và số trên nút ở thanh trái.
    var deskPage: DeskPage?
    var deskFailed = false

    func refreshDesk() async {
        do { deskPage = try await api.desk(); deskFailed = false } catch { deskFailed = true }
    }
    var toast: Toast?
    var narrowShowsCanvas = false  // cửa sổ hẹp: đang xem khung làm việc thay vì trợ lý
    var isNarrow = false           // cửa sổ hẹp / iPhone: một ngăn (MainView đặt theo bề ngang)
    var switchPersona: () -> Void = {}
    // thông báo (Notices.swift, quyết định 0044)
    var notices: [Notice] = []
    var unread = 0
    var showNotices = false
    var shareTarget: ShareTarget?   // "Hỏi trợ lý" từ một khối trao đổi ⇒ câu trả lời có nút "Chia sẻ vào trao đổi" (0046)
    // chế độ Siri nổi trên Mac (SiriFloat.swift): cửa sổ chính lui đi, khối Siri + ngăn thẻ nổi
    var voiceMode = false
    var voiceOpened: [String] = []   // thứ trợ lý mở ra trong lúc nói chuyện ⇒ tab của ngăn thẻ
    // khung kiểu Lark trên Mac (Rail.swift): thanh bên trái chọn mục → cột giữa đổi theo; khung làm việc bên phải giữ nguyên
    var section: RailSection = .assistant
    var messagesPane: MessagesPane = .list
    var talk: TalkInbox?
    var showSearch = false
    var chatInspector = false   // ngăn phụ cạnh cuộc chat (thẻ phòng / hồ sơ / thẻ mở từ tin)
    @ObservationIgnored var present: ((Bool) -> Void)?
    @ObservationIgnored var noticeLoop: Task<Void, Never>?
    @ObservationIgnored var talkLoop: Task<Void, Never>?   // kênh hộp tin (0115) — chạy cùng kênh thông báo
    @ObservationIgnored var lastNoticeId = 0
    @ObservationIgnored var pushToken: String?

    /// Mọi màn ghim theo phân hệ (menu "Tất cả màn") và vài màn mở nhanh trên đầu khung làm việc.
    var allScreens: [(title: String, icon: String, screens: [ScreenSpec])] {
        (manifest?.workspaces ?? []).map { ($0.title, $0.icon, $0.screens) }
    }
    var quickScreens: [ScreenSpec] {
        let spaces = manifest?.workspaces ?? []
        if let pins = manifest?.pins, !pins.isEmpty {
            return pins.compactMap { pin in spaces.first { $0.key == pin.workspace }?.screens.first { $0.key == pin.key } }
        }
        return Array(spaces.flatMap { $0.screens.filter { $0.group != "Nhập liệu" }.prefix(2) }.prefix(5))
    }
    /// Màn ghim trên đầu khung THEO CHỖ ĐANG ĐỨNG: trong một phân hệ ⇒ ghim của phân hệ đó; trợ lý / việc / tin nhắn ⇒ bộ ghim chung;
    /// trình tạo app (0098) ⇒ không có (khung dành cho app đang dựng).
    var contextPins: [ScreenSpec] {
        if builder != nil { return [] }
        guard case .workspace(let key) = section, let ws = manifest?.workspaces.first(where: { $0.key == key }) else { return quickScreens }
        let keys = Set((manifest?.pins ?? []).filter { $0.workspace == key }.map(\.key))
        return ws.screens.filter { keys.contains($0.key) }
    }
    var subtitle: String {
        guard let m = manifest else { return "" }
        return [m.user.role_label, m.user.team ?? "", m.tenant.name].filter { !$0.isEmpty }.joined(separator: " · ")
    }

    init(api: API) { self.api = api }

    var firstName: String { manifest?.user.name.split(separator: " ").last.map(String.init) ?? "bạn" }
    var active: CanvasItem? { canvas.first { $0.id == activeId } }

    /// Màn ghim trùng truy vấn với thẻ (để thẻ trợ lý có luôn nút thao tác / lọc / xuất của màn đó).
    func screen(for query: String) -> ScreenSpec? {
        // màn việc chỉ ở bàn việc (0094) vẫn là màn mở được — tìm cả ở đó
        (manifest?.workspaces.flatMap(\.screens) ?? []).first { $0.query == query } ?? manifest?.desk?.first { $0.query == query }
    }

    func load() async {
        do {
            #if os(iOS)
            manifest = try await api.manifest().forPhone()
            #else
            manifest = try await api.manifest()
            #endif
            loadError = nil
        } catch let e as APIError where e.status == 403 && api.token == nil {
            switchPersona()   // dev: vai đã lưu không còn là thành viên công ty (vừa nạp lại DB) ⇒ về màn chọn vai, không kẹt ở màn lỗi
            return
        } catch {
            loadError = error.localizedDescription
            return
        }
        async let s = try? api.suggestions()
        async let t = try? api.threads()
        async let a = try? api.attention()
        async let mo = try? api.morning()
        suggestions = await s ?? []
        threads = await t ?? []
        attention = await a ?? []
        morning = await mo ?? []
        await refreshInbox()
        await refreshTalk()
        // Mở app là thấy việc của mình ngay (màn ghim đầu của vai) — không để khung làm việc trống chờ người hỏi.
        if canvas.isEmpty, let home = quickScreens.first { open(.screen(home)) }
    }

    /// Bấm một mục "cần chú ý": mở màn đó ở khung làm việc.
    func openAttention(_ a: Attention) {
        if let spec = screen(for: a.query) { withAnimation(.snappy) { open(.screen(spec), show: true) } }
        else if let q = a.ask { send(q) }
    }

    func refreshInbox() async {
        changes += 1   // mọi đường chạy lệnh xong đều gọi đây ⇒ bàn việc + số trên nút "Việc" tải lại (0094)
        Task { await refreshDesk() }
        guard let inbox = manifest?.inbox, case .table(let t)? = try? await api.query(inbox.query) else { return }
        inboxCount = Int(t.kpis.first?.value.double ?? 0)
    }

    // MARK: trò chuyện

    func send(_ override: String? = nil) {
        var text = (override ?? draft).trimmingCharacters(in: .whitespacesAndNewlines)
        let images = override == nil ? draftImages : []
        if text.isEmpty && !images.isEmpty { text = "Cập nhật từ ảnh này giúp em" }
        guard !text.isEmpty, live == nil else { return }
        draft = ""
        draftImages = []
        messages.append(ChatMessage(role: "user", text: text, images: images.map(\.data)))
        live = LiveTurn()
        Task { await stream(text, images: images) }
    }

    /// Thêm ảnh vào câu hỏi kế tiếp (tối đa 3 — server cũng chặn).
    func attach(_ a: Attachment) {
        guard draftImages.count < 3 else { show("Tối đa 3 ảnh mỗi câu hỏi", isError: true); return }
        draftImages.append(a)
        focusChat += 1
    }

    /// Áp MỘT sự kiện của lượt trả lời vào ``live`` — dùng chung cho chat chữ (stream) và giọng nói (backend chạy lượt chat hộ).
    /// Trả chữ trả lời cuối khi gặp "done" / "error", còn lại nil.
    func apply(_ ev: StreamEvent) -> String? {
        switch ev.type {
        case "status": live?.status = ev.text ?? ""
        case "step":
            guard let n = ev.n else { return nil }
            let step = Step(n: n, tool: ev.tool ?? "", title: ev.title ?? "", status: ev.status ?? "running", error: ev.error,
                            summary: ev.summary, card_id: ev.card_id)
            withAnimation(.spring(duration: 0.35)) {
                if let i = live?.steps.firstIndex(where: { $0.n == n }) { live?.steps[i] = step } else { live?.steps.append(step) }
            }
        case "card":
            if let card = ev.card {
                live?.cards.append(card)
                // trình tạo app (0098): thẻ mở app ⇒ nhớ mã app (các lượt sau làm trên app này); app hiện trên khung khi xong lượt
                if builder != nil, card.query == "apps.open", let id = card.input?["app_id"]?.text, !id.isEmpty {
                    builder?.appId = id
                    live?.cards.removeAll { $0.query == "apps.open" && $0.id != card.id }   // chat giữ thẻ app mới nhất
                    Task { await showBuilderApp(id) }   // HIỆN DẦN: app lên khung sau từng bước
                    return nil
                }
                if builder != nil { return nil }   // trình tạo: khung bên phải dành cho app — thẻ khác chỉ ở trong chat
                // thứ trợ lý vừa đụng tới bung ra khung làm việc — trừ bảng rỗng (vẫn có trong chat để bấm xem)
                if case .table(let t)? = card.page, t.rows.isEmpty { return nil }
                // có thẻ gọn ⇒ xem ngay trong chat, "Mở" mới ra khung (0094 đợt 2). Siri nổi (Mac): cột chat đã lui ⇒ thẻ gọn không ai thấy — luôn ra ngăn thẻ.
                if card.preview != nil && !voiceMode { return nil }
                withAnimation(.snappy) { open(.card(card)) }
            }
        case "text": live?.text += ev.text ?? ""
        case "text_reset": live?.text = ""
        case "done":
            if let id = ev.thread_id { threadId = id }
            return ev.text ?? live?.text ?? ""
        case "error": return "⚠︎ " + (ev.message ?? "Lỗi")
        default: break
        }
        return nil
    }

    func finishTurn(_ reply: String) async {
        let turn = live ?? LiveTurn()
        messages.append(ChatMessage(role: "assistant", text: reply.isEmpty ? turn.text : reply, cards: turn.cards, steps: turn.steps))
        live = nil
        threads = (try? await api.threads()) ?? threads
        if let id = builder?.appId { await showBuilderApp(id) }
    }

    // MARK: trình tạo app (0098 — kiểu Claude Design)

    /// Mã lượt đang chạy (gửi kèm lúc hỏi) ⇒ "Dừng" huỷ đúng lượt này ở server; phần đã làm vẫn lưu.
    var turnKey: String?
    func stopTurn() {
        guard let key = turnKey else { return }
        turnKey = nil
        Task { try? await api.stopTurn(key) }
    }

    /// ``origin``: mở trình tạo từ đâu (vd phân hệ "App của tôi") — thanh bên vẫn sáng mục đó, "Xong" quay lại đúng chỗ.
    struct Builder { var appId: String?; var origin: RailSection = .assistant }
    /// Chế độ trình tạo app: lời dặn + công cụ riêng, cuộc trò chuyện riêng; nil = trợ lý cá nhân. Vào thì cất cuộc trò chuyện đang có, "Xong" trả lại.
    var builder: Builder?
    private var savedChat: (messages: [ChatMessage], threadId: String?)?
    /// Tăng ⇒ thẻ lớn trên khung dựng lại (app vừa được trình tạo sửa — tải lại dữ liệu).
    var canvasRev = 0

    func openBuilder(prompt: String? = nil, appId: String? = nil) {
        guard live == nil else { return }
        if let cur = builder, appId == nil || appId == cur.appId {   // đang ở trình tạo trên đúng app này ⇒ giữ cuộc trò chuyện
            if let p = prompt?.trimmingCharacters(in: .whitespacesAndNewlines), !p.isEmpty { send(p) }
            return
        }
        if builder == nil { savedChat = (messages, threadId) }
        builder = Builder(appId: appId, origin: builder?.origin ?? section)
        messages = []
        threadId = nil
        section = .assistant
        if let appId { Task { await showBuilderApp(appId) } }
        if let p = prompt?.trimmingCharacters(in: .whitespacesAndNewlines), !p.isEmpty { send(p) }
    }

    func exitBuilder() {
        guard live == nil else { return }
        let origin = builder?.origin
        builder = nil
        messages = savedChat?.messages ?? []
        threadId = savedChat?.threadId
        savedChat = nil
        Task { await load() }
        if let origin { section = origin }   // về đúng chỗ đã mở trình tạo; app vừa làm vẫn ở khung bên cạnh
    }
    /// Mục thanh bên đang sáng: trong trình tạo (mượn chỗ của trợ lý) ⇒ mục đã mở trình tạo, không trông như "về trợ lý thường".
    var railSelected: RailSection { builder != nil && section == .assistant ? (builder?.origin ?? section) : section }

    /// Đưa ĐÚNG app đó lên khung làm việc; đang mở ⇒ chỉ tải lại TẠI CHỖ (không nháy). App mới chưa có trong menu ⇒ tải lại menu trước.
    private func showBuilderApp(_ id: String) async {
        let find = { self.manifest?.workspaces.flatMap(\.screens).first(where: { $0.key == "app-\(id)" }) }
        if find() == nil { await load() }
        guard let spec = find() else { return }
        canvasRev += 1
        open(.screen(spec), show: true)
    }

    private func stream(_ text: String, images: [Attachment] = []) async {
        var reply = ""
        do {
            let key = UUID().uuidString
            turnKey = key
            defer { turnKey = nil }
            // hỏi từ khối trao đổi (shareTarget) ⇒ gửi kèm loại + mã đối tượng: trợ lý đọc đúng luồng đó bằng quyền người hỏi (0115)
            let subject = builder == nil ? shareTarget : nil
            for try await ev in api.chatStream(text, threadId: threadId, images: images, mode: builder == nil ? nil : "builder",
                                               appId: builder?.appId, turnKey: key, subject: subject) {
                if let r = apply(ev) { reply = r }
            }
        } catch {
            reply = "⚠︎ " + error.localizedDescription
        }
        await finishTurn(reply)
    }

    // MARK: đọc chính tả (mic trong ô nhập — kiểu ChatGPT)

    let fieldRecorder = FieldRecorder()
    var transcribing = false

    /// Bấm lần 1: ghi âm. Bấm lần 2: server CHỈ chép lời ⇒ chữ chèn vào ô nhập — người xem / sửa rồi TỰ bấm gửi (không gửi luôn).
    func toggleDictation() {
        if fieldRecorder.isRecording {
            guard let audio = fieldRecorder.stop() else { show("Ghi âm quá ngắn — giữ lâu hơn một chút", isError: true); return }
            transcribing = true
            Task {
                defer { transcribing = false }
                do {
                    let text = try await api.transcribe(audio).trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !text.isEmpty else { show("Em không nghe rõ — nói lại giúp em nhé", isError: true); return }
                    draft = draft.isEmpty ? text : draft + " " + text
                    focusChat += 1
                } catch {
                    show(error.localizedDescription, isError: true)
                }
            }
        } else {
            Task { if let problem = await fieldRecorder.start() { show(problem, isError: true) } }
        }
    }

    // MARK: giọng nói (quyết định 0042)

    /// Bật / tắt giọng nói. Câu hỏi nói ra thành một lượt chat thật ở backend; bước, thẻ, câu trả lời hiện y như gõ chữ.
    func toggleVoice() {
        if voice.isOn { voice.stop(); return }
        voice.onChatStart = { [weak self] q in
            guard let self else { return }
            self.messages.append(ChatMessage(role: "user", text: q))
            self.live = LiveTurn()
        }
        voice.onThread = { [weak self] id in self?.threadId = id }
        voice.onChat = { [weak self] raw in
            guard let self, let data = try? JSONSerialization.data(withJSONObject: raw),
                  let ev = try? JSONDecoder().decode(StreamEvent.self, from: data) else { return }
            if ev.type == "reply" || (ev.type == "error" && ev.message == nil) {   // hết lượt (backend báo "reply" / lỗi)
                let text = ev.type == "reply" ? (ev.text ?? "") : "⚠︎ " + (raw["detail"] as? String ?? "Lỗi")
                Task { await self.finishTurn(text) }
            } else if let _ = apply(ev) {
                // "done" của lượt chat: chữ trả lời chốt ở "reply" ngay sau đó
            }
        }
        voice.start(api: api, threadId: threadId)
    }

    /// Bấm quả cầu / nút sóng âm: Mac ⇒ chế độ Siri nổi (cửa sổ chính lui, khối Siri góc màn hình + ngăn thẻ cạnh phải);
    /// iPhone ⇒ khối Siri ngay trong cột chat.
    func startVoiceMode() {
        if let present, !voiceMode {
            voiceOpened = []
            voiceMode = true
            present(true)
        }
        if !voice.isOn { toggleVoice() }
    }

    /// ✕ / Esc trong khối Siri: tắt giọng nói, về cửa sổ chính (thẻ đã mở vẫn còn ở khung làm việc). Xoá cả lỗi giọng nói: ô nhập vẽ khối Siri thay
    /// mình khi còn lỗi (``ChatInput``) ⇒ giữ lỗi thì ✕ đóng khối nổi xong khối y hệt lại hiện trong cột chat và không đóng được nữa.
    func stopVoiceMode() {
        if voice.isOn { voice.stop() }
        voice.problem = nil
        if voiceMode {
            voiceMode = false
            present?(false)
        }
    }

    func newThread() {
        guard live == nil else { return }
        if builder != nil { builder = nil; savedChat = nil }   // đang ở trình tạo app ⇒ thoát, sang trò chuyện mới (không còn nút "Xong")
        messages = []
        threadId = nil
        shareTarget = nil
    }

    func openThread(_ id: String) async {
        guard live == nil, let t = try? await api.thread(id) else { return }
        if builder != nil { builder = nil; savedChat = nil }   // mở cuộc trò chuyện cũ ⇒ thoát trình tạo app
        threadId = t.id
        for turn in t.turns {
            for d in (turn.decided ?? []) + (turn.pending?.calls?.values.map { $0 } ?? []) + (turn.asides?.values.map { $0 } ?? [])
            where d.status != "waiting" {
                confirmed[d.card_id] = d.message ?? (d.status == "dismissed" ? "Đã bỏ đề xuất" : "Đã xong")
            }
        }
        messages = t.turns.map {
            ChatMessage(role: $0.role, text: $0.text, cards: $0.cards ?? [], steps: $0.steps ?? [],
                        at: $0.at.flatMap(Fmt.parse) ?? .now, imagesGone: $0.attachments?.count ?? 0)
        }
    }

    // MARK: khung làm việc

    func open(_ item: CanvasItem, show: Bool = false) {
        if show { narrowShowsCanvas = true }
        if voiceMode && !voiceOpened.contains(item.id) { voiceOpened.append(item.id) }   // Siri nổi: thẻ trượt vào ngăn cạnh phải
        if !canvas.contains(where: { $0.id == item.id }) {
            canvas.insert(item, at: 0)
            if canvas.count > 12 { canvas.removeLast() }  // giữ tối đa 12 thứ đang mở
        }
        activeId = item.id
    }

    func clearCanvas() {
        canvas = []
        activeId = nil
    }

    func close(_ id: String) {
        canvas.removeAll { $0.id == id }
        if activeId == id { activeId = canvas.first?.id }
    }

    /// Xác nhận / Bỏ một đề xuất của lượt đang dừng (0049). Trả kết quả của chính lệnh đó cho form (lỗi ⇒ form hiện lỗi, sửa rồi bấm lại);
    /// phần trợ lý chạy tiếp (nếu đã đủ quyết định) chảy vào khung chat như một lượt trả lời, chạy nền sau khi form đã đóng.
    func decide(_ card: ChatCard, confirm: Bool, input: Input, key: String) async -> CommandResult {
        await decide(card, decision: confirm ? "confirm" : "dismiss", input: input, key: key)
    }

    /// ĐỐI SOÁT (rà 22/11): đóng thẻ cần đối soát bằng nút server gửi — thẻ sáng qua ``morningDecide``, thẻ chat qua ``decide``. Không gửi lại lệnh ngoài.
    func resolve(_ card: ChatCard, decision: String) async -> CommandResult {
        if card.source == "morning" {
            do {
                let r = try await api.morningDecide(id: card.id, decision: decision, input: [:], key: UUID().uuidString)
                if r.status == "ok" || r.status == "failed" { withAnimation(.snappy) { morning.removeAll { $0.id == card.id } } }
                return r
            } catch {
                return CommandResult(status: "error", output: nil, error: error.localizedDescription, approval_id: nil, replayed: false)
            }
        }
        return await decide(card, decision: decision, input: [:], key: UUID().uuidString)
    }

    func decide(_ card: ChatCard, decision: String, input: Input, key: String) async -> CommandResult {
        let stream = api.decide(cardId: card.id, decision: decision, input: input, key: key)
        return await withCheckedContinuation { (cont: CheckedContinuation<CommandResult, Never>) in
            Task { @MainActor in
                var answered = false
                func answer(_ r: CommandResult) { if !answered { answered = true; cont.resume(returning: r) } }
                var reply = ""
                var continuing = false
                do {
                    for try await ev in stream {
                        switch ev.type {
                        case "rejected":
                            answer(CommandResult(status: ev.status ?? "invalid", output: nil, error: ev.message ?? "", approval_id: nil, replayed: false))
                        case "decided":
                            let status = ev.status == "dismissed" ? "ok" : (ev.status ?? "ok")
                            answer(CommandResult(status: status, output: nil, error: "", approval_id: nil, replayed: ev.replayed ?? false))
                            if ev.resuming == true, live == nil { continuing = true; live = LiveTurn() }
                        case "error":
                            answer(CommandResult(status: "error", output: nil, error: ev.message ?? "Lỗi", approval_id: nil, replayed: false))
                        case "card" where !continuing:
                            // trợ lý không chạy tiếp: thẻ kết quả máy chủ phát kèm (vd link gửi khách — chỉ hiện lần này) vẫn mở ra khung
                            if let card = ev.card { withAnimation(.snappy) { open(.card(card)) } }
                        default:
                            if continuing, let r = apply(ev) { reply = r }
                        }
                    }
                } catch {
                    answer(CommandResult(status: "error", output: nil, error: error.localizedDescription, approval_id: nil, replayed: false))
                }
                answer(CommandResult(status: "error", output: nil, error: "Máy chủ không trả lời", approval_id: nil, replayed: false))
                if continuing { await finishTurn(reply) }
            }
        }
    }

    /// Người bấm Xác nhận trên thẻ đề xuất ⇒ đã chạy bằng quyền người (FormSheet gọi lệnh); ghi lại vào chat.
    /// Xác nhận / Bỏ một đề xuất buổi sáng: chạy bằng quyền người bấm ở server (kiểm căn cứ), xong thì rời danh sách.
    func decideMorning(_ card: ChatCard, confirm: Bool, input: Input, key: String) async -> CommandResult {
        do {
            let r = try await api.morningDecide(id: card.id, confirm: confirm, input: input, key: key)
            if ["ok", "pending_approval", "dismissed"].contains(r.status) { withAnimation(.snappy) { morning.removeAll { $0.id == card.id } } }
            return r.status == "dismissed" ? CommandResult(status: "ok", output: nil, error: "", approval_id: nil, replayed: false) : r
        } catch {
            return CommandResult(status: "error", output: nil, error: error.localizedDescription, approval_id: nil, replayed: false)
        }
    }

    func proposalDone(_ card: ChatCard, _ message: String) {
        confirmed[card.id] = message
        messages.append(ChatMessage(role: "system", text: "✓ \(message)"))
        Task { await refreshInbox() }
    }

    func show(_ text: String, isError: Bool = false) {
        withAnimation { toast = Toast(text: text, isError: isError) }
        Task {
            try? await Task.sleep(for: .seconds(3.5))
            withAnimation { if toast?.text == text { toast = nil } }
        }
    }
}
