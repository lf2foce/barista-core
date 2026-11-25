import SwiftUI

/// Khung iPhone (0041): thanh tab dưới đáy Trợ lý · Việc · Tin nhắn — cùng ba mục đầu của thanh bên trái trên Mac (Rail.swift). Khung làm việc
/// (thẻ / màn vừa mở) phủ lên trên các tab, có nút quay lại; các tab vẫn sống bên dưới nên cuộc trò chuyện đang mở, chữ đang gõ còn nguyên khi quay về.
/// Màn RỘNG (cỡ ngang ``regular``: iPhone Duo mở ra, iPad, iPhone Max xoay ngang): tab bên trái, khung làm việc bên phải — như web. Theo cỡ ngang chứ
/// không theo hướng xoay / loại máy (Apple: màn trong của iPhone Duo không theo hướng xoay); thêm sàn 640 điểm để iPad chia đôi hẹp vẫn một ngăn.
/// Gập lại (màn ngoài) / iPhone thường ⇒ một ngăn như cũ. Mọi thứ trong tab là mã dùng chung với Mac — file này chỉ xếp chỗ.
struct PhoneShell: View {
    @Environment(Desk.self) private var desk
    @Environment(\.horizontalSizeClass) private var sizeClass

    static let minWide: CGFloat = 640

    var body: some View {
        GeometryReader { g in
            let wide = sizeClass == .regular && g.size.width >= Self.minWide
            let _ = updateNarrow(!wide)
            if wide {
                HStack(spacing: 0) {
                    PhoneTabs().frame(width: min(460, max(320, g.size.width * 0.4)))
                    Rectangle().fill(Color.hairline).frame(width: 1)
                    CanvasView().frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            } else {
                ZStack {
                    PhoneTabs()
                        .overlay(alignment: .bottom) { if !desk.narrowShowsCanvas { PhoneToast() } }
                    if desk.narrowShowsCanvas {
                        CanvasView(onBack: { withAnimation(.snappy) { desk.narrowShowsCanvas = false } })
                            .transition(.move(edge: .bottom))   // khung làm việc là một lớp phủ ⇒ trượt lên từ dưới (như sheet), không đẩy ngang như trang con
                            .zIndex(1)
                    }
                }
            }
        }
        // chế độ giọng nói toàn màn (PhoneVoice): Desk bật ``voiceMode`` qua ``present`` — iPhone chỉ cần màn phủ, không có cửa sổ để lui như Mac
        .fullScreenCover(isPresented: Binding(get: { desk.voiceMode }, set: { if !$0 { desk.stopVoiceMode() } })) {
            PhoneVoice().environment(desk)
        }
        .task {
            desk.present = { _ in }
            // dev / chụp trình giả lập: BARISTA_SECTION=desk | messages | notices | all ⇒ mở thẳng tab đó (không cần bấm)
            switch ProcessInfo.processInfo.environment["BARISTA_SECTION"] {
            case "desk": desk.section = .desk
            case "messages": desk.section = .messages
            case "notices": desk.section = .messages; desk.messagesPane = .notices
            default: break
            }
        }
    }

    private func updateNarrow(_ narrow: Bool) {
        if desk.isNarrow != narrow { DispatchQueue.main.async { desk.isNarrow = narrow } }
    }
}

/// Dòng báo ngắn (``Desk.show``) khi khung làm việc đang đóng — trên Mac / khung làm việc nó nằm ở đáy khung.
private struct PhoneToast: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        if let t = desk.toast {
            Text(t.text)
                .font(.t14m).multilineTextAlignment(.center)
                .padding(.horizontal, 14).padding(.vertical, 8)
                .foregroundStyle(t.isError ? Color.white : Color.primary)
                .background(t.isError ? AnyShapeStyle(Color.bad) : AnyShapeStyle(.regularMaterial), in: Capsule())
                .padding(.horizontal, 16).padding(.bottom, 64)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .allowsHitTesting(false)
        }
    }
}

/// Tab dưới đáy: ba mục đầu của thanh bên trái trên Mac + "Tất cả" (thay chính thanh bên — iPhone không có chỗ cho nó, ADR 0255).
private enum PhoneTab: Hashable {
    case rail(RailSection)
    case all
}

private struct PhoneTabs: View {
    @Environment(Desk.self) private var desk
    @State private var onAll = false

    /// Phân hệ (``.workspace``) chỉ có trên thanh bên trái của Mac; trên iPhone mở qua khung làm việc ⇒ coi như đang ở trợ lý.
    /// "Tất cả" không phải một ``RailSection`` (Mac không có) ⇒ giữ ở đây; có nơi khác đổi mục (thông báo đẩy mở Tin nhắn…) thì rời "Tất cả".
    private var selection: Binding<PhoneTab> {
        Binding(get: {
            if onAll { return .all }
            if case .workspace = desk.section { return .rail(.assistant) }
            return .rail(desk.section)
        }, set: { t in
            switch t {
            case .all: onAll = true
            case .rail(let s): onAll = false; desk.section = s
            }
        })
    }

    var body: some View {
        TabView(selection: selection) {
            Tab("Trợ lý", systemImage: "sparkles", value: PhoneTab.rail(.assistant)) { ChatColumn() }
            Tab("Việc", systemImage: "checklist", value: PhoneTab.rail(.desk)) { DeskColumn() }
                .badge(desk.deskPage?.count ?? 0)
            Tab("Tin nhắn", systemImage: "bubble.left.and.bubble.right", value: PhoneTab.rail(.messages)) { PhoneMessages() }
                .badge(desk.unread + (desk.talk?.unread ?? 0))
            Tab("Tất cả", systemImage: "square.grid.2x2", value: PhoneTab.all) { PhoneAllItems() }
        }
        .onChange(of: desk.section) { onAll = false }
        .task { if ProcessInfo.processInfo.environment["BARISTA_SECTION"] == "all" { onAll = true } }   // dev: chụp trình giả lập
    }
}

// MARK: - Tất cả

/// Tab "Tất cả" (rà iPhone 25/11): mọi màn theo phân hệ — đúng quyền người xem, cùng luật với thanh bên của Mac (``RailLayout``): phân hệ theo vai
/// + đã ghim lên đầu, mọi phân hệ khác gấp trong "Tất cả mục" có nút ghim / bỏ ghim. Bấm một màn ⇒ khung làm việc trượt lên.
struct PhoneAllItems: View {
    @Environment(Desk.self) private var desk
    @State private var q = ""

    private var hits: [(ws: Workspace, screen: ScreenSpec)] {
        let t = q.trimmingCharacters(in: .whitespaces)
        return (desk.manifest?.workspaces ?? []).flatMap { w in w.screens.map { (w, $0) } }
            .filter { $0.screen.title.localizedStandardContains(t) || $0.ws.title.localizedStandardContains(t) }
    }

    var body: some View {
        let layout = desk.rail
        NavigationStack {
            List {
                if !q.trimmingCharacters(in: .whitespaces).isEmpty {
                    if hits.isEmpty { Text("Không có màn nào khớp").foregroundStyle(.secondary) }
                    ForEach(hits, id: \.screen.key) { h in screenRow(h.screen, caption: h.ws.title) }
                } else {
                    if let inbox = desk.manifest?.inbox { Section { screenRow(inbox) } }
                    ForEach(layout.onRail) { w in
                        Section(w.title) { ForEach(w.screens) { screenRow($0) } }
                    }
                    if layout.grouped {
                        Section("Tất cả mục") {
                            ForEach(layout.all) { i in
                                DisclosureGroup {
                                    ForEach(i.ws.screens) { screenRow($0) }
                                } label: {
                                    HStack(spacing: 12) {
                                        IconTile(symbol: i.ws.icon, tint: i.ws.screens.first?.tintColor ?? .accent, size: 30)
                                        Text(i.ws.title).font(.t16).lineLimit(1)
                                        Spacer(minLength: 8)
                                        Button { Task { await desk.togglePin(i) } } label: {
                                            Image(systemName: i.pinned ? "pin.fill" : "pin")
                                                .font(.system(size: 17, weight: .medium))
                                                .foregroundStyle(i.pinned ? Color.accent : Color.secondary)
                                                .frame(width: 40, height: 36)
                                        }
                                        .buttonStyle(.borderless)
                                        .accessibilityLabel(i.pinned ? "Bỏ ghim \(i.ws.title)" : "Ghim \(i.ws.title) lên đầu")
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Tất cả")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $q, placement: .navigationBarDrawer(displayMode: .always), prompt: "Tìm màn…")
        }
    }

    private func screenRow(_ s: ScreenSpec, caption: String? = nil) -> some View {
        Button { withAnimation(.snappy) { desk.open(.screen(s), show: true) } } label: {
            HStack(spacing: 12) {
                IconTile(symbol: s.icon, tint: s.tintColor, size: 30)
                VStack(alignment: .leading, spacing: 1) {
                    Text(s.title).font(.t16).foregroundStyle(Color.primary).lineLimit(1)
                    if let c = caption ?? (s.group.isEmpty ? nil : s.group) { Text(c).font(.t12).foregroundStyle(.secondary).lineLimit(1) }
                }
            }
        }
    }
}

// MARK: - Giọng nói toàn màn

/// Chạm quả cầu / nút sóng âm (rà iPhone 25/11, theo tab Phê Nâu): chế độ giọng nói TOÀN MÀN, nền tối, ẩn thanh tab — khối Siri ở đỉnh như Dynamic
/// Island mở rộng (quả cầu, phụ đề, micro, bàn phím, đóng), thứ trợ lý mở ra trong lúc nói hiện ngay bên dưới (nhiều thẻ ⇒ hàng tab). Mac: khối nổi +
/// ngăn thẻ cạnh phải (SiriFloat.swift). ✕ ⇒ ``Desk.stopVoiceMode`` ⇒ đóng màn, thẻ vẫn còn ở khung làm việc.
struct PhoneVoice: View {
    @Environment(Desk.self) private var desk
    @State private var picked: String?

    private var items: [CanvasItem] { desk.voiceOpened.compactMap { id in desk.canvas.first { $0.id == id } } }

    var body: some View {
        let items = self.items
        let current = items.first { $0.id == picked } ?? items.last
        VStack(spacing: 12) {
            SiriPill()
            if items.count > 1 {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(items) { it in
                            Button { withAnimation(.snappy) { picked = it.id } } label: {
                                HStack(spacing: 6) {
                                    IconTile(symbol: it.style.symbol, tint: it.style.tint, size: 20)
                                    Text(it.title).font(.t12m).lineLimit(1)
                                }
                                .padding(.horizontal, 10).frame(height: 32)
                                .background(Capsule().fill(it.id == current?.id ? Color.white.opacity(0.18) : Color.white.opacity(0.07)))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            if let current {
                CardFrame(item: current, onClose: { withAnimation(.snappy) { desk.voiceOpened.removeAll { $0 == current.id } } })
                    .id(current.id)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            } else {
                Spacer()
                VStack(spacing: 8) {
                    Text("Cứ nói tự nhiên").font(.t20)
                    Text("Số liệu, danh sách, lịch… trợ lý mở ra sẽ hiện ở đây").font(.t14).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                .padding(.horizontal, 24)
                Spacer()
            }
        }
        .padding(.horizontal, 10).padding(.top, 6).padding(.bottom, 4)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.black.ignoresSafeArea())
        .environment(\.colorScheme, .dark)
        .animation(.snappy, value: desk.voiceOpened)
    }
}

// MARK: - Tin nhắn

/// Đường đi trong tab "Tin nhắn": danh sách → một cuộc trò chuyện (``around`` = mở quanh một tin, từ kết quả tìm) / thông báo hệ thống.
enum PhoneRoute: Hashable {
    case thread(TalkInbox.Item, around: Int?)
    case notices
}

/// Tab "Tin nhắn" — cùng nội dung cột "Tin nhắn" của Mac (MessagesColumn): thông báo, phiếu chờ duyệt, tin riêng, phòng, trao đổi trên hồ sơ;
/// server quyết nhóm (``section``) và số chưa đọc. Đường đi đồng bộ với ``Desk.messagesPane`` ⇒ thông báo đẩy / kết quả tìm / "Nhắn riêng"
/// mở đúng cuộc như trên Mac.
struct PhoneMessages: View {
    @Environment(Desk.self) private var desk
    @State private var path: [PhoneRoute] = []
    @State private var creating: ActionSpec?
    @State private var q = ""
    @State private var searchOn = false
    @State private var found: [Hit] = []
    @State private var searching = false

    struct Hit: Identifiable {
        let id: String
        let group, title, subtitle: String
        let open: CardPage.Open
    }

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if q.trimmingCharacters(in: .whitespaces).isEmpty { inbox } else { results }
            }
            .navigationTitle("Tin nhắn")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    // một viên như đầu màn Trợ lý (HeaderPill): hai nút cùng khung ⇒ bằng nhau (ký hiệu hệ thống to nhỏ khác nhau khi đứng rời)
                    if desk.talk != nil {
                        HeaderPill {
                            if desk.talk?.can_start_direct == true {
                                HeaderIcon(symbol: "square.and.pencil", help: "Nhắn riêng") { creating = Self.command("talk.start_direct", "Nhắn riêng") }
                            }
                            HeaderIcon(symbol: "plus.bubble", help: "Tạo phòng nhắn tin") { creating = Self.command("talk.create_room", "Tạo phòng nhắn tin") }
                        }
                    }
                }
            }
            .searchable(text: $q, isPresented: $searchOn, placement: .navigationBarDrawer(displayMode: .always), prompt: "Tìm tin nhắn, tệp, khách…")
            .navigationDestination(for: PhoneRoute.self) { r in
                switch r {
                case .thread(let item, let around): PhoneThread(item: item, around: around)
                case .notices: PhoneNotices()
                }
            }
        }
        .onAppear { sync() }
        .onChange(of: desk.messagesPane) { sync() }
        .onChange(of: path) { if path.isEmpty && desk.messagesPane != .list { desk.closeTalk() } }
        .sheet(item: $creating) { a in
            // tạo phòng / mở tin riêng xong ⇒ mở luôn cuộc đó (server trả loại + mã đối tượng)
            FormSheet(api: desk.api, action: a, row: nil, onOutput: { out in desk.openTalk(from: out) }) { msg in desk.show(msg) }
                .presentationDetents([.medium, .large])
        }
        .task {
            // tin mới tới qua kênh hộp tin (Desk.startTalk); đọc lại thưa định kỳ chỉ để đỡ khi kênh rớt
            while !Task.isCancelled {
                await desk.refreshTalk()
                try? await Task.sleep(for: .seconds(120))
            }
        }
        .task(id: q) { await search() }
    }

    private static func command(_ command: String, _ title: String) -> ActionSpec {
        ActionSpec(command: command, title: title, style: "primary", bind: [:], preset: [:], when: [:], importable: false)
    }

    /// ``Desk.messagesPane`` (ai cũng đặt được: bấm thông báo, đẩy, form "Nhắn riêng") ⇒ đường đi của tab.
    private func sync() {
        switch desk.messagesPane {
        case .list:
            if !path.isEmpty { path = [] }
        case .notices:
            if path.last != .notices { path = [.notices] }
        case .thread(let item):
            if case .thread(let cur, _)? = path.last, cur.subject_id == item.subject_id { return }
            path = [.thread(item, around: nil)]
        }
    }

    // MARK: danh sách

    private var inbox: some View {
        List {
            Section {
                row(title: "Thông báo", tag: ("Hệ thống", .orange), preview: desk.notices.first?.title ?? "Chưa có thông báo",
                    stamp: desk.notices.first.map { Fmt.stamp($0.created_at) } ?? "", unread: desk.unread) {
                    IconTile(symbol: "bell.fill", tint: .orange, size: 44)
                } action: { desk.messagesPane = .notices }
                if let inbox = desk.manifest?.inbox, desk.inboxCount > 0 {
                    row(title: "Phiếu chờ duyệt", tag: ("Hệ thống", .orange), preview: "\(desk.inboxCount) phiếu chờ anh/chị duyệt",
                        stamp: "", unread: desk.inboxCount) {
                        IconTile(symbol: "checkmark.seal.fill", tint: .green, size: 44)
                    } action: { withAnimation(.snappy) { desk.open(.screen(inbox), show: true) } }
                }
            }
            if let talk = desk.talk {
                let directs = talk.items.filter { $0.section == "direct" }
                let rooms = talk.items.filter { $0.section == "room" }
                let records = talk.items.filter { $0.section != "direct" && $0.section != "room" }
                if !directs.isEmpty {
                    Section {
                        ForEach(directs) { item in
                            thread(item) { Initials(name: item.title, size: 44) }
                        }
                    } header: { header("Tin riêng") }
                }
                Section {
                    if rooms.isEmpty { hint("Chưa ở phòng nào. Bấm ＋ ở góc trên để lập phòng đội / dự án.") }
                    ForEach(rooms) { item in
                        thread(item) { RoomAvatar(name: item.title, size: 44) }
                    }
                } header: { header("Phòng") }
                Section {
                    if records.isEmpty {
                        hint("Chưa có trao đổi nào. Mở một khách / hồ sơ và nhắn trong khối “Trao đổi” — @nhắc tên để đồng nghiệp nhận được.")
                    }
                    ForEach(records) { item in
                        let style = ModuleStyle.of(item.subject_kind)
                        thread(item, tag: (item.kind_label, .secondary)) { IconTile(symbol: style.symbol, tint: style.tint, size: 44) }
                    }
                } header: { header("Trao đổi trên hồ sơ") }
            } else {
                hint("Gói Trao đổi chưa bật cho công ty này, hoặc anh/chị chưa được dùng.")
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(WA.wall)
        .refreshable {
            await desk.refreshTalk()
            await desk.refreshNotices(announce: false)
        }
    }

    private func thread<Icon: View>(_ item: TalkInbox.Item, tag: (String, Color)? = nil, @ViewBuilder icon: () -> Icon) -> some View {
        row(title: item.title, tag: tag, preview: item.preview, stamp: Fmt.stamp(item.at), unread: item.unread, muted: item.muted == true,
            mentioned: item.mentioned > 0, icon: icon) { desk.openTalk(item) }
            .swipeActions(edge: .trailing) {
                if !item.thread_id.isEmpty, item.inactive != true {
                    Button { Task { await mute(item) } } label: {
                        Label(item.muted == true ? "Bật thông báo" : "Tắt thông báo", systemImage: item.muted == true ? "bell" : "bell.slash")
                    }
                    .tint(item.muted == true ? .accent : .gray)
                }
            }
    }

    private func row<Icon: View>(title: String, tag: (String, Color)? = nil, preview: String, stamp: String, unread: Int = 0, muted: Bool = false,
                                 mentioned: Bool = false, @ViewBuilder icon: () -> Icon, action: @escaping () -> Void) -> some View {
        InboxRow(title: title, tag: tag, preview: preview, stamp: stamp, unread: unread, muted: muted, mentioned: mentioned, icon: icon, action: action)
            .listRowInsets(EdgeInsets(top: 0, leading: 6, bottom: 0, trailing: 6))
            .listRowBackground(WA.wall)
            .listRowSeparator(.hidden)
    }

    private func header(_ text: String) -> some View {
        Text(text).font(.t12m).foregroundStyle(.secondary).textCase(nil)
    }

    private func hint(_ text: String) -> some View {
        Text(text).font(.t12).foregroundStyle(.secondary)
            .listRowBackground(WA.wall).listRowSeparator(.hidden)
    }

    private func mute(_ item: TalkInbox.Item) async {
        do {
            let r = try await desk.api.command("talk.mute", ["thread_id": .string(item.thread_id), "muted": .bool(!(item.muted ?? false))],
                                               key: UUID().uuidString)
            if r.status != "ok" { desk.show(r.error.isEmpty ? r.status : r.error, isError: true) }
        } catch {
            desk.show(error.localizedDescription, isError: true)
        }
        await desk.refreshTalk()
    }

    // MARK: tìm — cùng nguồn với ⌘K trên Mac (``core.find``: tin nhắn, tệp trong trao đổi, khách, căn, giao dịch… trong phần người tìm được xem)

    private var results: some View {
        let term = q.trimmingCharacters(in: .whitespaces)
        let local = (desk.talk?.items ?? []).filter { $0.title.localizedStandardContains(term) }.prefix(5)
        return List {
            if !local.isEmpty {
                Section {
                    ForEach(Array(local)) { item in
                        Button { openFound(item, around: nil) } label: {
                            resultRow(title: item.title, subtitle: item.preview, symbol: "bubble.left.and.bubble.right.fill", tint: .cyan)
                        }
                        .buttonStyle(.plain)
                    }
                } header: { header("Cuộc trò chuyện") }
            }
            let groups = found.reduce(into: [String]()) { if !$0.contains($1.group) { $0.append($1.group) } }
            ForEach(groups, id: \.self) { g in
                Section {
                    ForEach(found.filter { $0.group == g }) { h in
                        Button { pick(h) } label: {
                            let style = ModuleStyle.of(h.open.query)
                            resultRow(title: h.title, subtitle: h.subtitle, symbol: style.symbol, tint: style.tint)
                        }
                        .buttonStyle(.plain)
                    }
                } header: { header(g) }
            }
            if local.isEmpty && found.isEmpty {
                Text(searching ? "Đang tìm…" : term.count < 2 ? "Gõ ít nhất 2 ký tự để tìm tin nhắn, tệp, khách, căn, giao dịch."
                                                            : "Không thấy “\(term)” trong phần anh/chị được xem.")
                    .font(.t14).foregroundStyle(.secondary).listRowSeparator(.hidden)
            }
        }
        .listStyle(.plain)
    }

    private func resultRow(title: String, subtitle: String, symbol: String, tint: Color) -> some View {
        HStack(spacing: 12) {
            IconTile(symbol: symbol, tint: tint, size: 32)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.t14m).lineLimit(1)
                if !subtitle.isEmpty { Text(subtitle).font(.t12).foregroundStyle(.secondary).lineLimit(2) }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 2)
        .contentShape(Rectangle())
    }

    /// Gõ dừng 250 ms mới hỏi server; gõ tiếp thì lượt cũ huỷ (task theo q).
    private func search() async {
        let term = q.trimmingCharacters(in: .whitespaces)
        guard term.count >= 2 else { found = []; return }
        try? await Task.sleep(for: .milliseconds(250))
        guard !Task.isCancelled else { return }
        searching = true
        defer { searching = false }
        guard case .card(let c)? = try? await desk.api.query("core.find", ["q": .string(term)]), !Task.isCancelled else { found = []; return }
        var out: [Hit] = []
        for case .list(let title, let items) in c.blocks {
            for (i, it) in items.enumerated() {
                guard let o = it.open else { continue }
                out.append(Hit(id: "\(title):\(i):\(it.title)", group: title, title: it.title,
                               subtitle: [it.subtitle, it.badge].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "), open: o))
            }
        }
        found = out
    }

    /// Kết quả là một tin / tệp trong trao đổi ⇒ mở cuộc đó ngay quanh tin; còn lại ⇒ thẻ trên khung làm việc.
    private func pick(_ h: Hit) {
        let o = h.open
        if o.query == "talk.thread", case .string(let kind)? = o.input["subject_kind"], case .string(let id)? = o.input["subject_id"] {
            let around: Int? = if case .number(let n)? = o.input["around"] { Int(n) } else { nil }
            openFound(desk.talk?.items.first { $0.subject_id == id } ?? .stub(kind: kind, id: id), around: around)
            return
        }
        searchOn = false
        Task { await desk.openTarget(query: o.query, input: o.input, title: h.title) }
    }

    private func openFound(_ item: TalkInbox.Item, around: Int?) {
        searchOn = false
        q = ""
        path = [.thread(item, around: around)]
        desk.messagesPane = .thread(item)
    }
}

/// Một cuộc trò chuyện chiếm cả màn: khối trao đổi dùng chung (ThreadView, ``fill``), ô nhắn ghim đáy. Phòng / hồ sơ ⇒ nút góc trên mở thẻ của nó
/// (thành viên, thẻ đã chia sẻ / hồ sơ khách) trên khung làm việc — như ngăn bên của Mac.
private struct PhoneThread: View {
    @Environment(Desk.self) private var desk
    let item: TalkInbox.Item
    let around: Int?
    @State private var block: ThreadBlock?
    @State private var record: CardPage.Open?
    @State private var error: String?

    private var title: String { item.title.isEmpty ? (block?.subject_title ?? "Trao đổi") : item.title }
    private var subtitle: String {
        item.isRoom ? "Phòng · chỉ thành viên phòng đọc được"
            : item.isDirect ? "Tin riêng · chỉ hai người đọc được"
            : "\(item.kind_label.isEmpty ? (block?.kind_label ?? "Hồ sơ") : item.kind_label) · chỉ người xem được hồ sơ mới đọc được"
    }

    var body: some View {
        Group {
            if let block {
                ThreadView(block: block, open: { req in
                    Task { await desk.openTarget(query: req.query, input: req.input, title: req.title) }
                }, fill: true)
            } else if let error {
                ContentUnavailableView("Không mở được trao đổi", systemImage: "bubble.left.and.exclamationmark.bubble.right", description: Text(error))
            } else {
                ProgressView()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(WA.wall)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 1) {
                    Text(title).font(.t14m).lineLimit(1)
                    Text(subtitle).font(.t12).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            if let record, !item.isDirect {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await desk.openTarget(query: record.query, input: record.input, title: record.title ?? title) } } label: {
                        Image(systemName: item.isRoom ? "person.2" : "info.circle")
                    }
                    .accessibilityLabel(item.isRoom ? "Thành viên phòng" : "Hồ sơ")
                }
            }
        }
        .toolbar(.hidden, for: .tabBar)
        .task { await load() }
    }

    private func load() async {
        var input: Input = ["subject_kind": .string(item.subject_kind), "subject_id": .string(item.subject_id)]
        if let around { input["around"] = .number(Double(around)) }
        do {
            guard case .card(let c) = try await desk.api.query("talk.thread", input) else { return }
            for case .thread(let t) in c.blocks { block = t }
            record = c.open.first
        } catch {
            self.error = error.localizedDescription   // vd đã đổi người phụ trách ⇒ không còn thấy hồ sơ
        }
    }
}

/// Thông báo hệ thống (việc mới, lead mới, phiếu duyệt…) — cùng danh sách với Mac, mở trong tab Tin nhắn.
private struct PhoneNotices: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        NoticeList(embedded: true)
            .background(WA.wall)
            .navigationTitle("Thông báo")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if desk.unread > 0 {
                    ToolbarItem(placement: .topBarTrailing) { Button("Đọc hết") { desk.markAllNoticesRead() } }
                }
            }
    }
}
