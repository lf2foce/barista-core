import SwiftUI

/// Khung làm việc — chép bố cục Barista Desk: thứ trợ lý vừa đụng tới (hoặc màn bấm mở nhanh) bung ra thành THẺ LỚN;
/// thẻ cũ lùi xuống kệ "Đã mở trước đó", bấm để kéo lên lại.
struct CanvasView: View {
    @Environment(Desk.self) private var desk
    var onBack: (() -> Void)?

    #if os(iOS)
    private let edge: CGFloat = 12   // iPhone: lề hẹp để thẻ rộng tối đa
    #else
    private let edge: CGFloat = 16
    #endif

    var body: some View {
        VStack(spacing: 0) {
            CanvasHeader(onBack: onBack)
                .padding(.horizontal, edge)
                .padding(.top, titlebarInset)
                .padding(.bottom, 8)   // không đường kẻ dưới: đầu khung nổi trên nền khung (cùng web)
            if let hero = desk.active {
                VStack(alignment: .leading, spacing: 12) {
                    CardFrame(item: hero)
                        .id(hero.id)
                        .transition(.asymmetric(
                            insertion: .scale(scale: 0.94, anchor: .topLeading).combined(with: .opacity).combined(with: .offset(x: -40)),
                            removal: .opacity))
                    let shelf = desk.builder != nil ? [] : desk.canvas.filter { $0.id != hero.id }   // trình tạo: chỉ app đang dựng
                    if !shelf.isEmpty {
                        // kệ gọn MỘT hàng chip (không chiếm ~100px như thẻ to)
                        HStack(spacing: 8) {
                            Text("Đã mở:").font(.t12m).foregroundStyle(.secondary)
                            ScrollView(.horizontal, showsIndicators: false) {
                                HStack(spacing: 6) { ForEach(shelf) { ShelfCard(item: $0) } }
                            }
                        }
                        .frame(height: 32)
                    }
                }
                .padding([.horizontal, .bottom], edge)   // trên: đầu khung đã chừa 8 ⇒ thẻ sát đầu khung, không hở hai lần
            } else {
                EmptyCanvas()
            }
        }
        .background(Color.canvas)
        .overlay(alignment: .bottom) { toast }
    }

    @ViewBuilder
    private var toast: some View {
        if let t = desk.toast {
            Text(t.text)
                .font(.t14m)
                .padding(.horizontal, 14).padding(.vertical, 8)
                .foregroundStyle(t.isError ? Color.white : Color.primary)
                .background(t.isError ? AnyShapeStyle(Color.bad) : AnyShapeStyle(.regularMaterial), in: Capsule())
                .padding(.bottom, 18)
                .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }
}

private struct CanvasHeader: View {
    @Environment(Desk.self) private var desk
    let onBack: (() -> Void)?

    #if os(iOS)
    /// iPhone: hai hàng — (thu xuống + tên) rồi dải nút màn kéo ngang; một hàng cố định bề rộng sẽ làm cả khung tràn màn hình.
    /// Khung trượt LÊN từ dưới (PhoneShell) ⇒ nút thu là mũi tên xuống, không phải "quay lại".
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                if let onBack {
                    Button(action: onBack) { Image(systemName: "chevron.down").font(.system(size: 14, weight: .semibold)) }
                        .buttonStyle(ToolChipStyle())
                        .accessibilityLabel("Thu khung làm việc")
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(desk.builder != nil ? "Xem trước app" : "Khung làm việc").font(.t16).lineLimit(1)
                    Text("Đúng quyền của \(desk.manifest?.user.name ?? "bạn")").font(.t12).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 8)
                if !desk.canvas.isEmpty {
                    Button { withAnimation(.snappy) { desk.clearCanvas() } } label: {
                        Image(systemName: "xmark.circle.fill").font(.system(size: 20)).foregroundStyle(.tertiary)
                    }
                    .buttonStyle(.plain)
                }
            }
            ScrollView(.horizontal, showsIndicators: false) { actions(labels: true) }
        }
    }
    #else
    var body: some View {
        HStack(spacing: 10) {
            if let onBack {
                Button(action: onBack) { Image(systemName: "chevron.left").font(.system(size: 14, weight: .semibold)) }
                    .buttonStyle(ToolChipStyle()).help("Về trợ lý")
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(desk.builder != nil ? "Xem trước app" : "Khung làm việc").font(.t16).lineLimit(1).fixedSize()
                Text("Dữ liệu đọc đúng quyền của \(desk.manifest?.user.name ?? "bạn")").font(.t12).foregroundStyle(.secondary).lineLimit(1)
            }
            .layoutPriority(1)
            Spacer(minLength: 8)
            // Đủ chỗ thì có chữ, hẹp thì chỉ còn biểu tượng (rê chuột xem tên) — như Barista Desk
            ViewThatFits(in: .horizontal) {
                actions(labels: true)
                actions(labels: false)
            }
            if !desk.canvas.isEmpty {
                Button { withAnimation(.snappy) { desk.clearCanvas() } } label: {
                    Image(systemName: "xmark.circle.fill").font(.system(size: 18)).foregroundStyle(.tertiary)
                }
                .buttonStyle(.plain)
                .help("Dọn khung")
            }
        }
    }

    #endif

    private func actions(labels: Bool) -> some View {
        HStack(spacing: 8) {
            if desk.builder == nil, let inbox = desk.manifest?.inbox, desk.inboxCount > 0 {
                Button { withAnimation(.snappy) { desk.open(.screen(inbox), show: true) } } label: {
                    ToolLabel(text: "\(desk.inboxCount) chờ duyệt", icon: "checkmark.seal.fill", tint: .white, labels: true)
                }
                .buttonStyle(ToolChipStyle(fill: .ok))
            }
            ForEach(desk.contextPins) { s in
                Button { withAnimation(.snappy) { desk.open(.screen(s), show: true) } } label: {
                    ToolLabel(text: s.title, icon: s.icon, tint: s.tintColor, labels: labels)
                }
                .buttonStyle(ToolChipStyle())
                .help(s.title)
            }
            #if os(iOS)
            Menu {
                if let inbox = desk.manifest?.inbox {
                    Button { desk.open(.screen(inbox), show: true) } label: { Label("Phiếu duyệt", systemImage: "checkmark.seal") }
                }
                // ADR 0255 (iPhone không có thanh bên): phân hệ "Theo vai" + "Đã ghim" mở sẵn từng màn; mọi phân hệ khác gấp trong "Tất cả mục",
                // mỗi phân hệ một menu con có nút ghim / bỏ ghim. Vai không có mặc định ⇒ liệt kê hết như trước.
                let layout = desk.rail
                ForEach(layout.onRail) { w in
                    Section(w.title) { screenButtons(w.screens) }
                }
                if layout.grouped {
                    Section("Tất cả mục") {
                        ForEach(layout.all) { i in
                            Menu {
                                screenButtons(i.ws.screens)
                                Divider()
                                Button { Task { await desk.togglePin(i) } } label: {
                                    Label(i.pinned ? "Bỏ ghim phân hệ" : "Ghim lên đầu menu", systemImage: i.pinned ? "pin.slash" : "pin")
                                }
                            } label: {
                                Label(i.ws.title, systemImage: i.pinned ? "pin.fill" : SymbolMap.name(i.ws.icon))
                            }
                        }
                    }
                }
            } label: {
                ToolLabel(text: "Tất cả màn", icon: "square.grid.2x2.fill", tint: .indigo, labels: labels)
            }
            .menuStyle(.button)
            .menuIndicator(.hidden)
            .buttonStyle(ToolChipStyle())
            .fixedSize()
            .help("Mọi màn theo phân hệ — đúng quyền của bạn")
            #endif
        }
        .fixedSize()
    }

    #if os(iOS)
    @ViewBuilder private func screenButtons(_ screens: [ScreenSpec]) -> some View {
        ForEach(screens) { s in
            Button { withAnimation(.snappy) { desk.open(.screen(s), show: true) } } label: {
                Label(s.group.isEmpty ? s.title : "\(s.title) (\(s.group.lowercased()))", systemImage: s.icon)
            }
        }
    }
    #endif
}

/// Thẻ lớn: mặt trắng bo 20, viền mảnh, không đổ bóng; đầu thẻ = ô biểu tượng tô màu + tên + nút đóng (như Barista Desk).
struct CardFrame: View {
    @Environment(Desk.self) private var desk
    let item: CanvasItem
    var onClose: (() -> Void)? = nil
    #if os(iOS)
    static let pad: CGFloat = 14   // iPhone: thẻ trong sheet — gọn như app Phê Nâu (rà 25/11)
    #else
    static let pad: CGFloat = 18
    #endif

    /// Thẻ là một MÀN ⇒ màn tự vẽ đầu thẻ (tên + bộ lọc chính + hỏi trợ lý cùng một hàng — rà bố cục 18/11).
    private var screenSpec: ScreenSpec? {
        switch item {
        case .screen(let spec): spec
        case .card(let c): c.isProposal ? nil : desk.screen(for: c.op)
        }
    }

    private var head: ScreenHead {
        ScreenHead(title: item.title, symbol: item.style.symbol, tint: item.style.tint, subtitle: subtitle) {
            if let onClose { onClose() } else { withAnimation(.snappy) { desk.close(item.id) } }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if screenSpec == nil { frameHeader }
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .padding(Self.pad)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Color.surface))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(Color.primary.opacity(0.12), lineWidth: 1))
    }

    private var frameHeader: some View {
            HStack(spacing: 10) {
                IconTile(symbol: item.style.symbol, tint: item.style.tint)
                VStack(alignment: .leading, spacing: 1) {
                    Text(item.title).font(.t16).lineLimit(1)
                    Text(subtitle).font(.t12).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 0)
                Button { if let onClose { onClose() } else { withAnimation(.snappy) { desk.close(item.id) } } } label: { Image(systemName: "xmark") }
                    .buttonStyle(.borderless).help("Đóng thẻ")
            }
    }

    private var subtitle: String {
        switch item {
        case .screen: return "Màn ghim · dữ liệu đúng quyền của bạn"
        case .card(let c):
            if c.isProposal { return desk.confirmed[c.id] ?? "Trợ lý soạn sẵn và đã chạy thử — chưa ghi gì cho tới khi bạn xác nhận" }
            if c.id.hasPrefix("open:") { return "Mở từ tin nhắn / thông báo · dữ liệu đúng quyền của bạn" }   // Desk.openTarget
            let filters = (c.subtitle ?? "").isEmpty ? "" : " · \(c.subtitle!)"
            if case .table(let t)? = c.page { return "Trợ lý mở\(filters) → \(t.rows.count) dòng" }
            return "Trợ lý mở\(filters)"
        }
    }

    @ViewBuilder
    private var content: some View {
        switch item {
        case .screen(let spec):
            ScreenView(spec: spec, api: desk.api, embedded: true, refresh: desk.canvasRev, head: head) { Task { await desk.refreshInbox() } }
        case .card(let card):
            if card.isProposal {
                ProposalBody(card: card)
            } else if let spec = desk.screen(for: card.op) {
                // cùng truy vấn với một màn ghim ⇒ có luôn bộ lọc, nút thao tác dòng, xuất Excel của màn đó
                ScreenView(spec: spec, api: desk.api, initialFilters: card.input ?? [:], initialPage: card.page, embedded: true, head: head) {
                    Task { await desk.refreshInbox() }
                }
            } else if let page = card.page {
                StaticPage(page: page)
            }
        }
    }
}

/// Thẻ nhỏ trên kệ: bấm để kéo lên thành thẻ lớn.
private struct ShelfCard: View {
    @Environment(Desk.self) private var desk
    let item: CanvasItem
    @State private var hover = false

    var body: some View {
        HStack(spacing: 6) {
            IconTile(symbol: item.style.symbol, tint: item.style.tint, size: 20)
            Text(item.title).font(.t12m).lineLimit(1)
        }
        .padding(.horizontal, 8)
        .frame(height: 30)
        .frame(maxWidth: 200, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(hover ? Color.surface2 : Color.surface))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Color.primary.opacity(0.12)))
        .contentShape(Rectangle())
        .onTapGesture { withAnimation(.snappy) { desk.activeId = item.id } }
        .onHover { hover = $0 }
        .help(item.title)
    }

    private var summary: String {
        switch item {
        case .screen: "Màn ghim"
        case .card(let c):
            if c.isProposal { desk.confirmed[c.id] ?? "Đề xuất chờ xác nhận" }
            else if case .table(let t)? = c.page { "\(t.rows.count) dòng" + (t.kpis.first.map { " · \($0.label) \(Fmt.kpi($0))" } ?? "") }
            else { "Chi tiết" }
        }
    }
}

private struct EmptyCanvas: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            ZStack {
                ForEach(0..<3) { i in
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .strokeBorder(Color.primary.opacity(0.08), style: StrokeStyle(lineWidth: 1, dash: [5, 5]))
                        .frame(width: 280, height: 170)
                        .rotationEffect(.degrees(Double(i - 1) * 6))
                        .offset(x: CGFloat(i - 1) * 26, y: CGFloat(i - 1) * -8)
                }
                Orb(mood: desk.live != nil ? .thinking : .idle, size: 64)
            }
            .frame(height: 220)
            Text("Chưa có gì mở").font(.t16)
            Text("Hỏi trợ lý ở bên trái — bảng hàng, khách, giao dịch, ví hoa hồng, dòng tiền, chiến dịch… sẽ bung ra ở đây.")
                .font(.t14).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 420)
            Text("Hoặc mở nhanh bằng các nút phía trên").font(.t12).foregroundStyle(.secondary)
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }
}

/// Trang không gắn màn ghim nào (vd gợi ý căn, profile căn): vẽ thẳng KPI + bảng / chi tiết.
private struct StaticPage: View {
    let page: Page

    var body: some View {
        switch page {
        case .detail(let d): DetailView(page: d)
        case .card(let c): CardView(page: c)
        case .table(let t):
            VStack(alignment: .leading, spacing: 12) {
                if !t.kpis.isEmpty { KPIRow(kpis: t.kpis, inset: true) }
                if !t.note.isEmpty { ClampNote(text: t.note) }
                if t.rows.isEmpty { ContentUnavailableView(t.empty, systemImage: "tray") }
                else { DataTable(page: t, selection: .constant([]), menu: { _ in EmptyView() }, open: { _ in }).cardSurface(radius: 12) }
            }
        }
    }
}

/// Thẻ đề xuất: lệnh ghi trợ lý đã soạn (và CHẠY THỬ được ở server). Người xem lại, sửa nếu cần, bấm Xác nhận ⇒ app gọi
/// đúng lệnh đó bằng quyền NGƯỜI (không phải trợ lý).
private struct ProposalBody: View {
    @Environment(Desk.self) private var desk
    let card: ChatCard
    @State private var dismissing = false

    var body: some View {
        if let done = desk.confirmed[card.id] {
            Label(done, systemImage: "checkmark.seal.fill").font(.t16).foregroundStyle(Color.ok)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        } else if let resolve = card.resolve, !resolve.isEmpty, card.state == "needs_reconciliation" {
            ResolveBody(card: card, buttons: resolve)
        } else if let state = card.state, state != "proposed" {
            // thẻ đã có kết cục / đang chạy (server nói) — không bày form cho bấm lại
            Label(card.state_label ?? "Đề xuất này đã được xử lý", systemImage: "info.circle").font(.t16).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        } else {
            VStack(alignment: .leading, spacing: 12) {
                // lượt đang dừng (0049): Xác nhận chạy lệnh bằng quyền người qua ``decide`` rồi trợ lý làm tiếp; thẻ cũ: gọi lệnh trực tiếp
                FormSheet(api: desk.api, action: action, row: nil, initial: desk.proposalEdits[card.id] ?? card.input ?? [:],
                          prefetched: card.fields, embedded: true, onEdit: { desk.proposalEdits[card.id] = $0 },
                          submitter: morning ? { input, key in await desk.decideMorning(card, confirm: true, input: input, key: key) }
                                     : resumable ? { input, key in await desk.decide(card, confirm: true, input: input, key: key) } : nil) {
                    desk.proposalDone(card, $0)
                }
                .id(card.id)
                if resumable || morning {
                    HStack(spacing: 10) {
                        Button(role: .destructive) { Task { await dismissProposal() } } label: { Label("Bỏ đề xuất", systemImage: "xmark.circle") }
                            .disabled(dismissing)
                        Text(morning ? "Em soạn sẵn sáng nay — anh/chị bấm mới chạy, bỏ thì hôm nay em không nhắc lại."
                                     : card.isAside ? "Gợi ý — ghi hay bỏ đều được, trợ lý không chờ."
                                     : "Anh/chị quyết xong, trợ lý làm tiếp phần còn lại.").font(.t12).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private var resumable: Bool { card.resumable == true }
    private var morning: Bool { card.source == "morning" }

    private func dismissProposal() async {
        dismissing = true
        defer { dismissing = false }
        let r = morning ? await desk.decideMorning(card, confirm: false, input: [:], key: UUID().uuidString)
                        : await desk.decide(card, confirm: false, input: [:], key: UUID().uuidString)
        if r.status == "ok" {
            desk.confirmed[card.id] = "Đã bỏ đề xuất"
            desk.messages.append(ChatMessage(role: "system", text: "✕ Đã bỏ đề xuất: \(card.title)"))
        } else {
            desk.show(r.error.isEmpty ? r.status : r.error, isError: true)
        }
    }

    private var action: ActionSpec {
        ActionSpec(command: card.command ?? "", title: "Xác nhận: \(card.title)", style: "primary", bind: [:], preset: [:], when: [:],
                   importable: false)
    }
}

/// ĐỐI SOÁT (rà 22/11): lệnh gửi hệ ngoài lỗi lưng chừng / quá hạn — người xem hệ ngoài rồi chọn đúng điều đã xảy ra (nút do server gửi).
/// Barista không gửi lại lệnh: "đã thực hiện" đóng thẻ là xong, "chưa thực hiện" đóng thẻ là lỗi (muốn làm thì đề xuất mới).
private struct ResolveBody: View {
    @Environment(Desk.self) private var desk
    let card: ChatCard
    let buttons: [ResolveButton]
    @State private var busy = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(card.state_label ?? "Cần đối soát với hệ ngoài", systemImage: "exclamationmark.triangle").font(.t16).foregroundStyle(.secondary)
            Text("Kiểm tra trên hệ ngoài rồi chọn đúng điều đã xảy ra — Barista không gửi lại lệnh.").font(.t12).foregroundStyle(.secondary)
            HStack(spacing: 8) {
                ForEach(Array(buttons.enumerated()), id: \.element) { i, b in
                    if i == 0 {
                        Button(busy == b.decision ? "Đang ghi…" : b.label) { Task { await pick(b) } }.buttonStyle(.borderedProminent)
                    } else {
                        Button(busy == b.decision ? "Đang ghi…" : b.label) { Task { await pick(b) } }.buttonStyle(.bordered)
                    }
                }
            }
            .disabled(!busy.isEmpty)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func pick(_ b: ResolveButton) async {
        busy = b.decision
        defer { busy = "" }
        let r = await desk.resolve(card, decision: b.decision)
        if ["ok", "failed"].contains(r.status) {
            desk.confirmed[card.id] = r.status == "ok" ? "Đã đối soát: việc đã thực hiện ở hệ ngoài" : "Đã đối soát: việc chưa thực hiện"
            Task { await desk.refreshInbox() }
        } else {
            desk.show(r.error.isEmpty ? r.status : r.error, isError: true)
        }
    }
}

extension View {
    /// Mặt thẻ phẳng: trắng / #1C1C1E, viền mảnh, không đổ bóng — như Barista Desk.
    func cardSurface(radius: CGFloat = 16) -> some View {
        background(RoundedRectangle(cornerRadius: radius, style: .continuous).fill(Color.surface))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous).strokeBorder(Color.primary.opacity(0.12)))
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
    }
}
