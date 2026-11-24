#if os(macOS)
import AppKit
import SwiftUI

// Khung kiểu Lark trên Mac (31/10): THANH BÊN TRÁI chọn mục → CỘT GIỮA đổi theo mục → KHUNG LÀM VIỆC bên phải giữ nguyên.
// Khác Lark ở chỗ trợ lý là mục đầu (thứ chính của Barista); "Tin nhắn" gộp trợ lý + thông báo hệ thống + phiếu chờ duyệt + trao đổi trên
// hồ sơ thành MỘT danh sách như danh sách chat của Lark. Phân hệ lấy từ manifest — server quyết ai thấy màn nào, app chỉ vẽ.

/// Nền thanh bên trái = vật liệu "sidebar" của macOS (mờ xuyên hình nền như Finder / Mail / Notes), tự theo sáng / tối và cửa sổ đang chọn.
private struct SidebarMaterial: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let v = NSVisualEffectView()
        v.material = .sidebar
        v.blendingMode = .behindWindow
        v.state = .followsWindowActiveState
        return v
    }
    func updateNSView(_ v: NSVisualEffectView, context: Context) {}
}

/// Biểu tượng SF Symbols của thanh: rỗng khi chưa chọn, đặc khi chọn; nét cùng độ đậm với chữ nhãn bên dưới (HIG).
private struct RailSymbol: View {
    let name: String
    let selected: Bool

    var body: some View {
        Image(systemName: SymbolMap.name(name).replacingOccurrences(of: ".fill", with: ""))
            .symbolVariant(selected ? .fill : .none)
            .font(.system(size: 17, weight: selected ? .semibold : .regular))
    }
}

extension Desk {
    /// Mục trên thanh theo thứ tự hiện (⌘1 … ⌘9).
    /// Có thanh bên theo vai (ADR 0255) ⇒ chỉ phân hệ "Theo vai" + "Đã ghim" có phím tắt; phần còn lại mở từ "Tất cả mục" / ⌘K.
    var railSections: [RailSection] { [.assistant, .desk, .messages] + rail.onRail.map { .workspace($0.key) } }

    func select(_ s: RailSection) {
        withAnimation(.snappy(duration: 0.2)) {
            if s == .messages && section == .messages { messagesPane = .list }   // bấm lại ⇒ về danh sách
            section = s
            narrowShowsCanvas = false
        }
    }
}

/// Bố cục cửa sổ chính: thanh bên trái | cột theo mục chọn | khung làm việc. Hẹp ⇒ thanh + một ngăn (cột hoặc khung làm việc).
struct DeskShell: View {
    @Environment(Desk.self) private var desk
    @AppStorage("railHidden") private var hiddenPref = false
    var alwaysRail = false   // chụp nghiệm thu: luôn hiện thanh, không đụng lựa chọn ẩn / hiện của người dùng
    private var railHidden: Bool { hiddenPref && !alwaysRail }

    var body: some View {
        GeometryReader { g in
            let narrow = g.size.width < 980
            let _ = updateNarrow(narrow)
            HStack(spacing: 0) {
                if !railHidden {
                    Rail().transition(.move(edge: .leading))
                    Rectangle().fill(Color.hairline).frame(width: 1)
                }
                if !narrow {
                    SectionColumn().frame(width: min(440, max(330, (g.size.width - (railHidden ? 0 : 81)) * 0.36)))
                    Rectangle().fill(Color.hairline).frame(width: 1)
                    if showsConversation {
                        MessagesDetail().frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        CanvasView().frame(maxWidth: .infinity, maxHeight: .infinity)
                    }
                } else if showsConversation {
                    MessagesDetail(back: { withAnimation(.snappy(duration: 0.2)) { desk.closeTalk() } })
                } else if desk.narrowShowsCanvas {
                    CanvasView(onBack: { withAnimation(.snappy) { desk.narrowShowsCanvas = false } })
                } else {
                    SectionColumn()
                }
            }
        }
        .overlay(alignment: .topLeading) {
            // ẩn / hiện thanh bên trái — cạnh ba nút đèn cửa sổ như Notes / Finder (⌃⌘S)
            Button { withAnimation(.snappy(duration: 0.25)) { hiddenPref = !railHidden } } label: {
                Image(systemName: "sidebar.left").font(.system(size: 14, weight: .medium)).foregroundStyle(.secondary)
                    .frame(width: 28, height: 22).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .keyboardShortcut("s", modifiers: [.control, .command])
            .help(railHidden ? "Hiện thanh bên trái (⌃⌘S)" : "Ẩn thanh bên trái (⌃⌘S)")
            .padding(.leading, 80).padding(.top, 5)
            // ⌘K mở ô tìm, ⌘1…⌘9 chuyển mục — chạy cả khi thanh bên trái đang ẩn
            Button("") { withAnimation(.snappy(duration: 0.2)) { desk.showSearch = true } }
                .keyboardShortcut("k", modifiers: .command)
                .opacity(0).frame(width: 0, height: 0).allowsHitTesting(false)
            ForEach(Array(desk.railSections.prefix(9).enumerated()), id: \.offset) { i, s in
                Button("") { desk.select(s) }
                    .keyboardShortcut(KeyEquivalent(Character("\(i + 1)")), modifiers: .command)
                    .opacity(0).frame(width: 0, height: 0).allowsHitTesting(false)
            }
        }
        .overlay {
            if desk.showSearch {
                ZStack(alignment: .top) {
                    Color.black.opacity(0.22).ignoresSafeArea().onTapGesture { desk.showSearch = false }
                    SearchPalette().padding(.top, 84)
                }
                .transition(.opacity)
            }
        }
    }

    /// Như app chat thường: cột trái là danh sách, bên phải là cuộc trò chuyện đang chọn (đầy đủ). Không chọn gì ⇒ khung làm việc.
    private var showsConversation: Bool { desk.section == .messages && desk.messagesPane != .list }

    private func updateNarrow(_ narrow: Bool) {
        if desk.isNarrow != narrow { DispatchQueue.main.async { desk.isNarrow = narrow } }
    }
}

// MARK: thanh bên trái

struct Rail: View {
    @Environment(Desk.self) private var desk

    private var messagesBadge: Int { desk.unread + (desk.talk?.unread ?? 0) }

    var body: some View {
        VStack(spacing: 6) {
            RailButton(title: "Tìm", selected: false, help: "Tìm khách, căn, giao dịch, màn (⌘K)") {
                RailSymbol(name: "magnifyingglass", selected: false)
            } action: { withAnimation(.snappy(duration: 0.2)) { desk.showSearch = true } }
            Rectangle().fill(Color.primary.opacity(0.1)).frame(width: 36, height: 1).padding(.vertical, 4)
            RailButton(title: "Trợ lý", selected: desk.railSelected == .assistant, help: "Trợ lý của \(desk.firstName) (⌘1)") {
                Orb(mood: desk.live != nil ? .thinking : .idle, size: 26)
            } action: { desk.select(.assistant) }
            RailButton(title: "Việc", badge: desk.deskPage?.count ?? 0,
                       unknown: (desk.deskPage?.count ?? 0) == 0 && ((desk.deskPage?.unchecked ?? 0) > 0 || desk.deskFailed), selected: desk.railSelected == .desk, help: "Mọi việc của bạn ở một chỗ: cần quyết, cần làm, đang theo dõi (⌘2)") {
                RailSymbol(name: "checklist", selected: desk.railSelected == .desk)
            } action: { desk.select(.desk) }
            RailButton(title: "Tin nhắn", badge: messagesBadge, selected: desk.railSelected == .messages,
                       help: "Phòng, trao đổi trên hồ sơ, thông báo, phiếu duyệt (⌘3)") {
                RailSymbol(name: "bubble.left.and.bubble.right", selected: desk.railSelected == .messages)
            } action: { desk.select(.messages) }
            Rectangle().fill(Color.primary.opacity(0.1)).frame(width: 36, height: 1).padding(.vertical, 4)
            let layout = desk.rail
            let shortcut = layout.onRail.map(\.key)
            ScrollViewReader { proxy in ScrollView(.vertical, showsIndicators: false) {
                VStack(spacing: 6) {
                    if layout.grouped {
                        // ADR 0255: không vẽ hết — "Theo vai" (gói / công ty khai) + "Đã ghim" (người xem ghim) + phân hệ đang mở nếu nó không ở nhóm nào
                        RailGroupLabel(text: "Theo vai")
                        ForEach(layout.role) { w in workspaceButton(w, shortcut: shortcut) }
                        if !layout.pinned.isEmpty {
                            RailGroupLabel(text: "Đã ghim")
                            ForEach(layout.pinned) { w in workspaceButton(w, shortcut: shortcut) }
                        }
                        if case .workspace(let key) = desk.railSelected, let here = layout.current(key) {
                            RailGroupLabel(text: "Đang mở")
                            workspaceButton(here, shortcut: shortcut)
                        }
                    } else {
                        ForEach(layout.all) { i in workspaceButton(i.ws, shortcut: shortcut) }
                    }
                }
            }
            // mục đang chọn luôn trong tầm mắt (nhiều phân hệ / cửa sổ thấp ⇒ thanh cuộn) — như web
            .onChange(of: desk.railSelected, initial: true) { _, s in
                if case .workspace(let key) = s { withAnimation(.snappy(duration: 0.2)) { proxy.scrollTo(key) } }
            } }
            Spacer(minLength: 8)
            if layout.grouped {
                Rectangle().fill(Color.primary.opacity(0.1)).frame(width: 36, height: 1).padding(.vertical, 2)
                AllItemsButton(layout: layout)   // ngoài vùng cuộn: luôn thấy, ngay trên người dùng
            }
            PersonaMenu()
        }
        .padding(.top, titlebarInset + 6)
        .padding(.bottom, 14)
        .frame(width: 80)
        .frame(maxHeight: .infinity)
        .background(SidebarMaterial())
    }

    private func workspaceButton(_ w: Workspace, shortcut: [String]) -> some View {
        let on = desk.railSelected == .workspace(w.key)
        let i = shortcut.firstIndex(of: w.key)
        return RailButton(title: w.title, selected: on, help: "\(w.title) — \(w.screens.count) màn" + (i.map { $0 < 6 ? " (⌘\($0 + 4))" : "" } ?? "")) {
            RailSymbol(name: w.icon, selected: on)
        } action: { desk.select(.workspace(w.key)) }
        .id(w.key)
    }
}

/// Tên nhóm trên thanh bên (vạch ngăn + chữ nhỏ) — "Theo vai", "Đã ghim", "Đang mở".
private struct RailGroupLabel: View {
    let text: String

    var body: some View {
        VStack(spacing: 4) {
            Rectangle().fill(Color.primary.opacity(0.1)).frame(width: 36, height: 1)
            Text(text).font(.t12).foregroundStyle(.secondary).lineLimit(1)
        }
        .padding(.top, 2)
    }
}

/// Nút "Tất cả mục": khung nổi liệt kê MỌI phân hệ người này thấy — bấm tên ⇒ mở, bấm ghim ⇒ ghim / bỏ ghim cả phân hệ lên thanh bên của chính mình.
/// Ô tìm là ⌘K sẵn có. Cùng nội dung với panel của web (`web/src/components/desk/rail.tsx`).
private struct AllItemsButton: View {
    @Environment(Desk.self) private var desk
    let layout: RailLayout
    @State private var open = false

    var body: some View {
        RailButton(title: "Tất cả mục", selected: open, help: "Mọi phân hệ bạn được dùng — ghim cái hay dùng lên thanh bên") {
            RailSymbol(name: "square.grid.2x2", selected: open)
        } action: { open.toggle() }
        .popover(isPresented: $open, arrowEdge: .trailing) {
            AllItemsList(layout: layout, close: { open = false })
        }
    }
}

private struct AllItemsList: View {
    @Environment(Desk.self) private var desk
    let layout: RailLayout
    let close: () -> Void
    @State private var busy: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button { close(); withAnimation(.snappy(duration: 0.2)) { desk.showSearch = true } } label: {
                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass")
                    Text("Tìm màn, khách, giao dịch…").frame(maxWidth: .infinity, alignment: .leading)
                    Text("⌘K").font(.t12)
                }
                .font(.t14).foregroundStyle(.secondary)
                .padding(.horizontal, 10).frame(height: 32)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Color.primary.opacity(0.12)))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Text("Tất cả mục · \(layout.all.count)").font(.t12m).foregroundStyle(.secondary).padding(.horizontal, 8).padding(.top, 8)
            ScrollView {
                VStack(spacing: 2) {
                    ForEach(layout.all) { i in row(i) }
                }
            }
            .frame(maxHeight: 460)
        }
        .padding(8)
        .frame(width: 320)
        .fixedSize(horizontal: false, vertical: true)
    }

    private func row(_ i: RailLayout.Item) -> some View {
        HStack(spacing: 2) {
            Button { close(); desk.select(.workspace(i.ws.key)) } label: {
                HStack(spacing: 10) {
                    IconTile(symbol: i.ws.icon, tint: .secondary, size: 28)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(i.ws.title).font(.t14).lineLimit(1)
                        Text(RailLayout.subtitle(i)).font(.t12).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer(minLength: 4)
                }
                .padding(.horizontal, 8).padding(.vertical, 5)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Button {
                busy = i.ws.key
                Task { await desk.togglePin(i); busy = nil }
            } label: {
                Image(systemName: i.pinned ? "pin.fill" : "pin")
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(i.pinned ? Color.accent : Color.secondary)
                    .frame(width: 30, height: 30).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(busy == i.ws.key)
            .help(i.pinned ? "Bỏ ghim khỏi thanh bên" : "Ghim lên thanh bên")
            .accessibilityLabel("\(i.pinned ? "Bỏ ghim" : "Ghim") \(i.ws.title)")
        }
        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(desk.railSelected == .workspace(i.ws.key) ? Color.accent.opacity(0.12) : .clear))
    }
}

private struct RailButton<Icon: View>: View {
    let title: String
    var badge = 0
    var unknown = false   // có mục chưa kiểm được mà không có số ⇒ dấu xám (không để trống như hết việc)
    let selected: Bool
    let help: String
    @ViewBuilder let icon: Icon
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            VStack(spacing: 4) {
                icon
                    .foregroundStyle(selected ? Color.accent : Color.secondary)
                    .frame(width: 44, height: 34)
                    .background(RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(selected ? Color.accent.opacity(0.16) : (hover ? Color.primary.opacity(0.07) : .clear)))
                    .overlay(alignment: .topTrailing) {
                        if unknown && badge == 0 {
                            Circle().fill(Color.secondary).frame(width: 9, height: 9).offset(x: 3, y: -2).help("Có mục chưa kiểm được")
                        }
                        if badge > 0 {
                            Text(badge > 99 ? "99+" : "\(badge)")
                                .font(.system(size: 10, weight: .bold)).monospacedDigit().foregroundStyle(.white)
                                .padding(.horizontal, 4).frame(minWidth: 16, minHeight: 16)
                                .background(Capsule().fill(Color.bad))
                                .offset(x: 6, y: -4)
                        }
                    }
                Text(title)
                    .font(selected ? .t12m : .t12)
                    .foregroundStyle(selected ? Color.accent : Color.secondary)
                    .lineLimit(2).multilineTextAlignment(.center)
                    .frame(width: 76)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help(help)
    }
}

// MARK: cột giữa

struct SectionColumn: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        switch desk.section {
        case .assistant: ChatColumn()
        case .desk: DeskColumn()
        case .messages: MessagesColumn()
        case .workspace(let key): WorkspaceColumn(key: key)
        }
    }
}

/// Đầu cột cùng chiều cao với đầu cột trợ lý và đầu khung làm việc ⇒ ba đường kẻ ngang thẳng hàng.
private struct ColumnHeader<Trailing: View>: View {
    let title: String
    var subtitle = ""
    var back: (() -> Void)? = nil
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: 10) {
            if let back {
                Button(action: back) { Image(systemName: "chevron.left").font(.system(size: 14, weight: .semibold)) }
                    .buttonStyle(ToolChipStyle()).help("Về danh sách")
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.t16).lineLimit(1)
                if !subtitle.isEmpty { Text(subtitle).font(.t12).foregroundStyle(.secondary).lineLimit(1) }
            }
            Spacer(minLength: 8)
            trailing
        }
        .frame(height: 40)
        .padding(.horizontal, 14)
        .padding(.top, titlebarInset)
        .padding(.bottom, 10)
        // đầu ngăn NỔI trên nền của chính ngăn (cùng PANE_HEADER của web): không dải nền, không đường kẻ dưới — nút viên nổi như ChatGPT
    }
}

extension ColumnHeader where Trailing == EmptyView {
    init(title: String, subtitle: String = "", back: (() -> Void)? = nil) {
        self.init(title: title, subtitle: subtitle, back: back) { EmptyView() }
    }
}

// MARK: Tin nhắn

struct MessagesColumn: View {
    @Environment(Desk.self) private var desk
    @State private var creating: ActionSpec?

    private var selectedThread: String? { if case .thread(let i) = desk.messagesPane { return i.thread_id }; return nil }

    var body: some View {
        list
        .task {
            // tin mới tới qua kênh hộp tin (Desk.startTalk); đọc lại thưa định kỳ chỉ để đỡ khi kênh rớt
            while !Task.isCancelled {
                await desk.refreshTalk()
                try? await Task.sleep(for: .seconds(120))
            }
        }
    }

    private var list: some View {
        VStack(spacing: 0) {
            ColumnHeader(title: "Tin nhắn", subtitle: summary) {
                if desk.talk != nil {
                    HeaderPill {
                        if desk.talk?.can_start_direct == true {
                            HeaderIcon(symbol: "paperplane", help: "Nhắn riêng — chỉ hai người đọc được") {
                                creating = ActionSpec(command: "talk.start_direct", title: "Nhắn riêng", style: "primary", bind: [:], preset: [:],
                                                      when: [:], importable: false)
                            }
                        }
                        HeaderIcon(symbol: "plus.bubble", help: "Tạo phòng nhắn tin nhóm") {
                            creating = ActionSpec(command: "talk.create_room", title: "Tạo phòng nhắn tin", style: "primary", bind: [:], preset: [:],
                                                  when: [:], importable: false)
                        }
                    }
                }
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2) {
                    InboxRow(title: "Trợ lý của \(desk.firstName)", tag: ("Trợ lý", .accent), preview: assistantPreview,
                             stamp: desk.threads.first.map { Fmt.stamp($0.updated_at) } ?? "") {
                        Orb(mood: desk.live != nil ? .thinking : .idle, size: 40)
                    } action: { withAnimation(.snappy(duration: 0.2)) { if desk.builder != nil { desk.exitBuilder() } else { desk.section = .assistant } } }
                    InboxRow(title: "Thông báo", tag: ("Hệ thống", .orange), preview: desk.notices.first?.title ?? "Chưa có thông báo",
                             stamp: desk.notices.first.map { Fmt.stamp($0.created_at) } ?? "", unread: desk.unread,
                             selected: desk.messagesPane == .notices) {
                        IconTile(symbol: "bell.fill", tint: .orange, size: 40)
                    } action: { withAnimation(.snappy(duration: 0.2)) { desk.messagesPane = .notices } }
                    if let inbox = desk.manifest?.inbox, desk.inboxCount > 0 {
                        InboxRow(title: "Phiếu chờ duyệt", tag: ("Hệ thống", .orange), preview: "\(desk.inboxCount) phiếu chờ anh/chị duyệt",
                                 stamp: "", unread: desk.inboxCount) {
                            IconTile(symbol: "checkmark.seal.fill", tint: .green, size: 40)
                        } action: { withAnimation(.snappy) { desk.messagesPane = .list; desk.open(.screen(inbox), show: true) } }
                    }
                    if let talk = desk.talk {
                        let directs = talk.items.filter { $0.section == "direct" }
                        let rooms = talk.items.filter { $0.section == "room" }
                        let records = talk.items.filter { $0.section != "direct" && $0.section != "room" }
                        if !directs.isEmpty { sectionTitle("Tin riêng") }
                        ForEach(directs) { item in
                            InboxRow(title: item.title, preview: item.preview, stamp: Fmt.stamp(item.at), unread: item.unread,
                                     muted: item.muted == true, selected: selectedThread == item.thread_id) {
                                Initials(name: item.title, size: 40)
                            } action: { withAnimation(.snappy(duration: 0.2)) { desk.openTalk(item) } }
                        }
                        sectionTitle("Phòng")
                        if rooms.isEmpty {
                            hint("Chưa ở phòng nào. Bấm ＋ ở đầu cột để lập phòng đội / dự án.")
                        }
                        ForEach(rooms) { item in
                            InboxRow(title: item.title, preview: item.preview, stamp: Fmt.stamp(item.at),
                                     unread: item.unread, muted: item.muted == true, mentioned: item.mentioned > 0, selected: selectedThread == item.thread_id) {
                                RoomAvatar(name: item.title, size: 40)
                            } action: { withAnimation(.snappy(duration: 0.2)) { desk.openTalk(item) } }
                        }
                        sectionTitle("Trao đổi trên hồ sơ")
                        if records.isEmpty {
                            hint("Chưa có trao đổi nào. Mở một khách / hồ sơ và nhắn trong khối “Trao đổi” — @nhắc tên để đồng nghiệp nhận được.")
                        }
                        ForEach(records) { item in
                            let style = ModuleStyle.of(item.subject_kind)
                            InboxRow(title: item.title, tag: (item.kind_label, .secondary), preview: item.preview, stamp: Fmt.stamp(item.at),
                                     unread: item.unread, muted: item.muted == true, mentioned: item.mentioned > 0, selected: selectedThread == item.thread_id) {
                                IconTile(symbol: style.symbol, tint: style.tint, size: 40)
                            } action: { withAnimation(.snappy(duration: 0.2)) { desk.openTalk(item) } }
                        }
                    }
                }
                .padding(.horizontal, 6).padding(.vertical, 8)
            }
        }
        .background(WA.wall)
        .sheet(item: $creating) { a in
            // tạo phòng / mở tin riêng xong ⇒ mở luôn cuộc đó (server trả loại + mã đối tượng)
            FormSheet(api: desk.api, action: a, row: nil, onOutput: { out in desk.openTalk(from: out) }) { msg in desk.show(msg) }
        }
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text).font(.t12m).foregroundStyle(.secondary).padding(.horizontal, 12).padding(.top, 14).padding(.bottom, 4)
    }

    private func hint(_ text: String) -> some View {
        Text(text).font(.t12).foregroundStyle(.secondary).padding(.horizontal, 12).padding(.bottom, 4)
    }

    private var summary: String {
        let n = desk.unread + (desk.talk?.unread ?? 0)
        return n > 0 ? "\(n) mục chưa đọc" : "Đã đọc hết"
    }

    private var assistantPreview: String {
        if desk.live != nil { return "đang trả lời…" }
        if let last = desk.messages.last(where: { $0.role == "assistant" }), !last.text.isEmpty {
            return last.text.replacingOccurrences(of: "\n", with: " ")
        }
        return desk.threads.first?.title ?? "Hỏi về khách, giao dịch, số liệu — em soạn sẵn để anh/chị bấm"
    }
}

/// Bên phải của "Tin nhắn": thông báo hoặc cuộc trò chuyện đang chọn, đầy đủ như app chat. ``back`` chỉ có khi cửa sổ hẹp.
struct MessagesDetail: View {
    @Environment(Desk.self) private var desk
    var back: (() -> Void)? = nil

    var body: some View {
        switch desk.messagesPane {
        case .list: EmptyView()
        case .notices:
            VStack(spacing: 0) {
                ColumnHeader(title: "Thông báo", subtitle: "Hệ thống Barista · việc mới, lead mới, phiếu duyệt", back: back) {
                    if desk.unread > 0 { Button("Đọc hết") { desk.markAllNoticesRead() }.buttonStyle(.borderless).font(.t12m) }
                }
                NoticeList(embedded: true)
            }
            .background(WA.wall)
        case .thread(let item): ChatPane(item: item, back: back).id(item.id)
        }
    }
}

/// Cuộc trò chuyện chiếm khung bên phải; ngăn phụ cạnh nó (bật / tắt ở đầu) hiện MỘT thẻ: thành viên phòng / hồ sơ khách, hoặc thẻ vừa bấm
/// mở từ tin. Mặc định tắt cho chat rộng; bấm một thẻ được chia sẻ ⇒ ngăn phụ tự mở.
private struct ChatPane: View {
    @Environment(Desk.self) private var desk
    let item: TalkInbox.Item
    var back: (() -> Void)?
    @State private var block: ThreadBlock?
    @State private var error: String?

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                ColumnHeader(title: item.title.isEmpty ? (block?.subject_title ?? "Trao đổi") : item.title,
                             subtitle: item.isRoom ? "Phòng · chỉ thành viên phòng đọc được"
                                 : item.isDirect ? "Tin riêng · chỉ hai người đọc được"
                                 : "\(item.kind_label.isEmpty ? (block?.kind_label ?? "Hồ sơ") : item.kind_label) · chỉ người xem được hồ sơ mới đọc được",
                             back: back) {
                    if !item.isDirect { Button { withAnimation(.snappy(duration: 0.25)) { desk.chatInspector.toggle() } } label: {
                        Label(item.isRoom ? "Thành viên" : "Hồ sơ", systemImage: "sidebar.right")
                    }
                    .buttonStyle(ToolChipStyle(fill: desk.chatInspector ? .accent.opacity(0.18) : nil))
                    .help(desk.chatInspector ? "Ẩn ngăn bên" : (item.isRoom ? "Thành viên, thẻ đã chia sẻ" : "Hồ sơ của trao đổi này"))
                    }
                }
                if let block {
                    ThreadView(block: block, open: { req in
                        desk.chatInspector = true
                        Task { await desk.openTarget(query: req.query, input: req.input, title: req.title, reveal: false) }
                    }, fill: true)
                    .frame(maxWidth: 760)
                    .frame(maxWidth: .infinity)
                } else if let error {
                    ContentUnavailableView("Không mở được trao đổi", systemImage: "bubble.left.and.exclamationmark.bubble.right",
                                           description: Text(error))
                        .frame(maxHeight: .infinity)
                } else {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .background(WA.wall)
            if desk.chatInspector, let active = desk.active {
                Rectangle().fill(Color.hairline).frame(width: 1)
                CardFrame(item: active, onClose: { withAnimation(.snappy(duration: 0.25)) { desk.chatInspector = false } })
                    .padding(14)
                    .frame(width: 460)
                    .background(Color.canvas)
                    .transition(.move(edge: .trailing))
            }
        }
        .task { await load() }
    }

    private func load() async {
        do {
            let page = try await desk.api.query("talk.thread", ["subject_kind": .string(item.subject_kind), "subject_id": .string(item.subject_id)])
            guard case .card(let c) = page else { return }
            for case .thread(let t) in c.blocks { block = t }
            // hồ sơ / thẻ phòng nạp sẵn cho ngăn phụ (không tự mở ngăn, không nhảy khỏi cuộc chat trên cửa sổ hẹp)
            if let o = c.open.first { await desk.openTarget(query: o.query, input: o.input, title: o.title ?? "", reveal: false) }
        } catch {
            self.error = error.localizedDescription   // vd đã đổi người phụ trách ⇒ không còn thấy hồ sơ
        }
    }
}

// MARK: phân hệ

private struct WorkspaceColumn: View {
    @Environment(Desk.self) private var desk
    let key: String

    var body: some View {
        let w = desk.manifest?.workspaces.first { $0.key == key }
        let screens = w?.screens ?? []
        let groups = screens.reduce(into: [String]()) { if !$0.contains($1.group) { $0.append($1.group) } }
        VStack(spacing: 0) {
            ColumnHeader(title: w?.title ?? "", subtitle: "\(screens.count) màn · dữ liệu đúng quyền của bạn")
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2) {
                    ForEach(groups, id: \.self) { g in
                        if !g.isEmpty {
                            Text(g).font(.t12m).foregroundStyle(.secondary).padding(.horizontal, 12).padding(.top, 12).padding(.bottom, 4)
                        }
                        ForEach(screens.filter { $0.group == g }) { s in ScreenRow(spec: s) }
                    }
                }
                .padding(.horizontal, 6).padding(.vertical, 8)
            }
        }
        .background(WA.wall)
    }
}

private struct ScreenRow: View {
    @Environment(Desk.self) private var desk
    let spec: ScreenSpec
    @State private var hover = false

    var body: some View {
        let selected = desk.activeId == CanvasItem.screen(spec).id
        let pinned = desk.quickScreens.contains { $0.query == spec.query && $0.key == spec.key }
        Button { withAnimation(.snappy) { desk.open(.screen(spec), show: true) } } label: {
            HStack(spacing: 10) {
                IconTile(symbol: spec.icon, tint: spec.tintColor, size: 28)
                Text(spec.title).font(selected ? .t14m : .t14).lineLimit(1)
                Spacer(minLength: 4)
                if pinned { Image(systemName: "pin.fill").font(.system(size: 10)).foregroundStyle(.tertiary).help("Màn ghim của vai") }
            }
            .padding(.horizontal, 10).padding(.vertical, 7)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(selected ? Color.accent.opacity(0.12) : (hover ? Color.primary.opacity(0.05) : .clear)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

// MARK: tìm nhanh ⌘K

/// Một ô tìm cho mọi thứ: màn (theo tên), trao đổi (theo hồ sơ), và mọi nguồn tìm các gói đang bật tự khai — khách, căn, giao dịch, tài liệu, kế
/// hoạch, việc, L/C… (``core.find`` — 0087, chỉ trong phần người tìm được xem).
private struct SearchPalette: View {
    @Environment(Desk.self) private var desk
    @State private var q = ""
    @State private var found: [Hit] = []
    @State private var searching = false
    @State private var selected = 0
    @FocusState private var focused: Bool

    struct Hit: Identifiable {
        enum Target { case screen(ScreenSpec), talk(TalkInbox.Item), open(String, Input, String) }
        let id: String
        let group, title, subtitle, symbol: String
        let tint: Color
        let target: Target
    }

    private var local: [Hit] {
        let term = q.trimmingCharacters(in: .whitespaces)
        let spaces = desk.manifest?.workspaces ?? []
        if term.isEmpty {
            return desk.quickScreens.map { s in
                Hit(id: "s:\(s.query):\(s.key)", group: "Màn hay dùng", title: s.title, subtitle: "", symbol: s.icon,
                    tint: s.tintColor, target: .screen(s))
            }
        }
        var hits: [Hit] = spaces.flatMap { w in
            w.screens.filter { $0.title.localizedStandardContains(term) }.map { s in
                Hit(id: "s:\(s.query):\(s.key)", group: "Màn", title: s.title, subtitle: w.title, symbol: s.icon,
                    tint: s.tintColor, target: .screen(s))
            }
        }
        hits += (desk.talk?.items ?? []).filter { $0.title.localizedStandardContains(term) }.prefix(5).map { t in
            Hit(id: "t:\(t.thread_id)", group: "Trao đổi", title: t.title, subtitle: t.preview, symbol: "bubble.left.and.bubble.right.fill",
                tint: .cyan, target: .talk(t))
        }
        return hits
    }

    private var hits: [Hit] { local + found }

    var body: some View {
        @Bindable var desk = desk
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass").font(.system(size: 16, weight: .semibold)).foregroundStyle(.secondary)
                TextField("Tìm khách (tên, SĐT), mã căn, mã giao dịch, tên màn…", text: $q)
                    .textFieldStyle(.plain).font(.system(size: 16))
                    .focused($focused)
                    .onSubmit { pick(selected) }
                    .onKeyPress(.downArrow) { selected = min(selected + 1, max(hits.count - 1, 0)); return .handled }
                    .onKeyPress(.upArrow) { selected = max(selected - 1, 0); return .handled }
                    .onExitCommand { close() }
                if searching { ProgressView().controlSize(.small) }
                Text("esc").font(.t12).foregroundStyle(.tertiary)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(RoundedRectangle(cornerRadius: 5).strokeBorder(Color.primary.opacity(0.15)))
            }
            .padding(.horizontal, 16).padding(.vertical, 14)
            Rectangle().fill(Color.hairline).frame(height: 1)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        let all = hits
                        ForEach(Array(all.enumerated()), id: \.element.id) { i, h in
                            if i == 0 || all[i - 1].group != h.group {
                                Text(h.group).font(.t12m).foregroundStyle(.secondary)
                                    .padding(.horizontal, 10).padding(.top, i == 0 ? 4 : 12).padding(.bottom, 4)
                            }
                            row(h, active: i == selected).id(h.id).onTapGesture { pick(i) }
                        }
                        if all.isEmpty && !searching {
                            Text(q.trimmingCharacters(in: .whitespaces).count < 2 ? "Gõ ít nhất 2 ký tự để tìm khách, căn, giao dịch."
                                                                                    : "Không thấy “\(q)” trong phần anh/chị được xem.")
                                .font(.t14).foregroundStyle(.secondary).padding(14)
                        }
                    }
                    .padding(8)
                }
                .frame(maxHeight: 420)
                .onChange(of: selected) { if hits.indices.contains(selected) { proxy.scrollTo(hits[selected].id) } }
            }
            Rectangle().fill(Color.hairline).frame(height: 1)
            Text("↑↓ chọn · ↩ mở · chỉ tìm trong phần dữ liệu anh/chị được xem").font(.t12).foregroundStyle(.tertiary)
                .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 16).padding(.vertical, 8)
        }
        .frame(width: 620)
        .fixedSize(horizontal: false, vertical: true)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Color.surface))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Color.primary.opacity(0.12)))
        .shadow(color: .black.opacity(0.25), radius: 30, y: 12)
        .onAppear {
            focused = true
            if q.isEmpty, let s = ProcessInfo.processInfo.environment["BARISTA_SEARCH"] { q = s }   // chụp nghiệm thu
        }
        .onChange(of: q) { selected = 0 }
        .task(id: q) { await search() }
    }

    private func row(_ h: Hit, active: Bool) -> some View {
        HStack(spacing: 10) {
            IconTile(symbol: h.symbol, tint: h.tint, size: 28)
            VStack(alignment: .leading, spacing: 1) {
                Text(h.title).font(.t14).lineLimit(1)
                if !h.subtitle.isEmpty { Text(h.subtitle).font(.t12).foregroundStyle(.secondary).lineLimit(1) }
            }
            Spacer(minLength: 4)
            if active { Image(systemName: "return").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary) }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(active ? Color.accent.opacity(0.14) : .clear))
        .contentShape(Rectangle())
    }

    /// Gõ dừng 200 ms mới hỏi server; gõ tiếp thì lượt cũ huỷ (task theo q).
    private func search() async {
        let term = q.trimmingCharacters(in: .whitespaces)
        guard term.count >= 2 else { found = []; return }
        try? await Task.sleep(for: .milliseconds(200))
        guard !Task.isCancelled else { return }
        searching = true
        defer { searching = false }
        guard case .card(let c)? = try? await desk.api.query("core.find", ["q": .string(term)]), !Task.isCancelled else { found = []; return }
        var out: [Hit] = []
        for case .list(let title, let items) in c.blocks {
            for (i, it) in items.enumerated() {
                guard let o = it.open else { continue }
                let style = ModuleStyle.of(o.query)
                out.append(Hit(id: "f:\(title):\(i):\(it.title)", group: title, title: it.title,
                               subtitle: [it.subtitle, it.badge].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "),
                               symbol: style.symbol, tint: style.tint, target: .open(o.query, o.input, it.title)))
            }
        }
        found = out
    }

    private func pick(_ i: Int) {
        let all = hits
        guard all.indices.contains(i) else { return }
        close()
        switch all[i].target {
        case .screen(let s): withAnimation(.snappy) { desk.open(.screen(s), show: true) }
        case .talk(let t): withAnimation(.snappy(duration: 0.2)) { desk.openTalk(t) }
        case .open(let query, let input, let title): Task { await desk.openTarget(query: query, input: input, title: title) }
        }
    }

    private func close() {
        withAnimation(.snappy(duration: 0.2)) { desk.showSearch = false }
    }
}

// MARK: nghiệm thu ngoài màn hình

/// `--snapshot-shell <thư mục>`: dựng cửa sổ chính (thanh bên trái + cột + khung làm việc) NGOÀI màn hình bằng dữ liệu thật rồi xuất PNG —
/// không chen vào màn hình người dùng. BARISTA_EMAIL (vai), BARISTA_SECTION (assistant | desk | messages | notices | thread | search | ws:<khoá>),
/// BARISTA_SEARCH (chữ gõ sẵn ở ô tìm), BARISTA_OPEN ('truy_vấn {json}'), BARISTA_DARK=1.
@MainActor
enum ShellSnapshot {
    static func run(into dir: URL) async {
        let env = ProcessInfo.processInfo.environment
        let email = env["BARISTA_EMAIL"] ?? "truonga@sanmau.demo"
        var tenant = env["BARISTA_TENANT"] ?? ""
        if tenant.isEmpty, let logins = try? await API(base: API.defaultBase, email: "", tenant: "").logins() {
            tenant = logins.first { $0.email == email }?.tenant_id ?? ""   // dev: công ty của vai đó
        }
        let desk = Desk(api: API(base: API.defaultBase, email: email, tenant: tenant))
        await desk.load()
        await desk.refreshNotices(announce: false)
        if let raw = env["BARISTA_OPEN"], let sp = raw.firstIndex(of: " ") {
            let input = (try? JSONDecoder().decode(Input.self, from: Data(raw[sp...].utf8))) ?? [:]
            await desk.openTarget(query: String(raw[..<sp]), input: input, title: "")
        }
        if let path = env["BARISTA_ATTACH"], let data = FileManager.default.contents(atPath: path), let a = ImageIntake.attachment(from: data) {
            desk.attach(a)   // chụp ô chat có ảnh chờ gửi
            desk.draft = env["BARISTA_DRAFT"] ?? ""
        }
        let section = env["BARISTA_SECTION"] ?? "messages"
        switch section {
        case "assistant": desk.section = .assistant
        case "desk": desk.section = .desk
        case "notices": desk.section = .messages; desk.messagesPane = .notices
        case "thread": if let t = desk.talk?.items.first { desk.openTalk(t) }
        case "search": desk.showSearch = true
        case let s where s.hasPrefix("ws:"): desk.section = .workspace(String(s.dropFirst(3)))
        default: desk.section = .messages
        }
        let height = Double(env["BARISTA_SNAPSHOT_HEIGHT"] ?? "") ?? 900   // cao hơn để thấy cả thẻ dài (vd màn điều hành)
        let width = Double(env["BARISTA_SNAPSHOT_WIDTH"] ?? "") ?? 1440   // hẹp hơn để kiểm chữ không đè nhau ở khổ nhỏ
        let view = DeskShell(alwaysRail: true).environment(desk).frame(width: width, height: height)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let name = "shell-\(section.replacingOccurrences(of: ":", with: "-")).png"
        await VoiceSnapshot.render(view, size: NSSize(width: width, height: height), to: dir.appending(path: name), settle: Double(env["BARISTA_SNAPSHOT_SETTLE"] ?? "") ?? 2.0)
        print("ok:", dir.appending(path: name).path)
    }
}
#endif
