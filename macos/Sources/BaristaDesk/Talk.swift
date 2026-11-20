import SwiftUI
import UniformTypeIdentifiers

/// Khối "Trao đổi" (quyết định 0046, 0115): tin nhắn gắn vào một hồ sơ / khách, một phòng, hoặc tin riêng 1–1. Server quyết ai đọc được (ai thấy đối
/// tượng) và nút nào hiện; app chỉ vẽ. @nhắc chỉ nhắc được người vốn xem được hồ sơ — server từ chối kèm lý do. "Hỏi trợ lý" / "Tóm tắt" là hỏi
/// RIÊNG (quyền mình); muốn cả luồng thấy câu trả lời thì bấm "Chia sẻ vào trao đổi" dưới câu trả lời (thẻ chỉ là tham chiếu — người mở chạy lại bằng
/// quyền của họ). Mất quyền đọc luồng ⇒ server báo `gone`, khối thôi hiện tin.

/// Thẻ số liệu chia sẻ trong trao đổi: ô vuông màu theo module + tên + gợi ý "số theo quyền của bạn" — bấm mở, chạy lại bằng quyền người mở.
struct SharedCardChip: View {
    let ref: ThreadBlock.CardRef
    let action: () -> Void
    /// Nút trên thẻ (vd Duyệt / Từ chối, Tham gia / Rời cuộc gọi) — chạy bằng quyền người bấm; server chỉ đưa nút người đó được bấm.
    var onAction: ((ActionSpec) -> Void)? = nil
    @State private var hover = false

    private var status: String {
        guard let live = ref.live else { return "Số theo quyền của bạn" }
        return live.gone ? "Bạn không xem được thẻ này" : (live.subtitle.isEmpty ? "Số theo quyền của bạn" : live.subtitle)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            chip
            if let acts = ref.live?.actions, !acts.isEmpty, let onAction {
                FlowRow(spacing: 6) {   // điện thoại hẹp: nút xuống dòng thay vì tràn khỏi bong bóng
                    ForEach(acts) { a in ActionButton(action: a) { onAction(a) } }
                }
            }
        }
    }

    private var chip: some View {
        let style = ModuleStyle.of(ref.query)
        return Button(action: action) {
            HStack(spacing: 8) {
                IconTile(symbol: style.symbol, tint: style.tint, size: 26)
                VStack(alignment: .leading, spacing: 1) {
                    Text(ref.shownTitle).font(.t12m).lineLimit(1)
                    Text(status).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(2)
                }
                Image(systemName: "arrow.up.right").font(.system(size: 9, weight: .bold)).foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 8).padding(.vertical, 6)
            .background(RoundedRectangle(cornerRadius: 11, style: .continuous).fill(hover ? Color.surface2 : Color.surface))
            .overlay(RoundedRectangle(cornerRadius: 11, style: .continuous).strokeBorder(Color.primary.opacity(0.1)))
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help("Mở thẻ — số liệu chạy lại theo quyền của bạn")
    }
}

/// Nút server gửi (trên thẻ sống, trên khối trao đổi): màu theo ``style`` server khai — app không tự quyết nút nào nguy hiểm.
private struct ActionButton: View {
    let action: ActionSpec
    let run: () -> Void

    var body: some View {
        Button(action.title, action: run)
            .buttonStyle(.borderedProminent).controlSize(.small)
            .tint(action.style == "destructive" ? .red : (action.style == "primary" || action.style == "instant") ? .accentColor : .gray)
    }
}

struct ThreadBlock: Decodable, Hashable {
    struct CardRef: Decodable, Hashable {
        let query: String; let input: [String: JSON]; let title: String
        /// Thẻ sống (0089): server chạy lại truy vấn của thẻ bằng quyền NGƯỜI XEM — trạng thái hiện tại + nút người đó được bấm.
        struct Live: Decodable, Hashable {
            let gone: Bool; let subtitle: String; let actions: [ActionSpec]
            /// Tên thẻ theo người xem (vd "A gọi cho bạn") — trường thêm, server cũ không gửi ⇒ trống, giữ tên lưu lúc chia sẻ.
            var headline: String? = nil
        }
        let live: Live?
        /// Tên theo người xem do server quyết (thẻ cuộc gọi: "A gọi cho bạn" / "Bạn đang gọi B"); không có thì tên trung tính lưu lúc chia sẻ.
        var shownTitle: String {
            guard let live, !live.gone, let h = live.headline, !h.isEmpty else { return title }
            return h
        }
    }
    struct File: Decodable, Hashable, Identifiable {
        let id, filename, content_type: String
        let size_bytes: Int
        let is_image: Bool
    }
    struct Reply: Decodable, Hashable { let id: Int; let author, body: String; let recalled: Bool }
    struct Reaction: Decodable, Hashable { let emoji: String; let count: Int; let mine: Bool; let names: [String] }
    /// Dấu dưới tin — server chỉ gửi cho người thấy thứ dấu dẫn tới; bấm mở thẻ đó.
    struct Mark: Decodable, Hashable { let title: String; var detail: String? = nil; var tone: String? = nil; var open: OpenRef? = nil }
    struct Msg: Decodable, Hashable, Identifiable {
        let id: Int
        let author: String
        let mine: Bool
        let body: String
        let at: String
        let mentions_me: Bool
        let card: CardRef?
        // 0115 — trường thêm, server cũ không gửi ⇒ mặc định
        var edited: Bool? = nil
        var recalled: Bool? = nil
        var pinned: Bool? = nil
        var reply: Reply? = nil
        var attachments: [File]? = nil
        var reactions: [Reaction]? = nil
        var can_edit: Bool? = nil
        var history: OpenRef? = nil
        /// Điểm cắm ``talk.message_actions``: nút gói khác góp cho tin này (vd "Tạo việc") + dấu nhỏ (vd "Đã tạo việc") — đã lọc theo quyền người xem.
        var actions: [ActionSpec]? = nil
        var marks: [Mark]? = nil

        var isRecalled: Bool { recalled ?? false }
        var files: [File] { attachments ?? [] }
    }
    struct Pinned: Decodable, Hashable, Identifiable { let id: Int; let author, body, pinned_by: String }
    struct Assist: Decodable, Hashable { let title, prompt: String }
    /// Tham chiếu mở một trang (lịch sử tin).
    struct OpenRef: Decodable, Hashable { let query: String; let input: [String: JSON] }

    let title: String
    let subject_kind, subject_id, subject_title: String
    let thread_id: String?
    let last_read: Int
    let can_post: Bool
    let people: [Option]
    let messages: [Msg]
    /// Nút gói khác góp lên khối (điểm cắm ``talk.thread_actions`` — vd "Gọi" của gói Gọi video, 0114), đã lọc theo quyền người xem.
    var actions: [ActionSpec]? = nil
    // 0115 — trường thêm
    var kind_label: String? = nil
    var has_more: Bool? = nil
    var has_newer: Bool? = nil
    var focus_id: Int? = nil
    var pinned: [Pinned]? = nil
    var muted: Bool? = nil
    var reaction_choices: [String]? = nil
    var can_attach: Bool? = nil
    var file_max: Int? = nil
    var file_kinds: String? = nil
    var assist: [Assist]? = nil
    var notice: String? = nil
    /// 0128: tab "Tệp" — trang thẻ (tab Ảnh / Tài liệu / Link…) server mô tả; nil khi luồng chưa có tin. Mac mở như mọi trang.
    var files: OpenRef? = nil

    var isRoom: Bool { subject_kind == "talk.room" }
    var isDirect: Bool { subject_kind == "talk.direct" }
}

/// Hộp trao đổi của tôi (``talk.inbox``): luồng tôi theo dõi / được nhắc, mới nhất trước — cột "Tin nhắn" trên Mac.
struct TalkInbox: Decodable {
    struct Item: Decodable, Identifiable, Hashable {
        let thread_id, subject_kind, subject_id, kind_label, title, last_author, last_body: String
        let last_mine: Bool
        let at: String
        let unread, mentioned: Int
        // 0115 — trường thêm
        var muted: Bool? = nil
        /// Nhóm ở cột "Tin nhắn" — server quyết ("direct" / "room" / "record").
        var section: String? = nil
        var inactive: Bool? = nil
        var id: String { thread_id.isEmpty ? subject_kind + subject_id : thread_id }
        var preview: String {
            if inactive == true { return "Đã rời công ty — chỉ đọc lại được" }
            return last_body.isEmpty ? (isRoom || isDirect ? "Chưa có tin — gửi lời chào đầu tiên" : "") : "\(last_mine ? "Bạn" : last_author): \(last_body)"
        }
        var isRoom: Bool { subject_kind == "talk.room" }
        var isDirect: Bool { subject_kind == "talk.direct" }

        /// Dòng tạm cho luồng chưa có trong hộp (vừa mở tin riêng / phòng mới) — luồng tự tải phần còn lại.
        static func stub(kind: String, id: String, title: String = "") -> Item {
            Item(thread_id: "", subject_kind: kind, subject_id: id, kind_label: "", title: title, last_author: "", last_body: "", last_mine: false,
                 at: "", unread: 0, mentioned: 0)
        }
    }
    let unread, mentioned: Int
    let items: [Item]
    var can_start_direct: Bool? = nil
}

extension Desk {
    /// Đọc lại hộp trao đổi. Gói Trao đổi tắt / không có quyền ⇒ giữ nil, cột "Tin nhắn" chỉ còn trợ lý + thông báo.
    func refreshTalk() async {
        if let t: TalkInbox = try? await api.data("talk.inbox") { talk = t }
    }

    /// Mở một luồng ở cột "Tin nhắn" (Mac). Thông báo trỏ vào chính luồng này (bị nhắc tên / có trao đổi mới) coi như đã đọc — một việc
    /// không đếm hai lần ở hai nơi.
    func openTalk(_ item: TalkInbox.Item) {
        section = .messages
        messagesPane = .thread(item)
        let ids = notices.filter { !$0.read && $0.open?.query == "talk.thread" && $0.open?.input["subject_id"]?.text == item.subject_id }.map(\.id)
        if !ids.isEmpty { Task { await api.markNoticesRead(ids); await refreshNotices(announce: false) } }
    }

    /// Lệnh vừa tạo phòng / mở tin riêng trả loại + mã đối tượng ⇒ mở luôn cuộc đó.
    func openTalk(from output: JSON?) {
        guard case .object(let o) = output, case .string(let kind) = o["subject_kind"], case .string(let id) = o["subject_id"] else { return }
        Task { await refreshTalk() }
        openTalk(talk?.items.first { $0.subject_id == id } ?? .stub(kind: kind, id: id))
    }

    /// Đích của thông báo đẩy / ``BARISTA_OPEN``: một luồng trao đổi (không kèm tin cụ thể) ⇒ mở ở "Tin nhắn" như bấm dòng đó; còn lại ⇒ thẻ.
    func openTargetOrTalk(query: String, input: Input, title: String) async {
        if query == "talk.thread", input["around"] == nil, case .string(let kind)? = input["subject_kind"], case .string(let id)? = input["subject_id"] {
            if talk == nil { await refreshTalk() }
            openTalk(talk?.items.first { $0.subject_id == id } ?? .stub(kind: kind, id: id, title: title))
            return
        }
        await openTarget(query: query, input: input, title: title)
    }

    func closeTalk() {
        messagesPane = .list
        Task { await refreshTalk() }
    }

    /// Luồng đang mở ở cột "Tin nhắn" — khối "Trao đổi" cùng luồng trên thẻ bên phải thu lại (không hiện một luồng hai lần).
    var talkOpenSubject: String? {
        if section == .messages, case .thread(let item) = messagesPane { return item.subject_id }
        return nil
    }
}

/// Nơi câu trả lời của trợ lý có thể được chia sẻ vào (đặt khi bấm "Hỏi trợ lý" trong một khối trao đổi).
struct ShareTarget: Hashable {
    let subjectKind, subjectId, title: String
}

enum Talk {
    /// Tên lưu tệp: bỏ ký tự điều khiển / định dạng vô hình (đảo chiều chữ U+202E…) và ép đuôi theo loại server đã nhận diện — "ảnh‮lmth.png" không
    /// thành tệp ".html" trên máy (server cũng đã làm sạch; đây là lớp thứ hai ở nơi tệp chạm hệ điều hành).
    static func safeName(_ raw: String, contentType: String) -> String {
        let visible = String(String.UnicodeScalarView(raw.unicodeScalars.filter {
            $0.value >= 0x20 && $0.value != 0x7F && $0.properties.generalCategory != .format && $0 != "/" && $0 != ":" && $0 != "\\"
        })).trimmingCharacters(in: CharacterSet(charactersIn: " ."))
        let mime = contentType.split(separator: ";").first.map { String($0).trimmingCharacters(in: .whitespaces) } ?? ""
        guard let ext = UTType(mimeType: mime)?.preferredFilenameExtension else { return visible.isEmpty ? "tep" : visible }
        let stem = (visible as NSString).deletingPathExtension
        return "\(stem.isEmpty ? "tep" : stem).\(ext)"
    }

    /// Loại tệp chọn được để gửi — đúng danh sách server nhận (server vẫn kiểm theo nội dung, ``talk.sniff``).
    static let pickable: [UTType] = [.image, .pdf, .plainText, .commaSeparatedText]
        + ["doc", "docx", "xls", "xlsx", "ppt", "pptx"].compactMap { UTType(filenameExtension: $0) }

    static func size(_ bytes: Int) -> String {
        bytes >= 1024 * 1024 ? String(format: "%.1f MB", Double(bytes) / 1_048_576).replacingOccurrences(of: ".", with: ",")
                             : "\(max(1, bytes / 1024)) KB"
    }
}

struct ThreadView: View {
    let block: ThreadBlock
    let open: (OpenRequest) -> Void
    /// Luồng chiếm cả cột "Tin nhắn" (tin cuộn, ô nhập ghim đáy) thay vì một khối nằm trong thẻ.
    var fill = false
    @Environment(Desk.self) private var desk: Desk?
    @State private var data: ThreadBlock?
    @State private var older: [ThreadBlock.Msg] = []
    @State private var olderMore: Bool?
    @State private var gone = false
    @State private var around: Int?
    @State private var draft = ""
    @State private var mentions: [Option] = []
    @State private var replyTo: ThreadBlock.Msg?
    @State private var editing: ThreadBlock.Msg?
    @State private var sending = false
    @State private var error: String?
    @State private var sendKey = UUID().uuidString
    @State private var acting: ActionSpec?
    @State private var recalling: ThreadBlock.Msg?
    /// Tab "Tệp" đang mở trên iPhone (phủ cả màn — ``filesCover``); Mac mở như mọi trang ở ngăn bên.
    @State private var filesOpen: FilesRef?

    private var t: ThreadBlock { data ?? block }
    private var messages: [ThreadBlock.Msg] {
        let ids = Set(t.messages.map(\.id))
        return older.filter { !ids.contains($0.id) } + t.messages
    }
    private var hasMore: Bool { olderMore ?? t.has_more ?? false }
    private var firstUnread: Int? { messages.first { !$0.mine && !$0.isRecalled && $0.id > t.last_read }?.id }

    var body: some View {
        Group {
            if gone { goneView } else if fill { filled } else { boxed }
        }
        .onChange(of: block) { data = nil; older = []; olderMore = nil; gone = false; around = block.focus_id }
        .onAppear {
            if around == nil { around = block.focus_id }
            // dev / chụp trình giả lập: BARISTA_THREAD_FILES=1 ⇒ mở luôn tab "Tệp" của luồng
            if threadFilesInCover, ProcessInfo.processInfo.environment["BARISTA_THREAD_FILES"] == "1", let f = t.files { filesOpen = FilesRef(ref: f) }
        }
        .sheet(item: $acting) { a in
            if let desk {
                FormSheet(api: desk.api, action: a, row: nil, initial: a.preset) { msg in
                    desk.show(msg)
                    Task {   // thẻ của mình tải lại ngay; báo máy khác đang mở luồng tính lại thẻ bằng quyền của họ
                        await refresh()
                        if let id = t.thread_id {
                            _ = try? await desk.api.command("talk.cards_changed", ["thread_id": .string(id)], key: UUID().uuidString)
                        }
                    }
                }
            }
        }
        .filesCover(item: $filesOpen) { f in
            ThreadFilesView(ref: f.ref, onJump: { id in filesOpen = nil; jump(to: id) }, onOpen: { req in
                filesOpen = nil
                // tấm phủ đóng xong mới mở thẻ (không trình bày hai thứ cùng lúc)
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { open(req) }
            }, onClose: { filesOpen = nil })
        }
        .confirmationDialog("Thu hồi tin này?", isPresented: Binding(get: { recalling != nil }, set: { if !$0 { recalling = nil } }),
                            presenting: recalling) { m in
            Button("Thu hồi", role: .destructive) { Task { await run("talk.recall_message", ["message_id": .number(Double(m.id))]) } }
        } message: { _ in
            Text("Mọi người sẽ thấy “Tin đã được thu hồi”. Nội dung gốc vẫn được lưu lại, chỉ anh/chị và người được giao quyền xem lại được.")
        }
        .task(id: t.thread_id) { await listen() }
        .task(id: t.messages.last?.id) { await markRead() }
    }

    private var goneView: some View {
        VStack(spacing: 4) {
            Text("Bạn không còn xem được trao đổi này").font(.t14m)
            Text("Có thể hồ sơ đã đổi người phụ trách hoặc anh/chị đã rời phòng.").font(.t12).foregroundStyle(.secondary)
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity, maxHeight: fill ? .infinity : nil)
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(fill ? Color.clear : Color.surface2))
    }

    private var emptyHint: some View {
        Text(t.isRoom ? "Chưa có tin nào trong phòng — gửi lời chào đầu tiên."
                      : t.isDirect ? "Chưa có tin nào — chỉ hai người đọc được cuộc này."
                                   : "Chưa có trao đổi nào. Hỏi đồng nghiệp ngay trên hồ sơ này — @nhắc tên để họ nhận thông báo.")
            .font(.t12).foregroundStyle(.secondary)
    }

    /// Tin liền nhau của cùng một người gộp thành một lượt nói: tên + ảnh chỉ ở tin đầu (như iMessage / Lark).
    private var messageList: some View {
        VStack(alignment: .leading, spacing: 4) {
            if hasMore {
                Button("Xem tin cũ hơn") { Task { await loadOlder() } }
                    .buttonStyle(.bordered).controlSize(.small).frame(maxWidth: .infinity)
            }
            ForEach(Array(messages.enumerated()), id: \.element.id) { i, m in
                if m.id == firstUnread { divider.padding(.vertical, 6) }
                let first = i == 0 || messages[i - 1].author != m.author || m.id == firstUnread || m.reply != nil
                bubble(m, first: first).padding(.top, first && i > 0 ? 8 : 0).id(m.id)
            }
            if t.has_newer == true {
                Button { jumpLatest() } label: { Label("Về tin mới nhất", systemImage: "arrow.down") }
                    .buttonStyle(.bordered).controlSize(.small).frame(maxWidth: .infinity)
            }
        }
    }

    /// Tô màu "@tên" trong tin để thấy ngay ai được nhắc.
    static func styled(_ body: String) -> AttributedString {
        var out = AttributedString()
        var rest = body[...]
        while let r = rest.range(of: #"@[^\s,.:;!?]+"#, options: .regularExpression) {
            out += AttributedString(String(rest[..<r.lowerBound]))
            var tag = AttributedString(String(rest[r]))
            tag.foregroundColor = Color.accent
            tag.font = .t14m
            out += tag
            rest = rest[r.upperBound...]
        }
        out += AttributedString(String(rest))
        return out
    }

    private var errorLine: some View {
        Group { if let error { Label(error, systemImage: "exclamationmark.triangle").font(.t12).foregroundStyle(Color.bad) } }
    }

    /// Đầu khối: nút gói khác góp (vd "Gọi"), "Tóm tắt" (trợ lý riêng), tắt tiếng. Hẹp (iPhone) ⇒ kéo ngang thay vì tràn.
    private var tools: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 6) { Spacer(minLength: 0); toolItems }
            ScrollView(.horizontal, showsIndicators: false) { HStack(spacing: 6) { toolItems } }
        }
    }

    @ViewBuilder private var toolItems: some View {
        Group {
            if let assist = t.assist, !assist.isEmpty, !messages.isEmpty, desk != nil {
                Menu {
                    Section("Trợ lý trả lời riêng anh/chị") {
                        ForEach(assist, id: \.title) { a in Button(a.title) { askPrivately(a.prompt) } }
                    }
                } label: { Label("Tóm tắt", systemImage: "sparkles") }
                .menuStyle(.button).buttonStyle(.bordered).controlSize(.small).fixedSize()
                .help("Nhờ trợ lý tóm tắt — chỉ anh/chị thấy")
            }
            if fill, t.thread_id != nil, t.can_post {
                Button { Task { await mute() } } label: { Image(systemName: t.muted == true ? "bell.slash" : "bell") }
                    .buttonStyle(.bordered).controlSize(.small)
                    .help(t.muted == true ? "Bật lại thông báo" : "Tắt thông báo trao đổi này")
            }
            if let f = t.files {
                Button {
                    if threadFilesInCover { filesOpen = FilesRef(ref: f) } else { open(OpenRequest(title: "Tệp", query: f.query, input: f.input)) }
                } label: { Label("Tệp", systemImage: "paperclip") }
                    .buttonStyle(.bordered).controlSize(.small)
                    .help("Ảnh, tài liệu, link đã gửi")
            }
            ForEach(t.actions ?? []) { a in ActionButton(action: a) { acting = a } }
        }
    }

    private var hasTools: Bool { !(t.actions ?? []).isEmpty || !(t.assist ?? []).isEmpty || (fill && t.thread_id != nil) || t.files != nil }

    private var boxed: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "bubble.left.and.bubble.right").foregroundStyle(Color.accent)
                Text(t.title).font(.t16)
                if !messages.isEmpty { Text("\(messages.count)").font(.t12).foregroundStyle(.secondary) }
                Spacer(minLength: 6)
                if hasTools { tools.layoutPriority(-1) }
            }
            if messages.isEmpty { emptyHint }
            messageList
            if let n = t.notice, !n.isEmpty { Text(n).font(.t12).foregroundStyle(.secondary) }
            if t.can_post { composer }
            errorLine
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.surface2))
    }

    private var filled: some View {
        VStack(spacing: 0) {
            if hasTools {
                tools
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .overlay(alignment: .bottom) { Rectangle().fill(Color.hairline).frame(height: 1) }
            }
            ScrollViewReader { proxy in
                if let p = t.pinned, let top = p.first { pinnedBar(top, all: p, proxy: proxy) }
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        if messages.isEmpty { emptyHint }
                        messageList
                        Color.clear.frame(height: 1).id("end")
                    }
                    .padding(.horizontal, 16).padding(.vertical, 14)
                }
                .defaultScrollAnchor(.bottom)
                .onChange(of: t.messages.last?.id) {
                    if t.has_newer != true { withAnimation(.snappy) { proxy.scrollTo("end", anchor: .bottom) } }
                }
                .onAppear { if let f = t.focus_id { proxy.scrollTo(f, anchor: .center) } }
                .onChange(of: t.focus_id) { if let f = t.focus_id { withAnimation(.snappy) { proxy.scrollTo(f, anchor: .center) } } }
                .scrollDismissesKeyboard(.interactively)
            }
            VStack(alignment: .leading, spacing: 6) {
                errorLine
                if let n = t.notice, !n.isEmpty { Text(n).font(.t12).foregroundStyle(.secondary) }
                if t.can_post { composer } else if (t.notice ?? "").isEmpty { Text("Bạn chỉ xem được trao đổi này.").font(.t12).foregroundStyle(.secondary) }
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background { WA.bar.ignoresSafeArea() }   // iPhone: nền ô nhắn kéo xuống tận mép dưới (vạch Home)
            .overlay(alignment: .top) { Rectangle().fill(Color.hairline).frame(height: 1) }
        }
    }

    /// Dải tin đã ghim: tin ghim mới nhất; bấm ⇒ cuộn tới tin (nếu đang có trên màn); nhiều tin ⇒ menu xem hết.
    private func pinnedBar(_ top: ThreadBlock.Pinned, all: [ThreadBlock.Pinned], proxy: ScrollViewProxy) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "pin.fill").font(.system(size: 11)).foregroundStyle(Color.accent)
            Button { withAnimation(.snappy) { proxy.scrollTo(top.id, anchor: .center) } } label: {
                (Text(top.author + ": ").font(.t12m) + Text(top.body).font(.t12)).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain).help("\(top.pinned_by) ghim")
            if all.count > 1 {
                Menu("\(all.count) tin ghim") {
                    ForEach(all) { p in Button("\(p.author): \(p.body)") { withAnimation(.snappy) { proxy.scrollTo(p.id, anchor: .center) } } }
                }
                .menuStyle(.button).buttonStyle(.borderless).font(.t12m).fixedSize()
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(Color.surface)
        .overlay(alignment: .bottom) { Rectangle().fill(Color.hairline).frame(height: 1) }
    }

    private var divider: some View {
        HStack(spacing: 8) {
            Rectangle().fill(Color.bad.opacity(0.4)).frame(height: 1)
            Text("Tin mới").font(.t12m).foregroundStyle(Color.bad)
            Rectangle().fill(Color.bad.opacity(0.4)).frame(height: 1)
        }
    }

    @ViewBuilder private func bubble(_ m: ThreadBlock.Msg, first: Bool = true) -> some View {
        HStack(alignment: .top, spacing: 8) {
            if m.mine { Spacer(minLength: 40) }
            if fill && !m.mine && !t.isDirect {
                if first { Initials(name: m.author, size: 28).padding(.top, 18) } else { Color.clear.frame(width: 28, height: 1) }
            }
            VStack(alignment: m.mine ? .trailing : .leading, spacing: 3) {
                if !m.mine && first && !t.isDirect { Text(m.author).font(.t12m).foregroundStyle(.secondary) }
                if let r = m.reply {
                    (Text(r.author + ": ").font(.t12m) + Text(r.body).font(.t12)).foregroundStyle(.secondary).lineLimit(1)
                        .padding(.leading, 8)
                        .overlay(alignment: .leading) { Rectangle().fill(Color.accent.opacity(0.5)).frame(width: 2) }
                }
                if !m.body.isEmpty || m.isRecalled { text(m) }
                ForEach(m.files) { f in TalkFileView(file: f) }
                if let c = m.card {
                    SharedCardChip(ref: c, action: { open(OpenRequest(title: c.shownTitle, query: c.query, input: c.input)) }, onAction: { acting = $0 })
                }
                if let ks = m.marks, !ks.isEmpty { marks(ks) }
                if let rs = m.reactions, !rs.isEmpty { reactions(m, rs) }
                HStack(spacing: 4) {
                    if m.pinned == true { Image(systemName: "pin.fill").font(.system(size: 8)) }
                    Text(fill ? Fmt.stamp(m.at) : Fmt.datetime(m.at))
                    if m.edited == true { Text("· đã sửa") }
                }
                .font(.system(size: 10)).foregroundStyle(.tertiary)
            }
            .contextMenu { menu(m) }
            if !m.mine { Spacer(minLength: 40) }
        }
    }

    private func text(_ m: ThreadBlock.Msg) -> some View {
        Group {
            if m.isRecalled { Text(m.body).foregroundStyle(.secondary) } else { Text(Self.styled(m.body)) }
        }
        .font(.t14).selectableOnMac()
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous)
            .fill(m.isRecalled ? Color.clear : m.mine ? Color.accent.opacity(0.14) : (fill ? WA.incoming : Color.surface)))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
            .strokeBorder(m.isRecalled ? Color.primary.opacity(0.18) : m.mentions_me ? Color.warn.opacity(0.7) : t.focus_id == m.id ? Color.accent.opacity(0.6) : .clear,
                          style: StrokeStyle(lineWidth: 1.5, dash: m.isRecalled ? [4, 3] : [])))
    }

    private func marks(_ ks: [ThreadBlock.Mark]) -> some View {
        HStack(spacing: 4) {
            ForEach(Array(ks.enumerated()), id: \.offset) { _, k in
                let tint = k.tone == "ok" ? Color.ok : Color.accent
                let detail = (k.detail ?? "").isEmpty ? nil : k.detail   // tin đã thu hồi: server bỏ chữ chi tiết
                Button {
                    if let o = k.open { open(OpenRequest(title: detail ?? k.title, query: o.query, input: o.input)) }
                } label: {
                    Label(detail.map { "\(k.title) · \($0)" } ?? k.title, systemImage: "checkmark.circle")
                        .font(.t12).lineLimit(1).foregroundStyle(tint)
                        .padding(.horizontal, 7).padding(.vertical, 2)
                        .overlay(Capsule().strokeBorder(tint.opacity(0.4)))
                }
                .buttonStyle(.plain).disabled(k.open == nil).help(detail ?? k.title)
            }
        }
    }

    private func reactions(_ m: ThreadBlock.Msg, _ rs: [ThreadBlock.Reaction]) -> some View {
        HStack(spacing: 4) {
            ForEach(rs, id: \.emoji) { r in
                Button { Task { await react(m, r.emoji) } } label: {
                    Text("\(r.emoji) \(r.count)").font(.t12).monospacedDigit()
                        .padding(.horizontal, 7).padding(.vertical, 2)
                        .background(Capsule().fill(r.mine ? Color.accent.opacity(0.15) : Color.surface))
                        .overlay(Capsule().strokeBorder(r.mine ? Color.accent.opacity(0.5) : Color.primary.opacity(0.1)))
                }
                .buttonStyle(.plain).disabled(!t.can_post).help(r.names.joined(separator: ", "))
            }
        }
    }

    /// Chuột phải / nhấn giữ một tin: chỉ những việc server cho phép (cảm xúc, trả lời, ghim, sửa / thu hồi tin của mình, lịch sử).
    @ViewBuilder private func menu(_ m: ThreadBlock.Msg) -> some View {
        let write = t.can_post && !m.isRecalled
        if write {
            MenuStrip { ForEach(t.reaction_choices ?? [], id: \.self) { e in Button(e) { Task { await react(m, e) } } } }
            Divider()
            Button { editing = nil; replyTo = m } label: { Label("Trả lời", systemImage: "arrowshape.turn.up.left") }
            Button { Task { await run("talk.pin", ["message_id": .number(Double(m.id)), "pinned": .bool(!(m.pinned ?? false))]) } } label: {
                Label(m.pinned == true ? "Bỏ ghim" : "Ghim tin", systemImage: "pin")
            }
        }
        if m.can_edit == true {
            Button { replyTo = nil; editing = m; draft = m.body; sendKey = UUID().uuidString } label: { Label("Sửa tin", systemImage: "pencil") }
        }
        if let h = m.history {
            Button("Xem lịch sử") { open(OpenRequest(title: "Lịch sử tin nhắn", query: h.query, input: h.input)) }
        }
        if !m.body.isEmpty && !m.isRecalled { Button("Chép chữ") { copyToPasteboard(m.body) } }
        if let acts = m.actions, !acts.isEmpty {   // nút gói khác góp cho tin (vd "Tạo việc") — form điền sẵn, chạy bằng quyền người bấm
            Divider()
            ForEach(acts) { a in Button { acting = a } label: { Label(a.title, systemImage: "checklist") } }
        }
        if m.can_edit == true {
            Divider()
            Button("Thu hồi tin", role: .destructive) { recalling = m }
        }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let ctx = editing ?? replyTo {
                HStack(spacing: 6) {
                    Image(systemName: editing != nil ? "pencil" : "arrowshape.turn.up.left").foregroundStyle(Color.accent)
                    (Text(editing != nil ? "Đang sửa tin: " : "Trả lời \(ctx.author): ").font(.t12m) + Text(ctx.body).font(.t12))
                        .lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
                    Button { cancelContext() } label: { Image(systemName: "xmark") }.buttonStyle(.borderless).help("Bỏ (Esc)")
                }
                .padding(.horizontal, 8).padding(.vertical, 5)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Color.surface))
            }
            if !mentions.isEmpty {
                FlowRow(spacing: 6) {
                    ForEach(mentions, id: \.value) { p in
                        Button { mentions.removeAll { $0.value == p.value } } label: {
                            Label("@\(p.label)", systemImage: "xmark").font(.t12m).labelStyle(.titleAndIcon)
                        }
                        .buttonStyle(.bordered).controlSize(.small)
                    }
                }
            }
            // cùng kiểu ô nhập của trợ lý: ô bo tròn, nút tròn — một ngôn ngữ cho mọi chỗ gõ tin
            HStack(alignment: .bottom, spacing: 8) {
                if !t.isDirect && editing == nil {
                    Menu {
                        ForEach(t.people.filter { p in !mentions.contains { $0.value == p.value } }, id: \.value) { p in
                            Button(p.label) { mentions.append(p) }
                        }
                    } label: { circleIcon("at") }
                    .menuStyle(.button).buttonStyle(.plain).menuIndicator(.hidden).fixedSize()
                    .help(t.isRoom ? "Nhắc tên — người trong phòng" : "Nhắc tên — chỉ nhắc được người xem được hồ sơ này")
                }
                if t.can_attach == true && editing == nil {
                    AttachButton(types: Talk.pickable, onPick: { data, name in Task { await sendFile(data, filename: name) } },
                                 onError: { error = $0 }) { circleIcon("paperclip") }
                        .buttonStyle(.plain).menuStyle(.button).menuIndicator(.hidden).fixedSize().disabled(sending)
                        .help("Gửi tệp — \(t.file_kinds ?? "ảnh, PDF, Word, Excel")\(t.file_max.map { ", tối đa \($0 / 1_048_576) MB" } ?? ""). Chữ đang gõ thành chú thích.")
                }
                TextField(editing != nil ? "Sửa tin…" : t.isRoom ? "Nhắn vào phòng…" : t.isDirect ? "Nhắn cho \(t.subject_title)…" : "Nhắn trên hồ sơ này…",
                          text: $draft, axis: .vertical)
                    .textFieldStyle(.plain).font(.t14).lineLimit(1...5)
                    .onSubmit { Task { await send() } }
                    .onKeyPress(.escape) { if editing != nil || replyTo != nil { cancelContext(); return .handled }; return .ignored }
                    .padding(.vertical, 8).padding(.horizontal, 12)
                    .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(WA.field))
                    .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(Color.primary.opacity(0.1)))
                let empty = draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                Button { Task { await send() } } label: {
                    Image(systemName: editing != nil ? "checkmark" : "arrow.up").font(.system(size: 14, weight: .bold)).foregroundStyle(.white)
                        .frame(width: 32, height: 32).background(Circle().fill(empty ? Color.secondary.opacity(0.3) : Color.accent))
                }
                .buttonStyle(.plain)
                .disabled(sending || empty)
                .help(editing != nil ? "Lưu tin đã sửa (↩)" : "Gửi (↩)")
                if desk != nil {
                    Button { askPrivately(nil) } label: { circleIcon("sparkles") }
                        .buttonStyle(.plain)
                        .help("Hỏi trợ lý riêng — muốn mọi người cùng thấy thì bấm chia sẻ dưới câu trả lời")
                }
            }
        }
    }

    private func circleIcon(_ symbol: String) -> some View {
        Image(systemName: symbol).font(.system(size: 13, weight: .semibold)).foregroundStyle(.secondary)
            .frame(width: 32, height: 32).background(Circle().fill(Color.primary.opacity(0.06)))
    }

    private func cancelContext() {
        if editing != nil { draft = "" }
        editing = nil
        replyTo = nil
    }

    private func askPrivately(_ prompt: String?) {
        guard let desk else { return }
        desk.shareTarget = ShareTarget(subjectKind: t.subject_kind, subjectId: t.subject_id, title: t.subject_title)
        // câu hỏi đi vào ô nhắn của TRỢ LÝ ⇒ chuyển sang trợ lý (đang ở "Tin nhắn" / khung làm việc thì ô đó không hiện)
        desk.section = .assistant
        desk.narrowShowsCanvas = false
        desk.ask(prompt ?? "Về \(t.subject_title): ")
    }

    // MARK: lệnh — mọi thao tác ghi đi qua /v1/commands (một cửa), server kiểm quyền

    private func finish(_ r: CommandResult) async -> Bool {
        guard r.status == "ok" else {
            error = r.error.isEmpty ? r.status : r.error
            return false
        }
        error = nil
        sendKey = UUID().uuidString
        await refresh()
        await desk?.refreshTalk()
        return true
    }

    private func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let desk, !text.isEmpty, !sending else { return }
        sending = true
        defer { sending = false }
        do {
            // cùng khoá khi bấm lại lúc mạng chập ⇒ không thành hai tin
            let r: CommandResult
            if let m = editing {
                r = try await desk.api.command("talk.edit_message", ["message_id": .number(Double(m.id)), "body": .string(text)], key: sendKey)
            } else {
                var input: Input = ["subject_kind": .string(t.subject_kind), "subject_id": .string(t.subject_id), "body": .string(text),
                                    "mentions": .array(mentions.map { .string($0.value) })]
                if let reply = replyTo { input["reply_to"] = .number(Double(reply.id)) }
                r = try await desk.api.command("talk.post", input, key: sendKey)
            }
            if await finish(r) {
                draft = ""
                mentions = []
                replyTo = nil
                editing = nil
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func sendFile(_ data: Data, filename: String) async {
        guard let desk, !sending else { return }
        if let max = t.file_max, data.count > max { error = "Tệp quá \(max / 1_048_576) MB — không gửi được"; return }
        sending = true
        defer { sending = false }
        do {
            let caption = draft.trimmingCharacters(in: .whitespacesAndNewlines)
            let r = try await desk.api.talkUpload(data, filename: filename, subjectKind: t.subject_kind, subjectId: t.subject_id,
                                                 caption: caption, replyTo: replyTo?.id, key: sendKey)
            if await finish(r) {
                draft = ""
                replyTo = nil
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func run(_ command: String, _ input: Input) async {
        guard let desk else { return }
        do { _ = await finish(try await desk.api.command(command, input, key: UUID().uuidString)) } catch { self.error = error.localizedDescription }
    }

    private func react(_ m: ThreadBlock.Msg, _ emoji: String) async {
        let mine = (m.reactions ?? []).contains { $0.emoji == emoji && $0.mine }
        await run("talk.react", ["message_id": .number(Double(m.id)), "emoji": .string(emoji), "on": .bool(!mine)])
    }

    private func mute() async {
        guard let id = t.thread_id else { return }
        await run("talk.mute", ["thread_id": .string(id), "muted": .bool(!(t.muted ?? false))])
    }

    // MARK: đọc

    private func fetch(_ extra: Input = [:]) async -> ThreadBlock? {
        guard let desk else { return nil }
        var input: Input = ["subject_kind": .string(t.subject_kind), "subject_id": .string(t.subject_id)]
        for (k, v) in extra { input[k] = v }
        do {
            guard case .card(let c) = try await desk.api.query("talk.thread", input) else { return nil }
            for case .thread(let fresh) in c.blocks { return fresh }
        } catch let e as APIError where e.status == 404 {
            gone = true   // mất quyền xem hồ sơ / bị mời khỏi phòng ⇒ thôi hiện tin cũ đang có trên màn
        } catch {}
        return nil
    }

    /// Đọc lại luồng (qua quyền người dùng) — khối tự làm mới được kể cả khi nằm trong thẻ tĩnh. Mở từ kết quả tìm ⇒ giữ cửa sổ quanh tin đó.
    private func refresh() async {
        if let fresh = await fetch(around.map { ["around": .number(Double($0))] } ?? [:]) { data = fresh }
    }

    private func loadOlder() async {
        guard let first = messages.first?.id, let page = await fetch(["before": .number(Double(first))]) else { return }
        let ids = Set(older.map(\.id))
        older = page.messages.filter { !ids.contains($0.id) } + older
        olderMore = page.has_more ?? false
    }

    /// Mở đúng một tin (từ tab "Tệp"): cửa sổ tin quanh tin đó, cuộn tới và viền sáng (``focus_id`` của server).
    private func jump(to id: Int) {
        around = id
        older = []
        olderMore = nil
        Task { await refresh() }
    }

    private func jumpLatest() {
        around = nil
        older = []
        olderMore = nil
        Task { await refresh() }
    }

    private func listen() async {
        guard let desk, let id = t.thread_id else { return }
        var wait = 2.0
        while !Task.isCancelled {
            do {
                for try await _ in desk.api.talkSignals(id) { wait = 2; await refresh() }
            } catch is TalkGone {
                gone = true
                return
            } catch is TalkAuth {
                error = "Phiên đăng nhập đã hết hạn — đăng nhập lại để nhận tin mới"   // tin đang hiện giữ nguyên
                return
            } catch {}   // lỗi tạm thời / mất mạng: nối lại sau, không xoá tin đang hiện
            try? await Task.sleep(for: .seconds(wait))
            wait = min(wait * 2, 30)
        }
    }

    private func markRead() async {
        guard let desk, let id = t.thread_id, let last = t.messages.last?.id, last > t.last_read, t.has_newer != true else { return }
        _ = try? await desk.api.command("talk.mark_read", ["thread_id": .string(id), "upto": .number(Double(last))], key: UUID().uuidString)
        await desk.refreshTalk()   // số chưa đọc ở cột "Tin nhắn" / thanh bên trái
    }
}

/// Tệp trong tin: ảnh tải qua API (kèm danh tính — server kiểm quyền đọc luồng MỖI lần) rồi hiện ngay; loại khác là thẻ tên + cỡ, bấm ⇒ lưu.
private struct TalkFileView: View {
    let file: ThreadBlock.File
    @Environment(Desk.self) private var desk: Desk?
    @State private var image: Image?
    @State private var failed = false

    var body: some View {
        if file.is_image && !failed {
            Group {
                if let image {
                    image.resizable().scaledToFit().frame(maxWidth: 280, maxHeight: 240)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.primary.opacity(0.1)))
                        .onTapGesture { Task { await save() } }
                        .help("\(file.filename) · \(Talk.size(file.size_bytes)) — bấm để xem / lưu")
                } else {
                    RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.surface).frame(width: 160, height: 110)
                        .overlay(ProgressView().controlSize(.small))
                }
            }
            .task(id: file.id) { await load() }
        } else {
            Button { Task { await save() } } label: {
                HStack(spacing: 8) {
                    Image(systemName: "doc.fill").foregroundStyle(Color.accent)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(file.filename).font(.t12m).lineLimit(1)
                        Text(Talk.size(file.size_bytes)).font(.t12).foregroundStyle(.secondary)
                    }
                    Image(systemName: "arrow.down.circle").foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 10).padding(.vertical, 7)
                .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.surface))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Color.primary.opacity(0.1)))
            }
            .buttonStyle(.plain).help("Xem / tải tệp")
        }
    }

    private func load() async {
        guard let desk, image == nil else { return }
        if let data = try? await desk.api.download("/v1/talk/files/\(file.id)"), let img = platformImage(data) { image = img } else { failed = true }
    }

    private func save() async {
        guard let desk else { return }
        do {
            let data = try await desk.api.download("/v1/talk/files/\(file.id)")
            if let name = openDownloadedFile(data, name: Talk.safeName(file.filename, contentType: file.content_type)) { desk.show("Đã lưu \(name)") }
        } catch {
            desk.show(error.localizedDescription, isError: true)
        }
    }
}

/// Mục cho tấm phủ "Tệp" (cần Identifiable).
struct FilesRef: Identifiable {
    let id = UUID()
    let ref: ThreadBlock.OpenRef
}
