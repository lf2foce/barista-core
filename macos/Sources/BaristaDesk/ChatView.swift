import SwiftUI

/// Cột trò chuyện — chép bố cục Barista Desk: thanh đầu (trợ lý của …), hội thoại kiểu tin nhắn, ô nhập bo tròn.
struct ChatColumn: View {
    @Environment(Desk.self) private var desk
    @FocusState private var focused: Bool
    @State private var droppedDocuments: [URL] = []   // chứng từ thả vào ⇒ hộp chọn kệ rồi mới tải lên (DocumentDrop.swift)

    var body: some View {
        VStack(spacing: 0) {
            ChatHeader()
            Conversation()
                #if os(macOS)
                .onDrop(of: [.image, .fileURL], isTargeted: nil) { providers in
                    desk.handleDrop(providers) { droppedDocuments = $0 }   // PDF / Word ⇒ hộp chọn kệ; ảnh ⇒ đính kèm như cũ
                    return true
                }
                .sheet(isPresented: Binding(get: { !droppedDocuments.isEmpty }, set: { if !$0 { droppedDocuments = [] } })) {
                    DocumentDropSheet(urls: droppedDocuments)
                }
                #endif
                // nút nổi neo theo vùng hội thoại ⇒ luôn nằm TRÊN ô nhập / khối Siri dù khối đó cao bao nhiêu
                .overlay(alignment: .bottomTrailing) {
                    if desk.isNarrow && !desk.canvas.isEmpty {
                        Button { withAnimation(.snappy) { desk.narrowShowsCanvas = true } } label: {
                            Label("Khung làm việc · \(desk.canvas.count)", systemImage: "rectangle.stack.fill")
                                .font(.t14m).padding(.horizontal, 14).padding(.vertical, 10)
                                .foregroundStyle(.white).background(Capsule().fill(Color.accent))
                                .shadow(color: .black.opacity(0.18), radius: 8, y: 3)
                        }
                        .buttonStyle(.plain)
                        .padding(.trailing, 16).padding(.bottom, 12)
                    }
                }
                .clipped()
                .background { ZStack { WA.wall; Doodles() } }
            ComposerBar(focused: $focused)
        }
    }
}

private struct ChatHeader: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        HStack(spacing: 12) {
            Button { desk.startVoiceMode() } label: { Orb(mood: desk.live != nil ? .thinking : .idle, size: 40) }
                .buttonStyle(.plain)
                .help("Nói chuyện với trợ lý (⌥⌘V)")
            VStack(alignment: .leading, spacing: 1) {
                Text(desk.builder.map { b in
                    let name = b.appId.flatMap { id in desk.manifest?.workspaces.flatMap(\.screens).first { $0.key == "app-\(id)" }?.title }
                    return "Trình tạo app" + (name.map { " · \($0)" } ?? "")
                } ?? "Trợ lý của \(desk.firstName)").font(.t16).lineLimit(1)
                HStack(spacing: 5) {
                    Circle().fill(desk.loadError == nil ? (desk.builder != nil ? Color.accent : Color.ok) : Color.secondary.opacity(0.5)).frame(width: 7, height: 7)
                    Text(desk.live != nil ? "đang làm…" : desk.builder.map { $0.appId == nil ? "App mới — em hỏi vài câu rồi dựng" : "Đang sửa app — xem bên cạnh" }
                         ?? desk.subtitle).font(.t12).foregroundStyle(WA.meta).lineLimit(1)
                }
            }
            Spacer(minLength: 8)
            #if os(iOS)
            NoticeBell()   // Mac: thông báo nằm trong "Tin nhắn" ở thanh bên trái; iPhone: "Việc" / "Tin nhắn" ở thanh tab dưới đáy
            #endif
            HeaderIcon(symbol: "square.and.pencil", help: "Cuộc trò chuyện mới") { desk.newThread() }
            Menu {
                if desk.threads.isEmpty { Text("Chưa có cuộc trò chuyện nào") }
                ForEach(desk.threads.prefix(20)) { t in
                    Button(t.title.isEmpty ? "(không tên)" : t.title) { Task { await desk.openThread(t.id) } }
                }
            } label: { IconTile(symbol: "clock.arrow.circlepath", tint: .purple, size: 30) }
            .menuStyle(.button)
            .buttonStyle(.plain)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Cuộc trò chuyện trước")
            #if os(iOS)
            PersonaMenu()   // Mac: ở đáy thanh bên trái
            #endif
        }
        .padding(.horizontal, 14)
        .padding(.top, titlebarInset)
        .padding(.bottom, 10)
        .background(WA.bar)
        .overlay(alignment: .bottom) { Rectangle().fill(Color.hairline).frame(height: 1) }
    }
}

/// Nút người đang dùng (cam đỏ như Barista Desk): xem đang ở vai nào, đổi vai.
struct PersonaMenu: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        Menu {
            if let m = desk.manifest { Text("\(m.user.name) · \(m.user.role_label)") }
            Button { Task { await desk.unregisterPush(); desk.switchPersona() } } label: {
                Label(desk.api.token == nil ? "Đổi vai…" : "Đăng xuất", systemImage: "rectangle.portrait.and.arrow.right")
            }
        } label: {
            Initials(name: desk.manifest?.user.name ?? "?", size: 34,
                     colors: [Color(red: 1.0, green: 0.62, blue: 0.18), Color(red: 1.0, green: 0.27, blue: 0.22)])
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .fixedSize()
        .disabled(desk.live != nil)
        .help("Đổi vai — xem hệ thống dưới góc nhìn người khác")
    }
}

/// Nút tiện ích đầu cột — ô vuông bo góc, gradient màu (như IconTile), không còn vòng tròn xám nhạt.
struct HeaderIcon: View {
    let symbol: String
    let help: String
    var tint: Color = .accent
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) { IconTile(symbol: symbol, tint: tint, size: 30) }
            .buttonStyle(.plain)
            .opacity(hover ? 1 : 0.88)
            .scaleEffect(hover ? 1.04 : 1)
            .animation(.snappy(duration: 0.15), value: hover)
            .onHover { hover = $0 }
            .help(help)
    }
}

/// Hộp tham chiếu KHÔNG quan sát giữ ``TailFollow`` — ghi mỗi mảnh chữ stream mà vẽ lại cả lịch sử thì giật.
private final class TailScroll { var plan = TailFollow() }

/// Kiểu ChatGPT (học từ barista_bds, luật ở ``TailFollow``): gửi xong, câu hỏi lên ĐỈNH khung và đứng yên, câu trả lời chảy dần xuống khoảng
/// trống bên dưới — khung KHÔNG trôi theo từng mảnh chữ (đang đọc từ đầu câu trả lời mà khung nhúc nhích thì không đọc được); stream xong mới
/// kéo xuống cuối MỘT lần, trừ khi người dùng đã tự cuộn trong lượt này.
private struct Conversation: View {
    @Environment(Desk.self) private var desk
    @State private var viewport: CGFloat = 0
    @State private var scroll = TailScroll()
    /// Tăng khi nạp cả lịch sử / đổi cuộc trò chuyện: dựng lại ScrollView để vị trí ban đầu bám đáy áp lại.
    @State private var loadEpoch = 0

    private struct Update: Equatable {
        let count: Int
        let lastID: UUID?
    }

    var body: some View {
        let messages = desk.messages
        // Đuôi = câu hỏi của người dùng gần nhất + mọi thứ sau nó (trả lời, lượt đang stream).
        let tailStart = messages.lastIndex(where: { $0.role == "user" })
        ScrollViewReader { proxy in
            ScrollView {
                // VStack (không Lazy): Lazy chỉ ƯỚC LƯỢNG chiều cao hàng chưa dựng ⇒ scrollTo vào đuôi hạ cánh lệch.
                VStack(alignment: .leading, spacing: 14) {
                    if messages.isEmpty && desk.live == nil { if let b = desk.builder { BuilderIntro(appId: b.appId) } else { Welcome() } }
                    ForEach(0..<(tailStart ?? messages.count), id: \.self) { i in Bubble(message: messages[i]) }
                    if let tailStart {
                        VStack(alignment: .leading, spacing: 14) {
                            ForEach(tailStart..<messages.count, id: \.self) { i in Bubble(message: messages[i]) }
                            if let live = desk.live { LiveBubble(turn: live) }
                            // mốc đo ĐÁY câu trả lời (toạ độ trong khung nhìn)
                            Color.clear.frame(height: 4)
                                .onGeometryChange(for: CGFloat.self) { $0.frame(in: .scrollView).maxY } action: { y in
                                    perform(scroll.plan.endMoved(to: y), proxy)
                                }
                        }
                        // cao ít nhất bằng khung ⇒ đỉnh đuôi chạm được mép trên khung
                        .frame(minHeight: max(0, viewport - 28), alignment: .top)
                        .onGeometryChange(for: CGFloat.self) { $0.frame(in: .scrollView).minY } action: { y in
                            perform(scroll.plan.tailTopMoved(to: y), proxy)
                        }
                        .id("tail")
                    } else if let live = desk.live {
                        LiveBubble(turn: live)
                    }
                }
                .padding(.horizontal, 16).padding(.vertical, 14)
            }
            .id(loadEpoch)
            .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { h in
                if abs(h - viewport) >= 2 { viewport = h }
                perform(scroll.plan.viewportChanged(h), proxy)
            }
            .scrollIndicators(.never)
            .defaultScrollAnchor(.bottom, for: .initialOffset)
            .defaultScrollAnchor(.top, for: .sizeChanges)
            .onScrollPhaseChange { _, phase in
                switch phase {
                case .interacting, .decelerating: scroll.plan.userBegan()
                case .animating: scroll.plan.animationBegan()
                case .idle:
                    scroll.plan.userEnded(turnActive: desk.live != nil)
                    perform(scroll.plan.animationEnded(), proxy)
                default: break
                }
            }
            #if os(iOS)
            .scrollDismissesKeyboard(.interactively)
            #endif
            .onChange(of: Update(count: messages.count, lastID: messages.last?.id)) { old, new in
                guard old.count != new.count || old.lastID != new.lastID else { return }
                guard let last = desk.messages.last else { return }
                if last.role == "user" {
                    // vừa gửi (gõ, bấm gợi ý, hoặc câu hỏi bằng giọng nói): câu hỏi lên ĐỈNH rồi chờ chữ chảy xuống
                    let action = scroll.plan.sent()
                    DispatchQueue.main.async { perform(action, proxy, animated: true) }
                } else if new.count - old.count != 1 {
                    scroll.plan = TailFollow()   // nạp cả lịch sử / đổi cuộc trò chuyện: về cuối
                    loadEpoch += 1
                } else {
                    // câu trả lời vừa stream xong: kéo xuống cuối MỘT lần (người dùng đã tự cuộn thì để yên)
                    let action = scroll.plan.finished()
                    DispatchQueue.main.async { perform(action, proxy, animated: true) }
                }
            }
        }
    }

    private func perform(_ action: TailFollow.Action, _ proxy: ScrollViewProxy, animated: Bool = false) {
        let anchor: UnitPoint
        switch action {
        case .none: return
        case .scrollTop: anchor = .top
        case .scrollEnd: anchor = .bottom
        }
        if animated { withAnimation(.snappy) { proxy.scrollTo("tail", anchor: anchor) } } else { proxy.scrollTo("tail", anchor: anchor) }
    }
}

/// Màn chào của TRÌNH TẠO APP (0098, như web ``BuilderIntro``): không phải lời chào trợ lý hằng ngày (cảnh báo, việc gấp). App mới ⇒ ý tưởng hợp vai
/// (server ``apps.builder_ideas`` dựng từ tập dữ liệu người này đọc được); sửa app ⇒ gợi ý sửa. Server chưa có truy vấn ⇒ câu mặc định.
private struct BuilderIntro: View {
    @Environment(Desk.self) private var desk
    let appId: String?
    @State private var ideas: [String]? = nil

    struct Ideas: Decodable { struct Idea: Decodable { let text: String }; let ideas: [Idea] }

    static let fallbackNew = ["Màn điều hành công nợ: còn phải thu, theo tháng đến hạn, danh sách quá hạn có nút nhắc",
                              "Việc hôm nay của đội tôi: khách cần gọi, hồ sơ kẹt, việc trễ hạn",
                              "Doanh thu và chi phí theo tháng, so với cùng kỳ"]
    static let fallbackEdit = ["Thêm biểu đồ xu hướng theo tháng cho số chính",
                               "Thêm bộ lọc ở đầu trang (kỳ, nhóm hay dùng nhất)",
                               "Thêm bảng các dòng cần xử lý ngay (vd quá hạn) dưới biểu đồ"]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(appId != nil ? "Muốn sửa gì ở app này?" : "Anh/chị muốn app làm gì?").font(.t20)
            Text(appId != nil ? "Nói phần muốn đổi — em chỉ sửa đúng chỗ đó, app bên cạnh cập nhật ngay."
                              : "Kể ngắn app để xem gì, làm gì. Em hỏi lại vài câu và gợi ý cách làm hay hơn, rồi dựng từng phần — app hiện ngay bên cạnh, đúng dữ liệu và quyền của anh/chị.")
                .font(.t14).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let ideas {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(ideas, id: \.self) { t in
                        Button { desk.send(t) } label: {
                            HStack(alignment: .top) {
                                Text(t).font(.t14).multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                                Spacer(minLength: 0)
                                Image(systemName: "arrow.up.right").font(.system(size: 11, weight: .bold)).foregroundStyle(WA.meta)
                            }
                            .padding(.horizontal, 14).padding(.vertical, 10)
                            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface2))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .padding(.top, 8)
        .task(id: appId) {
            let input: Input = appId.map { ["app_id": .string($0)] } ?? [:]
            if let got: Ideas = try? await desk.api.data("apps.builder_ideas", input), !got.ideas.isEmpty {
                ideas = got.ideas.map(\.text)
            } else {
                ideas = appId != nil ? Self.fallbackEdit : Self.fallbackNew
            }
        }
    }
}

private struct Welcome: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Chào \(desk.firstName) 👋").font(.t20)
            if !desk.morning.isEmpty {
                // thẻ đề xuất sáng ở BÀN VIỆC (nhóm "Cần tôi quyết", 0094) — lời chào chỉ dẫn sang, không làm chỗ thứ hai
                Button { withAnimation(.snappy) { desk.section = .desk } } label: {
                    HStack(spacing: 10) {
                        Image(systemName: "sparkles").font(.system(size: 13)).foregroundStyle(Color.accent)
                        Text("Em soạn sẵn \(desk.morning.count) đề xuất sáng nay — xem ở Việc").font(.t14m)
                        Spacer(minLength: 6)
                        Image(systemName: "chevron.right").font(.system(size: 11)).foregroundStyle(.tertiary)
                    }
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.accent.opacity(0.08)))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            if desk.attention.isEmpty {
                Label("Hôm nay chưa có gì gấp", systemImage: "checkmark.circle.fill").font(.t14).foregroundStyle(Color.ok)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Hôm nay em thấy").font(.t12m).foregroundStyle(.secondary)
                    ForEach(desk.attention) { a in AttentionRow(item: a) }
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                Text("Hoặc hỏi em").font(.t12m).foregroundStyle(.secondary)
                ForEach(desk.suggestions, id: \.self) { s in
                    Button { desk.send(s) } label: {
                        HStack {
                            Text(s).font(.t14).multilineTextAlignment(.leading)
                            Spacer(minLength: 0)
                            Image(systemName: "arrow.up.right").font(.system(size: 11, weight: .bold)).foregroundStyle(WA.meta)
                        }
                        .padding(.horizontal, 14).padding(.vertical, 10)
                        .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(WA.incoming))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            Text("Việc cần ghi, em soạn sẵn để anh/chị bấm xác nhận — em không tự làm thay.").font(.t12).foregroundStyle(.secondary)
        }
        .padding(.top, 8)
    }
}

/// Ghi chú hiện trường (0074 D): bấm ghi — đang ghi hiện đồng hồ đỏ — bấm lần nữa gửi; trợ lý ghi chăm sóc / hẹn lại / tạo việc thành đề xuất.
/// Mic đọc chính tả nằm TRONG ô nhập: bấm ghi — đang ghi thành nút đỏ + đồng hồ — bấm lần nữa ⇒ chép lời, chữ chèn vào ô (không gửi luôn).
private struct DictateMic: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        let rec = desk.fieldRecorder
        Button { desk.toggleDictation() } label: {
            if rec.isRecording, let start = rec.startedAt {
                TimelineView(.periodic(from: start, by: 1)) { ctx in
                    let s = Int(ctx.date.timeIntervalSince(start))
                    Label(String(format: "%d:%02d", s / 60, s % 60), systemImage: "stop.fill")
                        .font(.t12m).monospacedDigit().foregroundStyle(.white)
                        .padding(.horizontal, 9).frame(height: 28)
                        .background(Capsule().fill(Color.bad))
                }
            } else if desk.transcribing {
                ProgressView().controlSize(.small).frame(width: 28, height: 28)
            } else {
                Image(systemName: "mic").font(.system(size: 15, weight: .medium)).foregroundStyle(.secondary)
                    .frame(width: 28, height: 28).contentShape(Rectangle())
            }
        }
        .buttonStyle(.plain)
        .disabled(desk.transcribing)
        .help(rec.isRecording ? "Bấm để dừng — chữ sẽ hiện vào ô" : "Đọc chính tả — nói, chữ hiện vào ô để bạn sửa rồi gửi")
    }
}

/// Một đề xuất soạn sẵn (0074 A): việc nên làm + vì sao (code tính); bấm ⇒ mở thẻ đề xuất ở khung làm việc (sửa được, Xác nhận / Bỏ).
struct MorningRow: View {
    @Environment(Desk.self) private var desk
    let card: ChatCard
    @State private var hover = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "wand.and.stars").font(.system(size: 13, weight: .semibold)).foregroundStyle(Color.accent).padding(.top, 2)
            VStack(alignment: .leading, spacing: 2) {
                Text(card.title).font(.t14m).multilineTextAlignment(.leading)
                if let why = card.subtitle, !why.isEmpty { Text(why).font(.t12).foregroundStyle(.secondary).multilineTextAlignment(.leading) }
            }
            Spacer(minLength: 6)
            Image(systemName: "chevron.right").font(.system(size: 11)).foregroundStyle(.tertiary).padding(.top, 4)
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(hover ? Color.accent.opacity(0.12) : Color.accent.opacity(0.07)))
        .contentShape(Rectangle())
        .onTapGesture { withAnimation(.snappy) { desk.open(.card(card)) } }
        .onHover { hover = $0 }
    }
}

/// Một việc em phát hiện: chấm màu + câu nói như trợ lý + một dòng "em đã làm gì / cần anh làm gì"; bấm ⇒ mở màn đó.
private struct AttentionRow: View {
    @Environment(Desk.self) private var desk
    let item: Attention
    @State private var hover = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(Color.card(item.tone)).frame(width: 8, height: 8).padding(.top, 6)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.t14m)
                Text(item.detail).font(.t12).foregroundStyle(.secondary)
            }
            Spacer(minLength: 6)
            if let q = item.ask {
                Button { desk.send(q) } label: { Image(systemName: "sparkles").font(.system(size: 12)).foregroundStyle(Color.accent) }
                    .buttonStyle(.plain).help("Nhờ em xử lý")
            }
            Image(systemName: "chevron.right").font(.system(size: 11)).foregroundStyle(.tertiary).padding(.top, 4)
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(hover ? Color.primary.opacity(0.07) : WA.incoming))
        .contentShape(Rectangle())
        .onTapGesture { desk.openAttention(item) }
        .onHover { hover = $0 }
    }
}

private struct Bubble: View {
    @Environment(Desk.self) private var desk
    let message: ChatMessage
    @State private var showSteps = false
    @State private var sharing: ActionSpec?
    @State private var confirmingAll = false

    /// Đề xuất của lượt đang dừng mà người chưa quyết (0049).
    private var waiting: [ChatCard] { message.cards.filter { $0.isProposal && !$0.isAside && $0.resumable == true && desk.confirmed[$0.id] == nil } }

    /// "Nói sau buổi gặp" thường ra nhiều thẻ cùng lúc ⇒ một nút xác nhận hết; trợ lý chạy tiếp sau thẻ cuối.
    private func confirmAll() async {
        confirmingAll = true
        defer { confirmingAll = false }
        for card in waiting {
            let r = await desk.decide(card, confirm: true, input: desk.proposalEdits[card.id] ?? card.input ?? [:], key: UUID().uuidString)
            if r.status == "ok" || r.status == "pending_approval" {
                desk.proposalDone(card, r.status == "pending_approval" ? "Đã gửi phiếu duyệt" : "Đã xong: \(card.title)")
            } else {
                desk.show(r.error.isEmpty ? r.status : r.error, isError: true)
                withAnimation(.snappy) { desk.open(.card(card), show: true) }
                return
            }
        }
    }

    /// Chia sẻ câu trả lời vào trao đổi (0046): chữ người dùng xem / sửa trước khi gửi; thẻ chỉ gửi THAM CHIẾU (truy vấn + tham số) —
    /// đồng nghiệp mở thẻ thì chạy lại bằng quyền của họ, không thấy số vượt quyền.
    private func shareAction(_ target: ShareTarget) -> ActionSpec {
        var preset: Input = ["subject_kind": .string(target.subjectKind), "subject_id": .string(target.subjectId)]
        if let c = resultCards.last(where: { !$0.isProposal }), let q = c.query {
            preset["card"] = .object(["query": .string(q), "input": .object(c.input ?? [:]), "title": .string(c.title)])
        }
        return ActionSpec(command: "talk.post", title: "Chia sẻ vào trao đổi “\(target.title)”", style: "primary", bind: [:], preset: preset,
                          when: [:], importable: false)
    }

    /// Dưới câu trả lời chỉ hiện KẾT QUẢ: mọi thẻ đề xuất (cần người bấm) + thẻ kết quả cuối (bỏ bảng rỗng). Thẻ của các bước trung gian
    /// vẫn mở được trong "Đã làm N bước" (bấm vào bước).
    private var resultCards: [ChatCard] {
        func empty(_ c: ChatCard) -> Bool { if case .table(let t)? = c.page { return t.rows.isEmpty }; return false }
        let shown = message.cards.filter { !$0.isAside }   // gợi ý bên lề vẽ riêng (AsideRow)
        let final = shown.last { !$0.isProposal && !empty($0) } ?? shown.last { !$0.isProposal }
        return shown.filter { $0.isProposal || $0.id == final?.id }
    }

    var body: some View {
        switch message.role {
        case "user":
            HStack {
                Spacer(minLength: 60)
                VStack(alignment: .trailing, spacing: 2) {
                    if !message.images.isEmpty {
                        HStack(spacing: 6) { ForEach(Array(message.images.enumerated()), id: \.offset) { _, d in Thumb(data: d, size: 88) } }
                            .padding(.bottom, 4)
                    } else if message.imagesGone > 0 {
                        Label("\(message.imagesGone) ảnh (không lưu lại)", systemImage: "photo").font(.t12).foregroundStyle(.white.opacity(0.85))
                    }
                    Text(message.text.replacingOccurrences(of: #" \(mã tài liệu [0-9a-f-]{36}\)"#, with: "", options: .regularExpression)).font(.t14).foregroundStyle(.white).textSelection(.enabled)   // mã tài liệu chỉ dành cho trợ lý
                    HStack(spacing: 3) {
                        Text(Fmt.time(message.at))
                        Image(systemName: "checkmark").fontWeight(.bold)
                    }
                    .font(.system(size: 10)).foregroundStyle(.white.opacity(0.8))
                }
                .padding(.horizontal, 12).padding(.top, 7).padding(.bottom, 5)
                .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(WA.outgoing))
            }
        case "system":
            Text(message.text)
                .font(.t12).foregroundStyle(WA.meta)
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(RoundedRectangle(cornerRadius: 8).fill(WA.chip))
                .frame(maxWidth: .infinity)
        default:
            VStack(alignment: .leading, spacing: 8) {
                if !message.steps.isEmpty {
                    Button { withAnimation(.snappy) { showSteps.toggle() } } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "checkmark.circle.fill").font(.system(size: 13, weight: .semibold)).foregroundStyle(Color.ok)
                            Text("Đã làm \(message.steps.count) bước").font(.t12m)
                            Image(systemName: "chevron.right").font(.system(size: 10, weight: .bold)).foregroundStyle(WA.meta)
                                .rotationEffect(.degrees(showSteps ? 90 : 0))
                        }
                        .padding(.horizontal, 10).padding(.vertical, 5)
                        .background(Capsule().fill(Color.surface))
                        .overlay(Capsule().strokeBorder(Color.primary.opacity(0.12)))
                        .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    if showSteps { StepList(steps: message.steps, cards: message.cards) }
                }
                MarkdownText(text: message.text)
                ForEach(resultCards) { card in
                    // thẻ gọn ngay trong câu trả lời (0094 đợt 2): xem / làm luôn ở đây — "Mở" mới ra khung đầy đủ
                    if let preview = card.preview, let q = card.query {
                        CompactCard(title: card.title, query: q, input: card.input ?? [:], initial: preview) {
                            withAnimation(.snappy) { desk.open(.card(card), show: true) }
                        }
                    } else {
                        PreviewRow(card: card)
                    }
                }
                ForEach(message.cards.filter(\.isAside)) { card in AsideRow(card: card) }
                if waiting.count >= 2 {
                    Button { Task { await confirmAll() } } label: {
                        Label(confirmingAll ? "Đang xác nhận…" : "Xác nhận cả \(waiting.count) đề xuất", systemImage: "checkmark.circle.fill")
                    }
                    .buttonStyle(.borderedProminent).controlSize(.small)
                    .disabled(confirmingAll || desk.live != nil)
                    .help("Chạy lần lượt từng đề xuất bằng quyền của bạn (đúng như đang hiện trên thẻ) — thẻ nào lỗi thì dừng và mở thẻ đó để sửa")
                }
                if let target = desk.shareTarget, !message.text.isEmpty {
                    Button { sharing = shareAction(target) } label: {
                        Label("Chia sẻ vào trao đổi “\(target.title)”", systemImage: "arrowshape.turn.up.right")
                    }
                    .buttonStyle(.bordered).controlSize(.small)
                    .help("Câu trả lời này chỉ mình bạn thấy — bấm để gửi vào trao đổi của hồ sơ (bạn xem lại chữ trước khi gửi)")
                }
                Text(Fmt.time(message.at)).font(.system(size: 10)).foregroundStyle(WA.meta)
            }
            .padding(.horizontal, 4)
            .frame(maxWidth: .infinity, alignment: .leading)
            .sheet(item: $sharing) { a in
                FormSheet(api: desk.api, action: a, row: nil, initial: ["body": .string(message.text)]) { desk.show($0) }
            }
        }
    }
}

/// Gợi ý BÊN LỀ (vd ghi nhớ): trợ lý đã trả lời xong, không chờ — một dòng nhỏ, bấm tại chỗ (qua ``decide``: chạy bằng quyền người, nguồn là lượt
/// này, "Thôi" thành tín hiệu bỏ). Bấm chữ ⇒ mở thẻ để sửa trước khi ghi.
private struct AsideRow: View {
    @Environment(Desk.self) private var desk
    let card: ChatCard
    @State private var busy = false

    private var verb: String { card.verb ?? "Đồng ý" }
    private var dropped: Bool { ["Đã bỏ qua", "Đã bỏ đề xuất"].contains(desk.confirmed[card.id] ?? "") }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "brain.head.profile").font(.system(size: 14, weight: .semibold)).foregroundStyle(Color.pink)
            Button { withAnimation(.snappy) { desk.open(.card(card)) } } label: {
                Text(card.title).font(.t12).lineLimit(2).multilineTextAlignment(.leading).frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain).help("Mở để sửa trước khi \(verb.lowercased())")
            if desk.confirmed[card.id] != nil {
                Text(dropped ? "Đã bỏ qua" : "✓ Đã \(verb.lowercased())").font(.t12m).foregroundStyle(dropped ? WA.meta : Color.ok)
            } else {
                HStack(spacing: 6) {
                    Button(verb) { Task { await decide(true) } }.buttonStyle(.borderedProminent)
                    Button("Thôi") { Task { await decide(false) } }.buttonStyle(.bordered)
                }
                .controlSize(.small).disabled(busy)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(Color.primary.opacity(0.14), style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
        .opacity(desk.confirmed[card.id] != nil ? 0.75 : 1)
    }

    private func decide(_ confirm: Bool) async {
        busy = true
        defer { busy = false }
        let r = await desk.decide(card, confirm: confirm, input: confirm ? desk.proposalEdits[card.id] ?? card.input ?? [:] : [:], key: UUID().uuidString)
        if r.status == "ok" || r.status == "pending_approval" {
            desk.confirmed[card.id] = confirm ? "Đã xong: \(card.title)" : "Đã bỏ qua"
        } else {
            desk.show(r.error.isEmpty ? r.status : r.error, isError: true)
            withAnimation(.snappy) { desk.open(.card(card), show: true) }   // lỗi (vd khách đã chuyển người) ⇒ mở thẻ để xem / sửa
        }
    }
}

/// Một thẻ trợ lý đã tạo — như "xem trước liên kết"; bấm để đưa lên khung làm việc.
private struct PreviewRow: View {
    @Environment(Desk.self) private var desk
    let card: ChatCard
    @State private var hover = false

    var body: some View {
        let style = CanvasItem.card(card).style
        Button { withAnimation(.snappy) { desk.open(.card(card)) } } label: {
            HStack(spacing: 12) {
                IconTile(symbol: style.symbol, tint: style.tint, size: 34)
                VStack(alignment: .leading, spacing: 2) {
                    Text(card.isProposal ? "Đề xuất: \(card.title)" : card.title).font(.t14m).lineLimit(1)
                    Text(subtitle).font(.t12).foregroundStyle(desk.confirmed[card.id] != nil ? Color.ok : WA.meta).lineLimit(1)
                }
                Spacer(minLength: 8)
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 11, weight: .bold)).foregroundStyle(WA.meta)
                    .frame(width: 26, height: 26)
                    .background(Circle().fill(Color.primary.opacity(hover ? 0.1 : 0.05)))
            }
            .padding(.horizontal, 10).padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(hover ? Color.surface2 : Color.surface))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.primary.opacity(0.1)))
            .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }

    private var subtitle: String {
        if let done = desk.confirmed[card.id] { return done }
        if card.isProposal { return "Chưa ghi gì — bấm để xem lại và xác nhận" }
        let filters = card.subtitle.map { $0.isEmpty ? "" : "\($0) · " } ?? ""
        if case .table(let t)? = card.page { return "\(filters)\(t.rows.count) dòng" }
        return filters.isEmpty ? "Mở trên khung làm việc" : String(filters.dropLast(3))
    }
}

/// Lượt đang chạy: các bước hiện dần, rồi chữ chảy vào.
private struct LiveBubble: View {
    let turn: LiveTurn

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "sparkles").foregroundStyle(Color.accent)
                ShimmerText(text: turn.status)
            }
            if !turn.steps.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(turn.steps) { s in
                        HStack(spacing: 6) {
                            if s.done == nil {
                                ProgressView().controlSize(.mini)
                            } else {
                                Image(systemName: s.done == true ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                                    .foregroundStyle(s.done == true ? Color.accent : Color.warn)
                            }
                            Text(s.title).lineLimit(1)
                            if let d = (s.error?.isEmpty == false ? s.error : nil) ?? s.summary, !d.isEmpty {
                                Text("· \(d)").lineLimit(1).foregroundStyle(WA.meta)
                            }
                        }
                        .font(.t12)
                        .transition(.move(edge: .leading).combined(with: .opacity))
                    }
                }
            }
            if turn.text.isEmpty { TypingDots() } else { MarkdownText(text: turn.text) }
        }
        .padding(.horizontal, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct ComposerBar: View {
    @Environment(Desk.self) private var desk
    @FocusState.Binding var focused: Bool
    #if os(macOS)
    @State private var pasteMonitor: Any?
    #endif

    private var hasText: Bool { !desk.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        if (desk.voice.isOn || desk.voice.problem != nil) && !desk.voiceMode {
            SiriPill().padding(.horizontal, 10).padding(.vertical, 8)   // đang nói: khối Siri thay ô nhập
        } else {
            composer
        }
    }

    private var composer: some View {
        @Bindable var desk = desk
        return VStack(spacing: 6) {
            DraftImages()
            if !desk.messages.isEmpty && !hasText && desk.live == nil && !desk.suggestions.isEmpty && !desk.voice.isOn {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(desk.suggestions, id: \.self) { s in
                            Button { desk.send(s) } label: {
                                Text(s).font(.t12).lineLimit(1)
                                    .padding(.horizontal, 10).padding(.vertical, 6)
                                    .background(Capsule().fill(Color.surface))
                                    .overlay(Capsule().strokeBorder(Color.primary.opacity(0.12)))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            HStack(alignment: .bottom, spacing: 8) {
                #if os(macOS)
                Button { for a in ImageIntake.pick() { desk.attach(a) } } label: {
                    Image(systemName: "photo.badge.plus").font(.system(size: 15, weight: .medium)).foregroundStyle(.secondary)
                        .frame(width: 32, height: 36)
                }
                .buttonStyle(.plain)
                .help("Gửi ảnh (đoạn chat với khách, phiếu cọc…) — hoặc dán ⌘V / kéo thả vào khung chat")
                #endif
                TextField(desk.draftImages.isEmpty ? "Hỏi trợ lý…" : "Nhờ trợ lý làm gì với ảnh này…", text: $desk.draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.t14)
                    .lineLimit(1...6)
                    .focused($focused)
                    .onSubmit { desk.send() }
                    .onChange(of: desk.focusChat) { focused = true }
                    .padding(.vertical, 9)
                    .padding(.leading, 14).padding(.trailing, desk.fieldRecorder.isRecording ? 84 : 36)
                    .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(WA.field))
                    .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(Color.primary.opacity(0.1)))
                    // mic ĐỌC CHÍNH TẢ trong ô nhập (kiểu ChatGPT): nói xong chữ hiện vào ô — người xem / sửa rồi tự gửi
                    .overlay(alignment: .bottomTrailing) { DictateMic().padding(.trailing, 6).padding(.bottom, 4) }
                if desk.live != nil, desk.turnKey != nil {   // đang trả lời ⇒ "Dừng": server huỷ lượt (phần đã làm vẫn lưu)
                    Button { desk.stopTurn() } label: {
                        ZStack {
                            Circle().fill(Color.primary)
                            RoundedRectangle(cornerRadius: 2).fill(Color.surface).frame(width: 11, height: 11)
                        }
                        .frame(width: 36, height: 36)
                    }
                    .buttonStyle(.plain)
                    .help("Dừng")
                } else if hasText || desk.voice.isOn || !desk.draftImages.isEmpty {
                    Button { desk.send() } label: {
                        ZStack {
                            Circle().fill(hasText || !desk.draftImages.isEmpty ? Color.accent : Color.secondary.opacity(0.3))
                            Image(systemName: "arrow.up").font(.system(size: 16, weight: .bold)).foregroundStyle(.white)
                        }
                        .frame(width: 36, height: 36)
                    }
                    .buttonStyle(.plain)
                    .disabled((!hasText && desk.draftImages.isEmpty) || desk.live != nil)
                    .help("Gửi (↩)")
                } else {
                    // ô trống ⇒ nút trợ lý giọng nói: nói chuyện (giọng nói đi qua đúng một lượt chat thật — quyền như gõ chữ)
                    Button { desk.startVoiceMode() } label: { SiriButton() }
                        .buttonStyle(.plain)
                        .help("Nói chuyện với trợ lý")
                }
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(WA.bar)
        .overlay(alignment: .top) { Rectangle().fill(Color.hairline).frame(height: 1) }
        .onAppear {
            focused = true
            #if os(macOS)
            // ⌘V khi bảng nháp có ẢNH mà không có chữ (chụp màn hình, "Sao chép ảnh" từ Zalo / trình duyệt) ⇒ đính ảnh; còn lại để ô chat
            // dán chữ như thường (kể cả copy ô Excel — có cả chữ lẫn ảnh xem trước)
            pasteMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { ev in
                guard ev.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command, ev.charactersIgnoringModifiers == "v",
                      focused, NSPasteboard.general.string(forType: .string) == nil,
                      let a = ImageIntake.fromPasteboard() else { return ev }
                desk.attach(a)
                return nil
            }
            #endif
        }
        #if os(macOS)
        .onDisappear { if let m = pasteMonitor { NSEvent.removeMonitor(m); pasteMonitor = nil } }
        #endif
    }
}
