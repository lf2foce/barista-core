import SwiftUI

// THẺ KHỐI (kind "card") — server ghép trang từ khối có kiểu (xem backend/src/barista/kernel/ui.py), app chỉ vẽ. Khối lạ ⇒ bỏ qua.

struct CardPage: Decodable {
    struct Open: Decodable, Hashable { let title: String?; let query: String; let input: [String: JSON] }  // list item: không có title
    struct Step: Decodable, Hashable { let label, state: String; let at: JSON?; let note: String?; let owner: String? }
    struct Item: Decodable, Hashable {
        let label, status: String
        let required, hidden: Bool
        let value, note: String?
        var id: String { label }
    }
    struct Stage: Decodable, Hashable { let key, name, owner, state: String; let items: [Item] }
    /// Ô trong lưới thẻ (chợ gói…): ``module`` ⇒ biểu tượng + màu theo phân hệ (ModuleStyle), ``tone`` ⇒ màu nhãn trạng thái.
    struct Tile: Decodable, Hashable {
        let title: String
        let subtitle, badge, tone, module: String?
        let open: Open?
    }
    struct ListItem: Decodable, Hashable {
        let title: String
        let subtitle, tone, badge: String?
        let open: Open?
        let ask: String?
        /// Dòng là một tệp trong trao đổi (tab "Tệp" — 0128): mã + loại để vẽ ảnh thu nhỏ qua đường tải tệp sẵn có. Trường thêm, thường không có.
        var file: ThreadBlock.File? = nil
    }
    /// Bảng kanban: cột → thẻ. ``move`` có ⇒ kéo thẻ sang cột = chạy lệnh đó (server vẫn kiểm quyền / luật); cột trong ``form_columns``
    /// cần hỏi thêm (vd "Kẹt" cần lý do) ⇒ mở form điền sẵn thay vì chạy luôn. ``move`` trống ⇒ thẻ chỉ chuyển cột theo dữ kiện (hồ sơ).
    struct Board: Decodable, Hashable {
        struct Card: Decodable, Hashable, Identifiable { let id, title: String; let subtitle, tone, badge: String?; let open: Open? }
        struct Column: Decodable, Hashable, Identifiable { let key, title: String; let subtitle, tone: String?; let items: [Card]; var id: String { key } }
        struct Move: Decodable, Hashable { let command, id_key, column_key: String; let form_columns: [String]? }
        let columns: [Column]
        let move: Move?
    }
    enum Block: Decodable {
        case kpis([KPI])
        case steps(title: String, steps: [Step])
        case stages(title: String, stages: [Stage])
        case callout(tone: String, title: String, text: String, facts: [String], draft: String, actions: [ActionSpec])
        case list(title: String, items: [ListItem])
        case tiles(title: String, items: [Tile])
        case text(title: String, markdown: String)
        case table(TablePage)
        case board(title: String, board: Board)
        case thread(ThreadBlock)
        case chart(ChartBlock)
        case timeline(TimelineData)   // dòng thời gian (0222): Mac / iPhone vẽ danh sách theo nhóm có ngày
        case finding(FindingBlock)
        case choices(Choices)
        /// Khối TẢI SAU (``ui.b_later`` / ``ui.part``): chỗ chờ — ``LaterLoader`` tải rồi thay bằng các khối thật theo thứ tự trong thẻ.
        case later(Later)
        /// Ảnh trong thẻ (``ui.b_image``, api 1.73): ảnh nguồn (phiếu chụp…) — bấm ⇒ xem to.
        case image(CardImage)
        /// Chia hai cột (``ui.b_split``, api 1.73): mỗi cột là các khối bất kỳ (lồng được); khung hẹp ⇒ xếp dọc, cột trái trước.
        case split(ratio: String, left: [Block], right: [Block])
        /// Lịch (0240): lưới giờ / danh sách theo ngày (CalendarBlock.swift).
        case calendar(CalendarData)
        /// "Soạn với AI" (thử nghiệm): trình soạn chỉ có trên web — app không vẽ, thẻ server gửi kèm bản chữ thay thế ngay sau khối này
        case composer
        case unknown

        private enum K: String, CodingKey { case type, items, title, steps, stages, tone, text, facts, draft, actions, markdown, page, left, right, ratio }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: K.self)
            switch try c.decode(String.self, forKey: .type) {
            case "kpis": self = .kpis(try c.decode([KPI].self, forKey: .items))
            case "steps": self = .steps(title: try c.decodeIfPresent(String.self, forKey: .title) ?? "", steps: try c.decode([Step].self, forKey: .steps))
            case "stages": self = .stages(title: try c.decodeIfPresent(String.self, forKey: .title) ?? "", stages: try c.decode([Stage].self, forKey: .stages))
            case "callout":
                self = .callout(tone: try c.decodeIfPresent(String.self, forKey: .tone) ?? "muted", title: try c.decode(String.self, forKey: .title),
                                text: try c.decodeIfPresent(String.self, forKey: .text) ?? "", facts: try c.decodeIfPresent([String].self, forKey: .facts) ?? [],
                                draft: try c.decodeIfPresent(String.self, forKey: .draft) ?? "", actions: try c.decodeIfPresent([ActionSpec].self, forKey: .actions) ?? [])
            case "list": self = .list(title: try c.decodeIfPresent(String.self, forKey: .title) ?? "", items: try c.decode([ListItem].self, forKey: .items))
            case "tiles": self = .tiles(title: try c.decodeIfPresent(String.self, forKey: .title) ?? "", items: try c.decode([Tile].self, forKey: .items))
            case "text": self = .text(title: try c.decodeIfPresent(String.self, forKey: .title) ?? "", markdown: try c.decode(String.self, forKey: .markdown))
            case "table": self = .table(try c.decode(TablePage.self, forKey: .page))
            case "board": self = .board(title: try c.decodeIfPresent(String.self, forKey: .title) ?? "", board: try Board(from: decoder))
            case "thread": self = .thread(try ThreadBlock(from: decoder))
            case "chart": self = .chart(try ChartBlock(from: decoder))
            case "timeline": self = .timeline(try TimelineData(from: decoder))
            case "finding": self = .finding(try FindingBlock(from: decoder))
            case "choices": self = .choices(try Choices(from: decoder))
            case "later": self = .later(try Later(from: decoder))
            case "image": self = .image(try CardImage(from: decoder))
            case "calendar": self = .calendar(try CalendarData(from: decoder))
            case "split":
                // cột giải mã đệ quy, bỏ khối lạ; tỉ lệ sai dạng ⇒ 1:1 (cùng luật với web)
                let ratio = try c.decodeIfPresent(String.self, forKey: .ratio) ?? ""
                let known: ([Block]) -> [Block] = { $0.filter { if case .unknown = $0 { return false }; return true } }
                self = .split(ratio: CardImage.validRatio(ratio) ? ratio : "1:1",
                              left: known(try c.decodeIfPresent([Block].self, forKey: .left) ?? []),
                              right: known(try c.decodeIfPresent([Block].self, forKey: .right) ?? []))
            case "composer": self = .composer
            default: self = .unknown
            }
        }
    }

    struct Later: Decodable, Hashable {
        let query: String
        var input: [String: JSON] = [:]
        var part: String? = nil
        var title: String = ""
        private enum K: String, CodingKey { case query, input, part, title }
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: K.self)
            query = try c.decode(String.self, forKey: .query)
            input = (try? c.decodeIfPresent([String: JSON].self, forKey: .input)) ?? [:]
            part = (try? c.decodeIfPresent(String.self, forKey: .part)).flatMap { $0?.isEmpty == false ? $0 : nil }
            title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        }
    }

    let title, subtitle, icon: String
    var blocks: [Block]
    /// Bề rộng ``w`` (1–12, 0166) theo vị trí khối — cùng độ dài ``blocks``; nil = cả hàng.
    var widths: [Int?] = []
    let actions: [ActionSpec]
    let open: [Open]
    let ask: DetailPage.Ask?
    let compact: Compact?   // thẻ gọn (0094): bản server đã cắt
    /// Thẻ nhiều trang (app tự ghép — 0098): bấm tab = chạy lại truy vấn của màn với ``param`` = key.
    let tabs: Tabs?
    /// "Sửa khối này" theo vị trí khối (app tự ghép — 0098) — đọc riêng từ cùng mảng khối, khối không có thì không có.
    var edits: [Int: ActionSpec] = [:]

    private enum CK: String, CodingKey { case title, subtitle, icon, blocks, actions, open, ask, compact, tabs }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CK.self)
        title = try c.decode(String.self, forKey: .title)
        subtitle = try c.decode(String.self, forKey: .subtitle)
        icon = try c.decode(String.self, forKey: .icon)
        blocks = try c.decode([Block].self, forKey: .blocks)
        actions = try c.decode([ActionSpec].self, forKey: .actions)
        open = try c.decode([Open].self, forKey: .open)
        ask = try c.decodeIfPresent(DetailPage.Ask.self, forKey: .ask)
        compact = try c.decodeIfPresent(Compact.self, forKey: .compact)
        tabs = try? c.decodeIfPresent(Tabs.self, forKey: .tabs)
        if let raw = try? c.decode([OptionalEdit].self, forKey: .blocks) {
            for (i, e) in raw.enumerated() { if let a = e.edit { edits[i] = a } }
            widths = raw.map { $0.w.map { min(max($0, 1), CardGrid.columns) } }
        }
        if widths.count != blocks.count { widths = blocks.map { _ in nil } }
    }
    private struct OptionalEdit: Decodable {
        let edit: ActionSpec?
        let w: Int?
        init(from decoder: Decoder) throws {
            let c = try? decoder.container(keyedBy: K.self)
            edit = try? c?.decodeIfPresent(ActionSpec.self, forKey: .edit)
            w = (try? c?.decodeIfPresent(Int.self, forKey: .w)) ?? nil
        }
        private enum K: String, CodingKey { case edit, w }
    }

    /// Thay khối ``index`` (khối tải sau) bằng các khối của trang con — bề rộng / nút sửa của khối sau dời theo.
    func replacing(_ index: Int, with sub: CardPage?) -> CardPage {
        var out = self
        let add = sub?.blocks ?? []
        let addW = sub.map { $0.widths.count == add.count ? $0.widths : add.map { _ in nil } } ?? []
        out.blocks.replaceSubrange(index...index, with: add)
        out.widths.replaceSubrange(index...index, with: addW)
        var e: [Int: ActionSpec] = [:]
        for (k, a) in edits where k != index { e[k < index ? k : k + add.count - 1] = a }
        for (k, a) in sub?.edits ?? [:] { e[index + k] = a }
        out.edits = e
        return out
    }

    struct Tabs: Decodable, Hashable {
        struct Item: Decodable, Hashable, Identifiable { let key, title: String; var id: String { key } }
        let param: String
        let items: [Item]
        let current: String
    }

    /// Câu hỏi TRƯỚC khi làm (0098): chọn đáp án từng câu rồi gửi MỘT tin nhắn vào chat.
    struct Choices: Decodable, Hashable {
        struct Question: Decodable, Hashable, Identifiable { let key, question: String; let options: [String]; let multi: Bool?; var id: String { key } }
        let title: String?
        let questions: [Question]
        let submit: String?
        let prefix: String?
    }
}

extension Color {
    /// Tông của khối thẻ: ok · warn · bad · accent · muted (khác tông của ô bảng).
    static func card(_ name: String?) -> Color {
        switch name {
        // nhận cả bộ tông của bảng (green / orange / red / blue) — khối gửi "red" / "orange" trước đây thành xám (cùng web `cardToneVar`)
        case "ok", "green": .ok
        case "warn", "orange": .warn
        case "bad", "red": .bad
        case "accent", "blue": .accent
        default: .secondary
        }
    }
}

struct CardView: View {
    let page: CardPage
    var onClose: (() -> Void)? = nil
    /// Trang chứa thẻ tải lại sau khi một thao tác trên thẻ chạy xong (kéo thẻ, bấm nút) — trống ⇒ thẻ tĩnh (bản chụp trong chat).
    var onChanged: (() -> Void)? = nil
    /// Thẻ đang nằm trong cửa sổ con (``OpenSheet``): bấm mở thứ khác ⇒ chuyển nội dung NGAY TRONG cửa sổ đó (có "Quay lại"), không chồng thêm
    /// cửa sổ — chồng cửa sổ giống hệt nhau làm nút "Đóng" tưởng như không chạy. Trống ⇒ mở cửa sổ con như cũ.
    var onOpen: ((OpenRequest) -> Void)? = nil
    /// Màn chứa thẻ đã đưa phụ đề + nút hỏi trợ lý lên ĐẦU THẺ (rà bố cục 18/11) ⇒ thân thẻ không vẽ lại hai thứ đó.
    var inHead = false
    /// Lần tải của màn chứa thẻ (tăng mỗi lần tải lại) ⇒ khối tải sau tải lại theo bản mới.
    var rev = 0
    @Environment(Desk.self) private var desk: Desk?
    @State private var action: ActionSpec?
    @State private var rowForm: FormRequest?
    @State private var opening: OpenRequest?
    @State private var done: String?
    @State private var dashboardScrolling = false
    /// Khối tải sau đã về: vị trí khối ``later`` → trang con (nil = trống) hoặc giờ lỗi.
    @State private var got: [Int: Result<CardPage?, LaterFailure>] = [:]
    @State private var gotKey = ""

    struct LaterFailure: Error { let at: Date }

    private enum Segment { case one(CardPage.Block), charts([(Int, ChartBlock)]), solo(ChartBlock), row([Int]) }

    private var laterKey: String {
        "\(rev)|" + page.blocks.enumerated().compactMap { i, b in
            if case .later(let l) = b { "\(i):\(l.query):\(l.part ?? ""):\(l.input.keys.sorted().map { "\($0)=\(l.input[$0]?.text ?? "")" }.joined(separator: "&"))" } else { nil }
        }.joined(separator: ";")
    }

    /// Thẻ đang vẽ: khối tải sau đã về thay vào chỗ của nó (lỗi ⇒ một dòng báo tông warn kèm giờ lỗi, phần còn lại của thẻ vẫn dùng được).
    private var shown: CardPage {
        guard gotKey == laterKey, !got.isEmpty else { return page }
        var out = page
        for i in got.keys.sorted(by: >) {
            guard i < out.blocks.count, case .later(let l) = out.blocks[i] else { continue }
            switch got[i]! {
            case .success(let sub): out = out.replacing(i, with: sub)
            case .failure(let f):
                out.blocks[i] = .callout(tone: "warn", title: l.title.isEmpty ? "Phần này chưa tải được" : l.title,
                                         text: "Tạm thời chưa tải được phần này (lúc \(Fmt.time(f.at))) — bấm Tải lại sau ít phút.", facts: [], draft: "", actions: [])
            }
        }
        return out
    }

    /// Bố cục theo lưới 12 cột (0166, cùng web ``card.tsx::segments``): khối có ``w`` liền nhau cùng một hàng; biểu đồ KHÔNG có ``w`` liền nhau gộp
    /// thành lưới 1–2 cột; biểu đồ có ``w`` đứng một mình chiếm trọn hàng; khối khác giữ nguyên chỗ.
    private func segments(_ p: CardPage) -> [(Int, Segment)] {
        var out: [(Int, Segment)] = []
        for r in CardGrid.rows(p.widths) {
            if r.count > 1 { out.append((r[0], .row(r))); continue }
            let i = r[0]
            if case .chart(let c) = p.blocks[i] {
                if p.widths[i] != nil { out.append((i, .solo(c))) }
                else if let last = out.last, case .charts(let cs) = last.1 { out[out.count - 1].1 = .charts(cs + [(i, c)]) }
                else { out.append((i, .charts([(i, c)]))) }
            } else {
                out.append((i, .one(p.blocks[i])))
            }
        }
        return out
    }

    var body: some View {
        let p = shown
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if !p.subtitle.isEmpty && !inHead { Text(p.subtitle).font(.t14).foregroundStyle(.secondary).textSelection(.enabled) }
                toolbar
                ForEach(segments(p), id: \.0) { i, seg in segmentView(p, i, seg) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .environment(\.dashboardIsScrolling, dashboardScrolling)
        }
        .onScrollPhaseChange { _, phase in
            if dashboardScrolling != phase.isScrolling { dashboardScrolling = phase.isScrolling }
        }
        .task(id: laterKey) { await loadLater() }
        .sheet(item: $action) { a in
            if let desk { FormSheet(api: desk.api, action: a, row: nil, initial: a.preset) { done = $0; onChanged?() } }
        }
        .sheet(item: $rowForm) { req in
            if let desk { FormSheet(api: desk.api, action: req.action, row: req.row) { done = $0; onChanged?() } }
        }
        .sheet(item: $opening) { req in
            if let desk { OpenSheet(api: desk.api, request: req) }
        }
    }

    /// Khối tải sau (ui.b_later / ui.part): tải LẦN LƯỢT theo thứ tự khối — thứ tự là ưu tiên do server khai; không bắn song song để phần nặng
    /// không giành máy chủ. Thẻ mới (lọc lại / tải lại) ⇒ bỏ kết quả cũ, tải lại.
    private func loadLater() async {
        let key = laterKey
        if gotKey != key { got = [:]; gotKey = key }
        guard let desk else { return }
        for (i, b) in page.blocks.enumerated() {
            guard case .later(let l) = b, got[i] == nil else { continue }
            let r: Result<CardPage?, LaterFailure>
            do {
                let sub = l.part.map { part in { try await desk.api.part(l.query, l.input, part: part) } } ?? { try await desk.api.query(l.query, l.input) }
                if case .card(let c) = try await sub() { r = .success(c) } else { r = .success(nil) }
            } catch {
                if Task.isCancelled { return }
                r = .failure(LaterFailure(at: .now))
            }
            guard !Task.isCancelled, gotKey == key else { return }
            got[i] = r
        }
    }

    @ViewBuilder private func segmentView(_ p: CardPage, _ i: Int, _ seg: Segment) -> some View {
        switch seg {
        case .charts(let cs):
            ChartGrid(blocks: cs.map(\.1)).contextMenu { if !p.edits.isEmpty {   // app tự ghép (0098): chuột phải ⇒ sửa đúng khối này
                ForEach(Array(cs.enumerated()), id: \.offset) { j, ch in
                    if let e = p.edits[ch.0] { Button("\(e.title): \(ch.1.title.isEmpty ? "biểu đồ \(j + 1)" : ch.1.title)") { action = e } }
                }
            } }
        case .solo(let c): editable(p, i) { ChartCard(block: c).equatable() }
        case .one(let b): editable(p, i) { block(b) }
        case .row(let cells):
            if cells.allSatisfy({ if case .kpis = p.blocks[$0] { true } else { false } }) {
                // hàng chỉ gồm ô số: chia đều, hẹp thì xuống hàng (ít nhất hai cột) — KHÔNG cắt số / nhãn
                KPIFlow(spacing: 10) {
                    ForEach(cells, id: \.self) { k in
                        if case .kpis(let ks) = p.blocks[k] { ForEach(ks.filter(\.shown), id: \.label) { kpi in KPITile(k: kpi, inset: true, onEdit: { action = $0 }) } }
                    }
                }
            } else {
                SpanGrid(spans: cells.map { p.widths[$0] ?? CardGrid.columns }, spacing: 12) {
                    ForEach(cells, id: \.self) { k in editable(p, k) { cell(p.blocks[k]) } }
                }
            }
        }
    }

    @ViewBuilder private func editable<C: View>(_ p: CardPage, _ i: Int, @ViewBuilder _ content: () -> C) -> some View {
        if let e = p.edits[i] { content().contextMenu { Button(e.title) { action = e } } } else { content() }
    }

    /// Một ô trong hàng lưới: biểu đồ / bảng có khung + tiêu đề trong khung, cao bằng hàng; khối khác vẽ như thường.
    @ViewBuilder private func cell(_ b: CardPage.Block) -> some View {
        switch b {
        case .chart(let c): ChartCard(block: c).equatable()
        case .table(let t):
            VStack(alignment: .leading, spacing: 10) {
                if !t.title.isEmpty { Text(t.title).font(.t14m) }
                tableBody(t, minHeight: 240)
            }
            .padding(16)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.primary.opacity(0.08)))
        case .kpis(let k): KPIRow(kpis: k, inset: true, onEdit: { action = $0 })
        default: block(b).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
    }

    /// Bảng trong thẻ: cao theo số dòng (≥ ``minHeight``, ≤ 420) — bảng dài cuộn bên trong; ghi chú gấp một dòng.
    @ViewBuilder private func tableBody(_ t: TablePage, minHeight: CGFloat = 200) -> some View {
        if !t.note.isEmpty { ClampNote(text: t.note) }
        if t.rows.isEmpty {
            Text(t.empty).font(.t14).foregroundStyle(.secondary).padding(12)
        } else {
            // bảng của app tự ghép (0098) mang nút dòng + mở chi tiết: chuột phải / nhấn giữ một dòng ⇒ nút; nhấp đúp ⇒ mở chi tiết
            DataTable(page: t, selection: .constant([]), menu: { row in
                ForEach(t.rowActions.filter { $0.applies(to: row) }) { a in Button(a.title) { rowForm = FormRequest(action: a, row: row) } }
                if let o = t.openRow { Divider(); Button(o.title.isEmpty ? "Mở" : o.title) { openRow(o, row) } }
            }, open: { row in if let o = t.openRow { openRow(o, row) } })
            #if os(macOS)
            .frame(height: min(420, max(minHeight, CGFloat(t.rows.count) * 26 + 34)))
            .cardSurface(radius: 10)
            #else
            .frame(minHeight: 200)
            #endif
        }
    }

    private func open(_ req: OpenRequest) {
        if let onOpen { onOpen(req) } else { opening = req }
    }

    private func openRow(_ o: OpenSpec, _ row: Row) {
        var input: Input = [:]
        for (field, col) in o.bind { input[field] = row.values[col] ?? .null }
        open(OpenRequest(title: o.title, query: o.query, input: input))
    }

    @ViewBuilder private var toolbar: some View {
        if desk != nil, (page.ask != nil && !inHead) || !page.actions.isEmpty || !page.open.isEmpty || done != nil {
            FlowRow(spacing: 8) {
                if let ask = page.ask, let desk, !inHead {
                    Button { if ask.mode == "builder" { desk.openBuilder(prompt: ask.prompt, appId: ask.app_id) } else { desk.ask(ask.prompt) }; onClose?() } label: { Label(ask.title, systemImage: "sparkles") }.buttonStyle(.borderedProminent)
                }
                ForEach(page.actions) { a in actionButton(a) }
                ForEach(Array(page.open.enumerated()), id: \.offset) { _, o in
                    Button(o.title ?? o.query) { open(OpenRequest(title: o.title ?? o.query, query: o.query, input: o.input)) }.buttonStyle(.bordered)
                }
                if let done { Label(done, systemImage: "checkmark.seal.fill").font(.t12m).foregroundStyle(Color.ok) }
            }
            .controlSize(.regular)
        }
    }

    @ViewBuilder private func actionButton(_ a: ActionSpec) -> some View {
        switch a.style {
        case "primary": Button(a.title) { action = a }.buttonStyle(.borderedProminent)
        case "destructive": Button(a.title, role: .destructive) { action = a }.buttonStyle(.bordered)
        default: Button(a.title) { action = a }.buttonStyle(.bordered)
        }
    }

    @ViewBuilder private func block(_ b: CardPage.Block) -> some View {
        switch b {
        case .kpis(let k): KPIRow(kpis: k, inset: true, onEdit: { action = $0 })
        case .steps(let title, let steps): section(title) { StepStrip(steps: steps) }
        case .stages(let title, let stages): section(title) { StageList(stages: stages) }
        case .callout(let tone, let title, let text, let facts, let draft, let actions):
            Callout(tone: tone, title: title, text: text, facts: facts, draft: draft, actions: actions) { action = $0 }
        case .list(let title, let items):
            section(title) {
                VStack(spacing: 0) {
                    ForEach(Array(items.enumerated()), id: \.offset) { i, it in
                        if i > 0 { Divider() }
                        ListRow(item: it) { o in open(OpenRequest(title: o.query, query: o.query, input: o.input)) }
                    }
                }
                .padding(.horizontal, 12)
                .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.surface2))
            }
        case .tiles(let title, let items):
            section(title) {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 230), spacing: 12, alignment: .top)], alignment: .leading, spacing: 12) {
                    ForEach(Array(items.enumerated()), id: \.offset) { _, t in
                        TileView(tile: t) { o in open(OpenRequest(title: t.title, query: o.query, input: o.input)) }
                    }
                }
            }
        case .text(let title, let md): section(title) { MarkdownText(text: md) }
        case .table(let t):
            section(t.title) { tableBody(t) }
        case .choices(let q): section(q.title ?? "") { ChoicesView(block: q) { text in desk?.send(text) } }
        case .board(let title, let board):
            section(title) {
                BoardView(board: board, onMove: { card, col in await move(card, to: col, in: board) },
                          onOpen: { o in open(OpenRequest(title: o.title ?? o.query, query: o.query, input: o.input)) })
            }
        case .thread(let t):
            if desk?.talkOpenSubject == t.subject_id {
                Label("Trao đổi về hồ sơ này đang mở bên cạnh", systemImage: "bubble.left.and.bubble.right")
                    .font(.t12).foregroundStyle(.secondary)
                    .padding(.horizontal, 12).padding(.vertical, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.surface2))
            } else {
                ThreadView(block: t, open: { open($0) })
            }
        case .chart(let c): ChartCard(block: c).equatable()
        case .later(let l):   // chỗ chờ — cùng khung với khối thật để thẻ không nhảy bố cục nhiều khi khối về
            section(l.title) {
                HStack(spacing: 8) { ProgressView().controlSize(.small); Text("Đang tải…").font(.t12).foregroundStyle(.secondary) }
                    .frame(maxWidth: .infinity, minHeight: 96)
                    .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface2))
            }
        case .timeline(let t):
            section(t.title) { TimelineList(data: t) { o in open(OpenRequest(title: o.title ?? o.query, query: o.query, input: o.input)) } }
        case .finding(let f): FindingView(block: f) { action = $0 }
        case .image(let i): CardImageView(block: i)
        case .calendar(let c):
            CalendarBlockView(block: c, onOpen: { o in open(OpenRequest(title: o.title ?? o.query, query: o.query, input: o.input)) }, onCreate: { action = $0 })
        case .split(let ratio, let left, let right):
            // cột lồng khối bất kỳ (kể cả split) ⇒ gọi lại chính hàm này; AnyView cắt vòng kiểu trả về
            SplitBlockView(ratio: ratio) {
                ForEach(Array(left.enumerated()), id: \.offset) { _, x in AnyView(block(x)) }
            } right: {
                ForEach(Array(right.enumerated()), id: \.offset) { _, x in AnyView(block(x)) }
            }
        case .composer, .unknown: EmptyView()
        }
    }

    /// Kéo thẻ sang cột: chạy lệnh của bảng (hoặc mở form khi cột cần hỏi thêm). Trả false ⇒ bảng trả thẻ về chỗ cũ.
    private func move(_ card: CardPage.Board.Card, to column: String, in board: CardPage.Board) async -> Bool {
        guard let mv = board.move, let desk else { return false }
        let input: Input = [mv.id_key: .string(card.id), mv.column_key: .string(column)]
        if mv.form_columns?.contains(column) == true {
            let title = board.columns.first { $0.key == column }?.title ?? column
            action = ActionSpec(command: mv.command, title: "\(card.title) → \(title)", style: "primary", bind: [:], preset: input, when: [:],
                                importable: false)
            return false
        }
        do {
            let r = try await desk.api.command(mv.command, input, key: UUID().uuidString)
            guard r.status == "ok" else {
                desk.show(r.error.isEmpty ? "Không đổi được (\(r.status))" : r.error, isError: true)
                return false
            }
            onChanged?()
            return true
        } catch {
            desk.show(error.localizedDescription, isError: true)
            return false
        }
    }

    @ViewBuilder private func section<C: View>(_ title: String, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if !title.isEmpty { Text(title).font(.t16) }
            content()
        }
    }
}

/// Hành trình: các mốc nằm ngang — xong (✓ xanh), hiện tại (chấm xanh), dừng (✕ đỏ), chưa tới (vòng xám).
struct StepStrip: View {
    let steps: [CardPage.Step]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: 0) {
                ForEach(Array(steps.enumerated()), id: \.offset) { i, s in
                    VStack(spacing: 6) {
                        HStack(spacing: 0) {
                            Rectangle().fill(i == 0 ? Color.clear : line(steps[i - 1].state)).frame(height: 2)
                            icon(s.state).frame(width: 24, height: 24)
                            Rectangle().fill(i == steps.count - 1 ? Color.clear : line(s.state)).frame(height: 2)
                        }
                        Text(s.label).font(.t12m).foregroundStyle(s.state == "todo" ? .secondary : .primary).lineLimit(1)
                        if let at = s.at, case .string(let str) = at { Text(Fmt.date(str)).font(.t12).foregroundStyle(.secondary) }
                        if let n = s.note, !n.isEmpty { Text(n).font(.t12).foregroundStyle(s.state == "stopped" ? Color.bad : .secondary).lineLimit(2).multilineTextAlignment(.center) }
                        if let o = s.owner, !o.isEmpty, s.state != "done" { Text(o).font(.t12).foregroundStyle(.tertiary) }
                    }
                    .frame(width: 104)
                }
            }
            .padding(.vertical, 4)
        }
    }

    private func line(_ prev: String) -> Color { prev == "done" ? Color.ok.opacity(0.6) : Color.primary.opacity(0.12) }

    @ViewBuilder private func icon(_ state: String) -> some View {
        switch state {
        case "done": Image(systemName: "checkmark.circle.fill").font(.system(size: 20)).foregroundStyle(Color.ok)
        case "current": Image(systemName: "circle.inset.filled").font(.system(size: 20)).foregroundStyle(Color.accent)
        case "stopped": Image(systemName: "xmark.circle.fill").font(.system(size: 20)).foregroundStyle(Color.bad)
        default: Image(systemName: "circle").font(.system(size: 20)).foregroundStyle(Color.primary.opacity(0.25))
        }
    }
}

/// Hồ sơ theo chặng: mỗi chặng một khối (ai giữ, mục cần đủ). Mục tiền công ty người xem không có quyền ⇒ chỉ thấy trạng thái.
struct StageList: View {
    let stages: [CardPage.Stage]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 240), spacing: 10, alignment: .top)], spacing: 10) {
            ForEach(stages, id: \.key) { st in
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 6) {
                        icon(st.state)
                        Text(st.name).font(.t14m).lineLimit(1)
                    }
                    Text(st.owner).font(.t12).foregroundStyle(.secondary)
                    ForEach(st.items, id: \.label) { it in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(alignment: .top, spacing: 6) {
                                Image(systemName: symbol(it.status)).font(.system(size: 12)).foregroundStyle(tint(it.status)).padding(.top, 2)
                                Text(it.label).font(.t12).foregroundStyle(it.status == "missing" ? .secondary : .primary)
                            }
                            if it.hidden {
                                Label("chỉ đội phụ trách thấy số", systemImage: "lock.fill").font(.t12).foregroundStyle(.tertiary).padding(.leading, 18)
                            } else {
                                if let v = it.value, !v.isEmpty { Text(v).font(.t12).monospacedDigit().foregroundStyle(.secondary).padding(.leading, 18) }
                                if let n = it.note, !n.isEmpty { Text(n).font(.t12).foregroundStyle(it.status == "rejected" ? Color.bad : .secondary).padding(.leading, 18) }
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(st.state == "current" ? Color.accent.opacity(0.10) : Color.surface2))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(st.state == "current" ? Color.accent.opacity(0.5) : .clear, lineWidth: 1))
            }
        }
    }

    @ViewBuilder private func icon(_ s: String) -> some View {
        switch s {
        case "done": Image(systemName: "checkmark.circle.fill").foregroundStyle(Color.ok)
        case "current": Image(systemName: "circle.inset.filled").foregroundStyle(Color.accent)
        case "stopped": Image(systemName: "xmark.circle.fill").foregroundStyle(Color.bad)
        default: Image(systemName: "circle").foregroundStyle(Color.primary.opacity(0.3))
        }
    }

    private func symbol(_ s: String) -> String {
        switch s { case "verified": "checkmark.circle.fill"; case "received": "clock.fill"; case "rejected": "xmark.circle.fill"; default: "circle" }
    }

    private func tint(_ s: String) -> Color {
        switch s { case "verified": .ok; case "received": .warn; case "rejected": .bad; default: Color.primary.opacity(0.3) }
    }
}

/// Khung lưu ý: tông màu + lý do + dữ kiện + (tuỳ chọn) tin nhắn soạn sẵn để sale tự gửi + nút thao tác.
struct Callout: View {
    let tone: String
    let title, text: String
    let facts: [String]
    let draft: String
    let actions: [ActionSpec]
    let run: (ActionSpec) -> Void

    private var titleColor: Color { tone == "muted" ? Color.primary : Color.card(tone) }
    private var fill: Color { Color.card(tone).opacity(tone == "muted" ? 0.06 : 0.09) }
    /// Ghi chú nhạt (muted, không nút / tin soạn sẵn): gấp còn tiêu đề + một dòng, "Xem thêm" mở — không đẩy số xuống (rà bố cục 18/11).
    /// Cảnh báo thật (warn / bad), callout có nút / tin soạn sẵn: luôn mở.
    private var quiet: Bool { (tone == "muted" || tone.isEmpty) && actions.isEmpty && draft.isEmpty && !(text.isEmpty && facts.isEmpty) }
    @State private var open = false

    var body: some View {
        if quiet && !open {
            VStack(alignment: .leading, spacing: 2) {
                if !title.isEmpty { Text(title).font(.t14m) }
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    let line = ([text] + facts).filter { !$0.isEmpty }.joined(separator: " · ")
                    Text(line).font(.t12).foregroundStyle(.secondary).lineLimit(1).truncationMode(.tail).help(line)
                    Spacer(minLength: 0)
                    Button("Xem thêm") { open = true }.buttonStyle(.plain).foregroundStyle(Color.accent).font(.t12m).fixedSize()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(fill))
        } else {
            full
        }
    }

    private var full: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.t14m).foregroundStyle(titleColor)
            if !text.isEmpty { Text(text).font(.t14).textSelection(.enabled) }
            ForEach(facts, id: \.self) { f in factRow(f) }
            if !draft.isEmpty { draftBox }
            if !actions.isEmpty || !draft.isEmpty { buttons }
            if quiet { Button("Thu gọn") { open = false }.buttonStyle(.plain).foregroundStyle(Color.accent).font(.t12m) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(fill))
    }

    private func factRow(_ f: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text("•").foregroundStyle(.secondary)
            Text(f).font(.t12).foregroundStyle(.secondary)
        }
    }

    private var draftBox: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Tin nhắn gợi ý — anh/chị tự gửi").font(.t12).foregroundStyle(.secondary)
            Text(draft).font(.t14).textSelection(.enabled)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Color.surface))
        }
    }

    private var buttons: some View {
        HStack(spacing: 8) {
            ForEach(actions) { a in actionButton(a) }
            if !draft.isEmpty {
                Button(action: copyDraft) { Label("Sao chép tin", systemImage: "doc.on.doc") }.buttonStyle(.bordered)
            }
        }
        .controlSize(.regular)
    }

    @ViewBuilder private func actionButton(_ a: ActionSpec) -> some View {
        if a.style == "primary" {
            Button(a.title) { run(a) }.buttonStyle(.borderedProminent)
        } else {
            Button(a.title) { run(a) }.buttonStyle(.bordered)
        }
    }

    private func copyDraft() {
        copyToPasteboard(draft)
    }
}

struct TileView: View {
    let tile: CardPage.Tile
    let open: (CardPage.Open) -> Void

    var body: some View {
        let style = ModuleStyle.of(tile.module ?? "")
        Button { if let o = tile.open { open(o) } } label: {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top) {
                    IconTile(symbol: style.symbol, tint: style.tint, size: 34)
                    Spacer(minLength: 8)
                    if let b = tile.badge, !b.isEmpty {
                        Text(b).font(.t12m).foregroundStyle(Color.card(tile.tone)).lineLimit(1)
                            .padding(.horizontal, 8).padding(.vertical, 3)
                            .background(Capsule().fill(Color.card(tile.tone).opacity(0.12)))
                    }
                }
                Text(tile.title).font(.t16).multilineTextAlignment(.leading)
                if let s = tile.subtitle, !s.isEmpty {
                    Text(s).font(.t12).foregroundStyle(.secondary).lineLimit(4).multilineTextAlignment(.leading)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, minHeight: 150, alignment: .topLeading)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.surface2))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(tile.open == nil)
    }
}

struct ListRow: View {
    let item: CardPage.ListItem
    let open: (CardPage.Open) -> Void
    @Environment(Desk.self) private var desk: Desk?

    private var content: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(Color.card(item.tone)).frame(width: 7, height: 7).padding(.top, 6)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.t14)
                if let s = item.subtitle, !s.isEmpty { Text(s).font(.t12).foregroundStyle(.secondary) }
            }
            Spacer(minLength: 8)
            trailing
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
    }

    @ViewBuilder private var trailing: some View {
        if let b = item.badge, !b.isEmpty { Text(b).font(.t12).foregroundStyle(.secondary).monospacedDigit() }
        if item.open != nil || item.ask != nil {
            Image(systemName: "chevron.right").font(.system(size: 11)).foregroundStyle(.tertiary).padding(.top, 4)
        }
    }

    var body: some View {
        if let o = item.open {
            Button { open(o) } label: { content }.buttonStyle(.plain)
        } else if let q = item.ask, let desk {
            Button { desk.send(q) } label: { content }.buttonStyle(.plain)
        } else {
            content
        }
    }
}

/// Bảng kanban (việc theo trạng thái, hồ sơ theo chặng). Kéo thẻ (Mac: kéo chuột; iPhone: chạm giữ rồi kéo) hoặc menu chuột phải /
/// chạm giữ ⇒ "Chuyển sang…". Thẻ nhảy cột ngay (lạc quan), lệnh lỗi ⇒ về chỗ cũ kèm lý do server trả.
struct BoardView: View {
    let board: CardPage.Board
    let onMove: (CardPage.Board.Card, String) async -> Bool
    let onOpen: (CardPage.Open) -> Void
    @State private var moved: [String: String] = [:]      // thẻ → cột mới (chờ trang tải lại)
    @State private var target: String?

    private var canMove: Bool { board.move != nil }
    #if os(iOS)
    private let width: CGFloat = 272
    #else
    private let width: CGFloat = 248
    #endif

    private func items(_ col: CardPage.Board.Column) -> [CardPage.Board.Card] {
        let stay = col.items.filter { moved[$0.id] == nil || moved[$0.id] == col.key }
        let incoming = board.columns.filter { $0.key != col.key }.flatMap(\.items).filter { moved[$0.id] == col.key }
        return stay + incoming
    }

    private func column(of id: String) -> String? {
        moved[id] ?? board.columns.first { c in c.items.contains { $0.id == id } }?.key
    }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: true) {
            HStack(alignment: .top, spacing: 12) {
                ForEach(board.columns) { col in columnView(col) }
            }
            .padding(.bottom, 6)
        }
        .onChange(of: board) { moved = [:] }
    }

    private func columnView(_ col: CardPage.Board.Column) -> some View {
        let cards = items(col)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Circle().fill(Color.card(col.tone)).frame(width: 8, height: 8)
                Text(col.title).font(.t14m).lineLimit(1)
                Text("\(cards.count)").font(.t12).monospacedDigit().foregroundStyle(.secondary)
                Spacer(minLength: 0)
            }
            if let sub = col.subtitle, !sub.isEmpty { Text(sub).font(.t12).foregroundStyle(.secondary).lineLimit(1) }
            VStack(spacing: 8) {
                ForEach(cards) { card in cardView(card, in: col) }
                if cards.isEmpty {
                    Text(canMove ? "Kéo thẻ vào đây" : "Trống").font(.t12).foregroundStyle(.tertiary)
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
            }
        }
        .padding(10)
        .frame(width: width, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(target == col.key ? Color.accent.opacity(0.12) : Color.surface2))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(target == col.key ? Color.accent.opacity(0.6) : .clear, lineWidth: 1.5))
        .dropDestination(for: String.self) { ids, _ in
            guard canMove, let id = ids.first, column(of: id) != col.key,
                  let card = board.columns.flatMap(\.items).first(where: { $0.id == id }) else { return false }
            go(card, to: col.key)
            return true
        } isTargeted: { on in
            if on { target = col.key } else if target == col.key { target = nil }
        }
    }

    private func go(_ card: CardPage.Board.Card, to col: String) {
        let needsForm = board.move?.form_columns?.contains(col) == true
        if !needsForm { withAnimation(.snappy) { moved[card.id] = col } }
        Task {
            if !(await onMove(card, col)), !needsForm { withAnimation(.snappy) { moved[card.id] = nil } }
        }
    }

    @ViewBuilder private func cardView(_ card: CardPage.Board.Card, in col: CardPage.Board.Column) -> some View {
        let tile = HStack(alignment: .top, spacing: 8) {
            RoundedRectangle(cornerRadius: 2).fill(Color.card(card.tone)).frame(width: 3)
            VStack(alignment: .leading, spacing: 4) {
                Text(card.title).font(.t14).foregroundStyle(.primary).multilineTextAlignment(.leading).lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                if let s = card.subtitle, !s.isEmpty {
                    Text(s).font(.t12).foregroundStyle(.secondary).lineLimit(2).fixedSize(horizontal: false, vertical: true)
                }
                if let b = card.badge, !b.isEmpty {
                    Text(b).font(.t12m).foregroundStyle(Color.card(card.tone))
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Capsule().fill(Color.card(card.tone).opacity(0.12)))
                }
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.surface))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Color.primary.opacity(0.08), lineWidth: 1))
        .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .onTapGesture { if let o = card.open { onOpen(o) } }
        if canMove {
            tile
                .draggable(card.id) {
                    Text(card.title).font(.t14).padding(10).frame(width: width - 20, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.surface))
                }
                .contextMenu {
                    ForEach(board.columns.filter { $0.key != column(of: card.id) }) { c in
                        Button("Chuyển sang “\(c.title)”") { go(card, to: c.key) }
                    }
                    if let o = card.open { Divider(); Button("Mở") { onOpen(o) } }
                }
        } else {
            tile
        }
    }
}


/// Câu hỏi trước khi làm (0098, như Claude Design hỏi lại): chọn đáp án mỗi câu (+ ghi thêm) rồi gửi MỘT tin nhắn — trợ lý ghép tiếp theo lựa
/// chọn. Gửi rồi thì khoá lại.
struct ChoicesView: View {
    let block: CardPage.Choices
    let send: (String) -> Void
    @State private var picked: [String: [String]] = [:]
    @State private var note = ""
    @State private var sent = false

    private var ready: Bool { block.questions.allSatisfy { !(picked[$0.key] ?? []).isEmpty } }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(Array(block.questions.enumerated()), id: \.element.id) { i, q in
                VStack(alignment: .leading, spacing: 6) {
                    (Text("\(i + 1)/\(block.questions.count)  ").font(.t12m).foregroundStyle(.secondary)
                     + Text(q.question).font(.t14m)
                     + Text(q.multi == true ? " · chọn được nhiều" : "").font(.t12).foregroundStyle(.secondary))
                    ForEach(q.options, id: \.self) { o in
                        let on = (picked[q.key] ?? []).contains(o)
                        // thẻ lựa chọn (như hộp hỏi của Claude): cả hàng bấm được — tròn: chọn một · vuông: chọn nhiều
                        Button { toggle(q, o) } label: {
                            HStack(alignment: .top, spacing: 10) {
                                ZStack {
                                    if q.multi == true {
                                        RoundedRectangle(cornerRadius: 4).strokeBorder(on ? Color.accent : Color.secondary.opacity(0.5))
                                            .background(RoundedRectangle(cornerRadius: 4).fill(on ? Color.accent : .clear))
                                    } else {
                                        Circle().strokeBorder(on ? Color.accent : Color.secondary.opacity(0.5)).background(Circle().fill(on ? Color.accent : .clear))
                                    }
                                    if on { Image(systemName: "checkmark").font(.system(size: 9, weight: .bold)).foregroundStyle(.white) }
                                }
                                .frame(width: 16, height: 16).padding(.top, 2)
                                Text(o).font(.t14).multilineTextAlignment(.leading).frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .padding(.horizontal, 12).padding(.vertical, 10)
                            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(on ? Color.accent.opacity(0.08) : Color.surface))
                            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(on ? Color.accent : Color.primary.opacity(0.12)))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityAddTraits(on ? .isSelected : [])
                    }
                }
            }
            TextField("Ghi thêm (không bắt buộc)", text: $note).textFieldStyle(.roundedBorder)
            HStack(spacing: 10) {
                Button(sent ? "Đã gửi" : (block.submit ?? "Gửi")) { submit() }
                    .buttonStyle(.borderedProminent).disabled(!ready || sent)
                if !ready && !sent { Text("Chọn mỗi câu ít nhất một ý").font(.t12).foregroundStyle(.secondary) }
            }
        }
        .disabled(sent)
    }

    private func toggle(_ q: CardPage.Choices.Question, _ o: String) {
        let now = picked[q.key] ?? []
        picked[q.key] = q.multi == true ? (now.contains(o) ? now.filter { $0 != o } : now + [o]) : [o]
    }

    private func submit() {
        var lines = [block.prefix?.isEmpty == false ? block.prefix! : "Trả lời:"]
        lines += block.questions.map { "- \($0.question) \((picked[$0.key] ?? []).joined(separator: ", "))" }
        let extra = note.trimmingCharacters(in: .whitespacesAndNewlines)
        if !extra.isEmpty { lines.append("- Ghi thêm: \(extra)") }
        send(lines.joined(separator: "\n"))
        sent = true
    }
}
