import SwiftUI
import UniformTypeIdentifiers

struct FormRequest: Identifiable {
    let id = UUID()
    let action: ActionSpec
    let row: Row?
}

struct OpenRequest: Identifiable {
    let id = UUID()
    let title: String
    let query: String
    let input: Input

    /// Cùng truy vấn + cùng đầu vào ⇒ cùng một thứ (mở lại thứ đang xem thì bỏ qua).
    func same(_ other: OpenRequest) -> Bool { query == other.query && input == other.input }
}

/// Đầu thẻ của khung làm việc trao cho màn (rà bố cục 18/11): màn tự vẽ hàng tên thẻ để bộ lọc chính + nút hỏi trợ lý nằm CÙNG hàng tên thẻ.
struct ScreenHead {
    let title: String
    let symbol: String
    let tint: Color
    var subtitle = "Màn ghim · dữ liệu đúng quyền của bạn"
    let onClose: () -> Void
}

/// Một màn: bộ lọc + nút lệnh + KPI + bảng (hoặc trang chi tiết / thẻ). Mọi thứ theo `ScreenSpec` server gửi — cùng hành vi web screen-view.tsx.
struct ScreenView: View {
    let spec: ScreenSpec
    let api: API
    let onChanged: () -> Void
    @Environment(Desk.self) private var deskEnv: Desk?
    private let hasInitialPage: Bool
    private let embedded: Bool  // nằm trong thẻ lớn của khung làm việc
    private let head: ScreenHead?

    /// Tăng ⇒ tải lại TẠI CHỖ (giữ nội dung đang thấy) — trình tạo app vừa sửa xong một bước (0098).
    var refresh = 0

    init(spec: ScreenSpec, api: API, initialFilters: Input = [:], initialPage: Page? = nil, embedded: Bool = false, refresh: Int = 0,
         head: ScreenHead? = nil, onChanged: @escaping () -> Void = {}) {
        self.refresh = refresh
        self.spec = spec
        self.api = api
        self.embedded = embedded
        self.head = head
        self.onChanged = onChanged
        hasInitialPage = initialPage != nil
        _filters = State(initialValue: initialFilters.filter { !$0.value.isNull })
        _page = State(initialValue: initialPage)
    }

    /// Số dòng một trang của bảng (server cắt — ô số vẫn tính trên cả tập, Excel xuất cả tập). Cùng web ``PAGE_ROWS``.
    static let pageRows = 300

    @State private var page: Page?
    @State private var error: String?
    @State private var errorAt: Date?
    @State private var loading = false
    @State private var loadingMore = false
    @State private var rev = 0                 // lần tải — khối tải sau của thẻ tải lại theo bản mới
    @State private var seq = 0                 // lượt tải mới nhất (lọc nhanh liên tiếp: chỉ nhận kết quả lượt cuối)
    @State private var filters: Input = [:]
    @State private var filterOptions: [String: [Option]] = [:]
    @State private var selection: Set<Row.ID> = []
    @State private var form: FormRequest?
    @State private var importing: ActionSpec?
    @State private var uploading = false   // tải PDF / Word lên kho tài liệu (màn có lệnh kb.add_document)
    @State private var sending = false     // nút nhận tệp đang gửi (ADR 0262)
    @State private var opening: OpenRequest?
    @State private var toast: Toast?
    @State private var panelOpen = false   // nút "Bộ lọc" (ô lọc phụ — rà bố cục 18/11)
    @State private var mode = "table"   // góc nhìn bảng: table | board | calendar | timeline (0087, 0222)
    @State private var width: CGFloat = 0

    private var table: TablePage? { if case .table(let t)? = page { t } else { nil } }
    private var card: CardPage? { if case .card(let c)? = page { c } else { nil } }
    private var selectedRow: Row? { table?.rows.first { selection.contains($0.id) } }

    // MARK: cách bày bộ lọc (web filter-layout.ts)

    /// Màn kiểu báo cáo BI (0166): cột lọc bên trái — hẹp (< 640) thì lọc nằm trên như màn thường.
    private var side: Bool { spec.filters_side == true && spec.filters.contains { $0.group == nil } && (width == 0 || width >= 640) }
    private var split: (inline: [FieldSpec], more: [FieldSpec]) {
        side ? ([], spec.filters.filter { $0.group != nil }) : FilterSplit.split(spec.filters)
    }
    /// Bộ lọc chính + nút lên ĐẦU THẺ khi ít (≤ 3 ô, ≤ 2 nút) — đầu thẻ không thành một bức tường ô.
    #if os(iOS)
    private var inHead: Bool { false }   // iPhone: đầu thẻ chỉ tên; lọc + nút ở hàng chip một dòng (filterBar) — ô chọn trên đầu thẻ chữ to, xuống dòng
    #else
    private var inHead: Bool { head != nil && split.inline.count <= 3 && spec.actions.count <= 2 }
    #endif
    private var showAsk: Bool {
        guard let ask = spec.ask, let deskEnv else { return false }
        return !(ask.app_id != nil && deskEnv.builder?.appId == ask.app_id)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let head { headRow(head) } else if !embedded { header }
            if !inHead && (!split.inline.isEmpty || !split.more.isEmpty || (head == nil && embedded) || !spec.actions.isEmpty || table != nil) {
                filterBar
            }
            chips
            if let error { LoadError(message: error, at: errorAt, busy: loading, compact: page != nil) { Task { await reload() } } }
            if side {
                HStack(alignment: .top, spacing: 16) {
                    sideColumn
                    contentArea
                }
            } else {
                contentArea
            }
        }
        .padding(embedded ? 0 : 20)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { w in if abs(w - width) > 1 { width = w } }
        .overlay(alignment: .bottom) { toastView }
        .environment(\.screenPick, ScreenPick(get: { k in filters[k].flatMap { $0.isNull ? nil : $0.text } },
                                               set: { k, v in filters[k] = v.map { .string($0) }; Task { await reload() } }))
        .task {
            await prepareFilters()
            if !hasInitialPage { await reload() }
        }
        .onChange(of: refresh) { Task { await reload() } }   // trình tạo app vừa sửa ⇒ tải lại tại chỗ, không dựng lại khung (không nháy)
        .onChange(of: mode) { _, m in   // Kanban / lịch / dòng thời gian cần ĐỦ dòng; bảng đang chỉ có một trang ⇒ xin lại đủ
            if m != "table", let p = table?.paging, p.total > (table?.rows.count ?? 0) { Task { await reload() } }
        }
        .sheet(item: $form) { req in
            FormSheet(api: api, action: req.action, row: req.row) { message in
                show(message, isError: false)
                Task { await reload(); onChanged() }
            }
        }
        .sheet(isPresented: $uploading) {
            KBUploadSheet(api: api) { message in show(message, isError: false); Task { await reload(); onChanged() } }
        }
        .sheet(item: $importing) { action in
            ImportSheet(api: api, action: action) { Task { await reload(); onChanged() } }
        }
        .sheet(item: $opening) { req in
            OpenSheet(api: api, request: req)
        }
    }

    @ViewBuilder private var contentArea: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let page { content(page) } else if loading { ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .clipped()   // nội dung cuộn KHÔNG bao giờ vẽ đè lên đầu thẻ / hàng lọc
    }

    // MARK: đầu thẻ

    private var headSubtitle: String {
        if let c = card, !c.subtitle.isEmpty { return c.subtitle }
        return head?.subtitle ?? ""
    }

    private func headTitle(_ h: ScreenHead) -> some View {
        HStack(spacing: 10) {
            IconTile(symbol: h.symbol, tint: h.tint)
            VStack(alignment: .leading, spacing: 1) {
                Text(h.title).font(.t16).lineLimit(1)
                Text(headSubtitle).font(.t12).foregroundStyle(.secondary).lineLimit(1).truncationMode(.tail).help(headSubtitle)
            }
        }
    }

    private func closeButton(_ h: ScreenHead) -> some View {
        Button(action: h.onClose) { Image(systemName: "xmark") }.buttonStyle(.borderless).help("Đóng thẻ")
    }

    /// Công cụ trên đầu thẻ: bộ lọc chính + "Bộ lọc" (khi đủ ít) · hỏi trợ lý · nút lệnh (khi ít) · tải lại.
    @ViewBuilder private var headTools: some View {
        if inHead {
            ForEach(split.inline) { f in filterControl(f) }
            if !split.more.isEmpty { panelButton }
        }
        askButton(secondary: true)
        if inHead { commandButtons }
        if inHead, table != nil { exportButton }
        #if os(macOS)
        reloadButton   // iPhone: "Tải lại" nằm trong nút "…" của hàng lọc (filterBar) — không để một nút lẻ dưới tên thẻ
        #endif
    }

    /// Rộng: tên thẻ + công cụ một hàng; hẹp: tên thẻ một hàng, công cụ tự xuống dòng bên dưới (không ô nào đè lên ô khác).
    private func headRow(_ h: ScreenHead) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 10) {
                headTitle(h)
                Spacer(minLength: 16)
                HStack(spacing: 8) { headTools }.fixedSize()
                closeButton(h)
            }
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) { headTitle(h); Spacer(minLength: 8); closeButton(h) }
                FlowRow(spacing: 8) { headTools }
            }
        }
        .controlSize(.regular)
        .font(.t12)
    }

    // MARK: thanh trên (màn đứng riêng — không nằm trong thẻ)

    private var header: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center) { titleView; Spacer(); HStack(spacing: 8) { askButton(secondary: false); commandButtons; reloadButton }.fixedSize() }
            VStack(alignment: .leading, spacing: 10) { titleView; FlowRow { askButton(secondary: false); commandButtons; reloadButton } }
        }
        .controlSize(.regular)
    }

    private var titleView: some View {
        HStack(spacing: 10) {
            IconTile(symbol: spec.icon, tint: ModuleStyle.of(spec.query).tint)
            Text(page?.title.isEmpty == false ? page!.title : spec.title).font(.t20).lineLimit(2)
        }
    }

    @ViewBuilder
    private func askButton(secondary: Bool) -> some View {
        // nút của MÀN (vd "Tạo app" / "Sửa app" ⇒ trình tạo — 0098); đang sửa đúng app này trong trình tạo thì không cần. Trên đầu thẻ: nút phụ (viền)
        if showAsk, let ask = spec.ask, let deskEnv {
            let open = { if ask.mode == "builder" { deskEnv.openBuilder(prompt: ask.prompt, appId: ask.app_id) } else { deskEnv.ask(ask.prompt) } }
            if ask.app_id != nil {
                Button(action: open) { Label(ask.title, systemImage: "pencil") }.buttonStyle(.bordered).fixedSize()
            } else if secondary {
                Button(action: open) { Label { Text(ask.title) } icon: { Image(systemName: "sparkles").foregroundStyle(Color.accent) } }
                    .buttonStyle(.bordered).fixedSize()
            } else {
                Button(action: open) { Label(ask.title, systemImage: "sparkles") }.buttonStyle(.borderedProminent).fixedSize()
            }
        }
    }

    @ViewBuilder
    private var commandButtons: some View {
        if spec.actions.contains(where: { $0.command == "kb.add_document" }) {
            Button { uploading = true } label: { Label("Tải file (PDF / Word)", systemImage: "doc.badge.arrow.up") }
                .buttonStyle(.bordered).fixedSize()
                .help("Tải PDF / Word / chữ lên kho — PDF scan trợ lý tự đọc (mất vài giây)")
        }
        ForEach(spec.actions) { a in
            if let exts = a.upload {
                uploadButton(a, exts)
            } else if a.importable {
                Menu {
                    Button("Nhập một") { form = FormRequest(action: a, row: nil) }
                    Button("Nhập từ Excel…") { importing = a }
                } label: { Text(a.title) } primaryAction: { form = FormRequest(action: a, row: nil) }
                .fixedSize()
            } else if a.style == "primary" {
                Button(a.title) { form = FormRequest(action: a, row: nil) }
                    .buttonStyle(.borderedProminent)
                    .fixedSize()
            } else {
                Button(a.title, role: a.style == "destructive" ? .destructive : nil) { form = FormRequest(action: a, row: nil) }
                    .buttonStyle(.bordered)
                    .fixedSize()
            }
        }
    }

    /// Nút NHẬN TỆP (ADR 0262): Mac chọn tệp; iPhone Thư viện ảnh / Chụp ảnh / Chọn tệp (``AttachButton``). Mỗi tệp một lượt gửi, báo kết quả, tải lại bảng.
    @ViewBuilder
    private func uploadButton(_ a: ActionSpec, _ exts: String) -> some View {
        let types = exts.split(separator: ",").compactMap { UTType(filenameExtension: String($0)) }
        let button = AttachButton(types: types.isEmpty ? [.data] : types, onPick: { data, name in Task { await upload(a, data, name) } },
                                  onError: { show($0, isError: true) }) {
            Label(sending ? "Đang gửi…" : a.title, systemImage: "doc.badge.arrow.up")
        }
        .disabled(sending)
        .help("Nhận tệp " + exts.split(separator: ",").map { "." + $0 }.joined(separator: ", "))
        .fixedSize()
        if a.style == "primary" { button.buttonStyle(.borderedProminent) } else { button.buttonStyle(.bordered) }
    }

    private func upload(_ a: ActionSpec, _ data: Data, _ name: String) async {
        sending = true
        defer { sending = false }
        do {
            let r = try await api.uploadFile(a.command, data: data, filename: name, params: a.preset.mapValues(\.text))
            guard ["ok", "pending_approval", "proposed"].contains(r.status) else {
                show(r.error.isEmpty ? "Không nhận được tệp (\(r.status))" : r.error, isError: true)
                return
            }
            if case .object(let o) = r.output, case .string(let said) = o["summary"], !said.isEmpty { show(said, isError: false) }
            else { show("Đã nhận \(name)", isError: false) }
            await reload()
        } catch {
            show(error.localizedDescription, isError: true)
        }
    }

    /// Nút do server gửi trong thân bảng (đầu bảng / màn trống): lệnh nhập cả bảng ⇒ màn nhập Excel; còn lại ⇒ form.
    private func run(_ a: ActionSpec) {
        if a.import_only == true { importing = a } else { form = FormRequest(action: a, row: nil) }
    }

    private var exportButton: some View {
        Button { Task { await export() } } label: { Label("Xuất Excel", systemImage: "square.and.arrow.up") }
            .buttonStyle(.bordered)
            .help("Xuất bảng đang xem ra tệp Excel")
            .fixedSize()
    }

    private var reloadButton: some View {
        Button { Task { await reload() } } label: { Image(systemName: "arrow.clockwise") }
            .buttonStyle(.bordered)
            .help("Tải lại (⌘R)")
            .keyboardShortcut("r", modifiers: .command)
    }

    #if os(iOS)
    /// iPhone (rà 25/11 — màn Giao dịch ba dòng lọc + nút): MỘT dòng — ô chọn thành chip "Nhãn: giá trị ▾" cuộn ngang + "Bộ lọc", mọi nút lệnh
    /// (thêm / nhập, xuất Excel, tải lại, hỏi trợ lý) gom vào nút "…". Nút tải tệp (cần trình chọn tệp riêng) vẫn đứng ngoài.
    private var filterBar: some View {
        HStack(spacing: 8) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(split.inline) { f in compactFilter(f) }
                    if !split.more.isEmpty { panelButton }
                    ForEach(spec.actions.filter { $0.upload != nil }) { a in if let exts = a.upload { uploadButton(a, exts) } }
                }
            }
            .controlSize(.small)
            Menu {
                if spec.actions.contains(where: { $0.command == "kb.add_document" }) {
                    Button { uploading = true } label: { Label("Tải file (PDF / Word)", systemImage: "doc.badge.arrow.up") }
                }
                ForEach(spec.actions.filter { $0.upload == nil }) { a in
                    if a.importable {
                        Button { form = FormRequest(action: a, row: nil) } label: { Label(a.title, systemImage: "plus") }
                        Button { importing = a } label: { Label("\(a.title) từ Excel…", systemImage: "tablecells") }
                    } else {
                        Button(role: a.style == "destructive" ? .destructive : nil) { form = FormRequest(action: a, row: nil) } label: { Text(a.title) }
                    }
                }
                if table != nil { Button { Task { await export() } } label: { Label("Xuất Excel", systemImage: "square.and.arrow.up") } }
                Button { Task { await reload() } } label: { Label("Tải lại", systemImage: "arrow.clockwise") }
            } label: {
                Image(systemName: "ellipsis").font(.system(size: 17, weight: .semibold)).frame(width: 36, height: 32)
            }
            .buttonStyle(ToolChipStyle())
            .accessibilityLabel("Thao tác khác")
        }
        .font(.t12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Ô lọc dạng chip (iPhone): ô chọn ⇒ "Nhãn: giá trị ▾" mở menu; ô khác (đúng/sai, gõ chữ) giữ như cũ.
    @ViewBuilder private func compactFilter(_ f: FieldSpec) -> some View {
        if let opts = f.options ?? filterOptions[f.key], f.kind != "bool" {
            let cur = filters[f.key]?.text ?? f.default?.text ?? ""
            let shown = opts.first { $0.value == cur }?.label ?? "Tất cả"
            Menu {
                Picker(f.label, selection: Binding(get: { cur }, set: { filters[f.key] = .string($0); Task { await reload() } })) {
                    if !opts.contains(where: { $0.value.isEmpty }) { Text("Tất cả").tag("") }
                    ForEach(opts, id: \.value) { Text($0.label).tag($0.value) }
                }
            } label: {
                HStack(spacing: 4) {
                    Text(f.label + ":").foregroundStyle(.secondary)
                    Text(shown).foregroundStyle(Color.primary).lineLimit(1)
                    Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold)).foregroundStyle(.secondary)
                }
                .font(.t12m)
                .padding(.horizontal, 10).frame(height: 32)
                .background(Capsule().fill(Color.surface2))
            }
            .buttonStyle(.plain)
        } else {
            filterControl(f)
        }
    }
    #else
    /// Hàng lọc khi đầu thẻ không cầm được: ô chính + "Bộ lọc" bên trái, nút lệnh bên phải (tự xuống dòng — không đè nội dung).
    private var filterBar: some View {
        FlowRow(spacing: 10) {
            ForEach(split.inline) { f in filterControl(f) }
            if !split.more.isEmpty { panelButton }
            if head == nil && embedded { askButton(secondary: false) }
            if head != nil || embedded { commandButtons }
            if table != nil { exportButton }   // đầu thẻ không cầm được hàng lọc ⇒ Xuất Excel cuối hàng lọc
            if head == nil && embedded { reloadButton }
        }
        .controlSize(.regular)
        .font(.t12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
    #endif

    // MARK: bộ lọc

    /// Cột lọc bên trái của màn kiểu báo cáo BI: nhãn trên, ô dưới, rộng hết cột; cột đứng yên khi nội dung cuộn.
    private var sideColumn: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Bộ lọc").font(.t12m).foregroundStyle(.secondary)
            ForEach(spec.filters.filter { $0.group == nil }) { f in filterControl(f, stacked: true) }
            if !split.more.isEmpty { panelButton }
        }
        .controlSize(.regular)
        .font(.t12)
        .padding(12)
        .frame(width: 190, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.primary.opacity(0.08)))
    }

    private var activeMore: Int { split.more.filter { FilterSplit.isActive($0, filters[$0.key]) }.count }

    private var panelButton: some View {
        Button { panelOpen = true } label: {
            HStack(spacing: 5) {
                Image(systemName: "line.3.horizontal.decrease")
                Text("Bộ lọc")
                if activeMore > 0 {
                    Text("\(activeMore)").font(.t12m).monospacedDigit().padding(.horizontal, 5)
                        .background(Capsule().fill(Color.accent.opacity(0.15))).foregroundStyle(Color.accent)
                }
            }
        }
        .buttonStyle(.bordered)
        .fixedSize()
        .popover(isPresented: $panelOpen, arrowEdge: .bottom) {
            FilterPanel(fields: split.more, values: filters, options: filterOptions) { next in
                for (k, v) in next { filters[k] = v }
                panelOpen = false
                Task { await reload() }
            }
        }
    }

    /// Ô phụ đang bật hiện thành chip "Nhãn: giá trị ×" dưới hàng đầu — bấm chip mở "Bộ lọc", × bỏ lọc (về mặc định).
    @ViewBuilder private var chips: some View {
        let on = split.more.filter { FilterSplit.isActive($0, filters[$0.key]) }
        if !on.isEmpty {
            FlowRow(spacing: 6) {
                ForEach(on) { f in
                    HStack(spacing: 4) {
                        Button { panelOpen = true } label: {
                            Text(FilterSplit.chip(f, filters[f.key], options: f.options ?? filterOptions[f.key])).font(.t12m).lineLimit(1)
                                .frame(maxWidth: 260)
                        }
                        .buttonStyle(.plain)
                        Button { filters[f.key] = f.default.flatMap { $0.isNull ? nil : $0 }; Task { await reload() } } label: {
                            Image(systemName: "xmark").font(.system(size: 9, weight: .bold))
                        }
                        .buttonStyle(.plain).foregroundStyle(.secondary).help("Bỏ lọc")
                    }
                    .padding(.horizontal, 10).padding(.vertical, 4)
                    .background(Capsule().fill(Color.accent.opacity(0.10)))
                    .fixedSize()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder
    private func filterControl(_ f: FieldSpec, stacked: Bool = false) -> some View {
        let opts = f.options ?? filterOptions[f.key]
        if f.kind == "bool" {   // đúng / sai ⇒ ô tích chọn (không phải ô gõ chữ "false")
            Toggle(f.label, isOn: Binding(
                get: { filters[f.key]?.bool ?? f.default?.bool ?? false },
                set: { filters[f.key] = $0 ? .bool(true) : nil; Task { await reload() } }))
                #if os(macOS)
                .toggleStyle(.checkbox)
                #endif
                .fixedSize()
        } else {
            let control = Group {
                if let opts {
                    Picker(f.label, selection: Binding(
                        get: { filters[f.key]?.text ?? f.default?.text ?? "" },
                        set: { filters[f.key] = .string($0); Task { await reload() } })) {
                        if !opts.contains(where: { $0.value.isEmpty }) { Text("Tất cả").tag("") }
                        ForEach(opts, id: \.value) { Text($0.label).tag($0.value) }
                    }
                    .labelsHidden()
                } else {
                    TextField(f.label, text: Binding(
                        get: {
                            // tiền hiện kiểu Việt (4.500.000.000); gõ "4,5 tỷ" cũng được — server hiểu
                            if f.kind == "money", let n = filters[f.key]?.double { return Fmt.number(n) }
                            return filters[f.key]?.text ?? ""
                        },
                        set: { v in
                            if v.isEmpty { filters[f.key] = nil }
                            else if f.kind == "number", let n = Double(v) { filters[f.key] = .number(n) }
                            else { filters[f.key] = .string(v) }
                        }), prompt: Text(f.placeholder ?? (f.kind == "money" ? "VD 4,5 tỷ" : f.kind == "date" ? "yyyy-mm-dd" : "Tất cả")))
                    .labelsHidden()
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await reload() } }
                }
            }
            if stacked {
                VStack(alignment: .leading, spacing: 4) {
                    Text(f.label).foregroundStyle(.secondary).lineLimit(2)
                    control.frame(maxWidth: .infinity, alignment: .leading)
                }
                .help(f.help)
            } else {
                // ô luôn có nhãn đứng trước — giá trị "2" đứng một mình thì không ai biết là số PN; ô chọn dài ⇒ cắt (≤ 220), không kéo cả hàng
                HStack(spacing: 6) {
                    Text(f.label).foregroundStyle(.secondary).lineLimit(1).fixedSize()
                    if opts != nil { control.frame(maxWidth: 220).fixedSize() }
                    else { control.frame(width: f.kind == "number" ? 60 : 130) }
                }
                .help(f.help)
            }
        }
    }

    // MARK: nội dung

    @ViewBuilder
    private func content(_ page: Page) -> some View {
        switch page {
        case .detail(let d):
            DetailView(page: d)
        case .card(let c):
            if let tabs = c.tabs {   // thẻ nhiều trang (app tự ghép — 0098): tab = chạy lại truy vấn của màn với tham số trang
                Picker("Trang", selection: Binding(get: { tabs.current }, set: { k in filters[tabs.param] = .string(k); Task { await reload() } })) {
                    ForEach(tabs.items) { Text($0.title).tag($0.key) }
                }
                .pickerStyle(.segmented).labelsHidden().fixedSize()
            }
            CardView(page: c, onChanged: { Task { await reload(); onChanged() } }, inHead: head != nil, rev: rev)
        case .table(let t):
            #if os(iOS)
            // iPhone (rà 25/11 — màn Giao dịch không cuộn): ô số 2 cột chiếm gần hết màn mà danh sách dòng tự cuộn trong khung riêng ⇒ không tới
            // được phần dưới. Ô số + ghi chú + các dòng chung MỘT vùng cuộn (bộ lọc đứng yên ở trên); danh sách dòng thôi cuộn riêng.
            ScrollView {
                VStack(alignment: .leading, spacing: 12) { tableArea(t, scrolls: false) }
            }
            .scrollDismissesKeyboard(.interactively)
            #else
            tableArea(t, scrolls: true)
            #endif
        }
    }

    /// Phần BẢNG của màn: lời nhắc đầu bảng, ô số, ghi chú, góc nhìn, các dòng. ``scrolls`` = danh sách dòng tự cuộn (Mac; iPhone đã bọc cả khối).
    @ViewBuilder
    private func tableArea(_ t: TablePage, scrolls: Bool) -> some View {
            ForEach(Array(t.header.enumerated()), id: \.offset) { _, b in
                if case .callout(let tone, let title, let text, let facts, let draft, let actions) = b {
                    Callout(tone: tone, title: title, text: text, facts: facts, draft: draft, actions: actions) { run($0) }
                }
            }
            if !t.kpis.isEmpty { KPIRow(kpis: t.kpis, inset: embedded) }
            if !t.note.isEmpty { ClampNote(text: t.note) }
            if let v = t.views {
                Picker("Góc nhìn", selection: $mode) {
                    Label("Bảng", systemImage: "tablecells").tag("table")
                    if v.board != nil { Label("Kanban", systemImage: "rectangle.split.3x1").tag("board") }
                    if v.calendar != nil { Label("Lịch", systemImage: "calendar").tag("calendar") }
                    if v.timeline != nil { Label("Dòng thời gian", systemImage: "chart.bar.xaxis").tag("timeline") }
                }
                .pickerStyle(.segmented).labelsHidden().fixedSize()
            }
            if t.rows.isEmpty {
                ContentUnavailableView {
                    Label(t.empty, systemImage: "tray")
                } actions: {
                    ForEach(t.emptyActions, id: \.title) { a in
                        Button(a.title) { run(a) }.buttonStyle(.borderedProminent)
                    }
                }
                .frame(maxHeight: .infinity)
            } else if mode == "board", let b = t.views?.board {
                BoardView(board: board(t, b), onMove: { card, col in await move(card, to: col, spec: b, rows: t.rows) },
                          onOpen: { o in opening = OpenRequest(title: o.title ?? "", query: o.query, input: o.input) })
                    .frame(maxHeight: .infinity, alignment: .top)
            } else if mode == "timeline", let tl = t.views?.timeline {
                Group {
                    if scrolls {
                        ScrollView { TimelineList(data: tl) { o in opening = OpenRequest(title: o.title ?? "", query: o.query, input: o.input) }.padding(12) }
                    } else {
                        TimelineList(data: tl) { o in opening = OpenRequest(title: o.title ?? "", query: o.query, input: o.input) }.padding(12)
                    }
                }
                    .cardSurface(radius: embedded ? 12 : 16)
            } else if mode == "calendar", let cal = t.views?.calendar {
                AgendaView(rows: t.rows, spec: cal, open: { open($0) })
                    .cardSurface(radius: embedded ? 12 : 16)
            } else {
                DataTable(page: t, selection: $selection, scrolls: scrolls,
                          menu: { row in rowMenu(row) },
                          open: { row in open(row) },
                          activate: spec.row_click == nil ? nil : { row in activate(row) })
                    .cardSurface(radius: embedded ? 12 : 16)
                    .frame(minHeight: 240)
                if let more = moreLabel(t) {
                    // trang phía server: "Hiện thêm" xin trang kế, nối vào bảng đang thấy (ô số vẫn tính trên cả tập)
                    Button { Task { await loadMore() } } label: {
                        HStack(spacing: 6) { if loadingMore { ProgressView().controlSize(.small) }; Text(more) }
                    }
                    .buttonStyle(.borderless).font(.t14m).disabled(loadingMore)
                    .frame(maxWidth: .infinity)
                }
                if let row = selectedRow { rowBar(row) }
            }
    }

    private func moreLabel(_ t: TablePage) -> String? {
        guard let p = t.paging, p.total > t.rows.count else { return nil }
        let rest = p.total - t.rows.count
        return "Hiện thêm \(Fmt.number(Double(min(rest, p.limit)))) dòng (còn \(Fmt.number(Double(rest))))"
    }

    @ViewBuilder
    private func rowMenu(_ row: Row) -> some View {
        ForEach(spec.row_actions.filter { $0.applies(to: row) }) { a in
            Button(a.title) { form = FormRequest(action: a, row: row) }
        }
        if let o = spec.open {
            Divider()
            Button(o.title.isEmpty ? "Mở" : o.title) { open(row) }
        }
    }

    private func rowBar(_ row: Row) -> some View {
        let actions = spec.row_actions.filter { $0.applies(to: row) }
        return HStack(spacing: 8) {
            Text("Dòng đang chọn:").font(.t12).foregroundStyle(.secondary)
            ForEach(actions) { a in
                Button(a.title) { form = FormRequest(action: a, row: row) }
                    .buttonStyle(.borderedProminent)
                    .tint(a.style == "destructive" ? .red : a.style == "primary" ? .accentColor : .gray)
                    .controlSize(.small)
            }
            if let o = spec.open {
                Button(o.title.isEmpty ? "Mở" : o.title) { open(row) }.controlSize(.small)
            }
            if actions.isEmpty && spec.open == nil {
                Text("Không có thao tác cho dòng này").font(.t12).foregroundStyle(.tertiary)
            }
            Spacer()
        }
    }

    @ViewBuilder
    private var toastView: some View {
        if let toast {
            Text(toast.text)
                .font(.t14m)
                .padding(.horizontal, 14).padding(.vertical, 8)
                .foregroundStyle(toast.isError ? Color.white : Color.primary)
                .background(toast.isError ? AnyShapeStyle(Color.red) : AnyShapeStyle(.regularMaterial), in: Capsule())
                .shadow(radius: 6)
                .padding(.bottom, 18)
                .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    private func show(_ text: String, isError: Bool) {
        withAnimation { toast = Toast(text: text, isError: isError) }
        Task {
            try? await Task.sleep(for: .seconds(3.5))
            withAnimation { if toast?.text == text { toast = nil } }
        }
    }

    /// Kanban từ CÙNG các dòng: cột theo thứ tự server gửi, thẻ mở đúng thẻ như bấm dòng (``spec.open``).
    private func board(_ t: TablePage, _ b: TableViews.BoardSpec) -> CardPage.Board {
        let cols = b.columns.map { c in
            CardPage.Board.Column(key: c.key, title: c.title, subtitle: nil, tone: c.tone,
                                  items: t.rows.filter { ($0.values[b.by]?.text ?? "") == c.key }.map { r in
                                      CardPage.Board.Card(id: r.id, title: r.values[b.title]?.text ?? "",
                                                          subtitle: b.subtitle.flatMap { r.values[$0]?.text }, tone: nil, badge: nil, open: openRef(r))
                                  })
        }
        return CardPage.Board(columns: cols, move: b.move)
    }

    private func openRef(_ row: Row) -> CardPage.Open? {
        guard let o = spec.open else { return nil }
        var input: Input = [:]
        for (field, col) in o.bind { input[field] = row.values[col] ?? .null }
        return CardPage.Open(title: o.title, query: o.query, input: input)
    }

    /// Kéo thẻ sang cột = chạy lệnh server khai (kiểm quyền / luật như mọi lệnh); cột cần hỏi thêm ⇒ mở form điền sẵn.
    private func move(_ card: CardPage.Board.Card, to column: String, spec b: TableViews.BoardSpec, rows: [Row]) async -> Bool {
        guard let mv = b.move else { return false }
        let input: Input = [mv.id_key: .string(card.id), mv.column_key: .string(column)]
        if mv.form_columns?.contains(column) == true {
            let title = b.columns.first { $0.key == column }?.title ?? column
            form = FormRequest(action: ActionSpec(command: mv.command, title: "\(card.title) → \(title)", style: "primary", bind: [:], preset: input,
                                                  when: [:], importable: false), row: rows.first { $0.id == card.id })
            return false
        }
        do {
            let r = try await api.command(mv.command, input, key: UUID().uuidString)
            guard r.status == "ok" else { show(r.error.isEmpty ? "Không đổi được (\(r.status))" : r.error, isError: true); return false }
            await reload()
            return true
        } catch {
            show(error.localizedDescription, isError: true)
            return false
        }
    }

    /// Bấm một lần (server khai ``row_click``): mở chi tiết / form nút mặc định; dòng không thoả ``when`` của nút ⇒ chỉ chọn.
    private func activate(_ row: Row) {
        guard let click = spec.row_click else { return }
        if click == "open" {
            open(row)
        } else if let a = spec.row_actions.first(where: { $0.command == click }), a.applies(to: row) {
            form = FormRequest(action: a, row: row)
        }
    }

    private func open(_ row: Row) {
        guard let o = spec.open else { return }
        var input: Input = [:]
        for (field, col) in o.bind { input[field] = row.values[col] ?? .null }
        opening = OpenRequest(title: o.title, query: o.query, input: input)
    }

    private func prepareFilters() async {
        for f in spec.filters {
            if filters[f.key] == nil, let d = f.default, !d.isNull { filters[f.key] = d }
            if f.options == nil, let q = f.options_query, let opts = try? await api.options(q) {
                filterOptions[f.key] = opts
            }
        }
    }

    /// Bộ lọc người chọn + đầu vào cố định của màn (0098) — bộ lọc không bao giờ đè đầu vào cố định (server đã bỏ trường đó khỏi bộ lọc).
    private var queryInput: Input { filters.merging(spec.input ?? [:]) { _, fixed in fixed } }

    /// Trang của màn: phần nhanh về trước (khối tải sau thẻ tự tải), bảng xin một trang khi đang ở góc nhìn bảng.
    private func reload() async {
        seq += 1
        let n = seq
        loading = true
        defer { if n == seq { loading = false } }
        do {
            let p = try await api.page(spec.query, queryInput, rows: mode == "table" ? (0, Self.pageRows) : nil)
            guard n == seq else { return }
            page = p
            rev += 1
            error = nil
            errorAt = nil
            if let t = table { selection = selection.filter { id in t.rows.contains { $0.id == id } } }
        } catch {
            guard n == seq else { return }
            self.error = error.localizedDescription
            errorAt = .now
        }
    }

    /// Trang kế của bảng (trang phía server): nối vào bảng đang thấy. Lọc / tải lại giữa chừng ⇒ bỏ kết quả cũ.
    private func loadMore() async {
        guard var t = table, let p = t.paging else { return }
        let n = seq
        loadingMore = true
        defer { loadingMore = false }
        do {
            guard case .table(let next) = try await api.page(spec.query, queryInput, rows: (t.rows.count, p.limit)), n == seq else { return }
            let seen = Set(t.rows.map(\.id))
            t.rows += next.rows.filter { !seen.contains($0.id) }
            t.paging = next.paging ?? p
            page = .table(t)
        } catch {
            show(error.localizedDescription, isError: true)
        }
    }

    private func export() async {
        do {
            let data = try await api.xlsx(query: spec.query, queryInput)
            if let name = saveFile(data, suggestedName: "\(page?.title ?? spec.title).xlsx", type: xlsxType) {
                show("Đã lưu \(name)", isError: false)
            }
        } catch {
            show(error.localizedDescription, isError: true)
        }
    }
}

// MARK: - Khối dùng chung

/// "Bộ lọc" (rà bố cục 18/11, kiểu Linear / Notion): nguyên bộ ô phụ — chỉnh rồi Áp dụng một lần; "Xoá lọc" đưa mọi ô về mặc định.
/// Mac: khung nổi; iPhone: popover tự thành tấm trượt.
struct FilterPanel: View {
    let fields: [FieldSpec]
    let values: Input
    let options: [String: [Option]]
    let apply: ([String: JSON?]) -> Void
    @State private var draft: Input = [:]

    init(fields: [FieldSpec], values: Input, options: [String: [Option]], apply: @escaping ([String: JSON?]) -> Void) {
        self.fields = fields
        self.values = values
        self.options = options
        self.apply = apply
        _draft = State(initialValue: values)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Bộ lọc").font(.t14m)
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(fields) { f in row(f) }
                }
                .padding(.vertical, 2)
            }
            .frame(maxHeight: 420)
            HStack {
                Button("Xoá lọc") {
                    var next: [String: JSON?] = [:]
                    for f in fields { next[f.key] = f.default.flatMap { $0.isNull ? nil : $0 } }
                    apply(next)
                }
                Spacer()
                Button("Áp dụng") {
                    var next: [String: JSON?] = [:]
                    for f in fields { next[f.key] = draft[f.key] }
                    apply(next)
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
        }
        .font(.t12)
        .padding(16)
        .frame(width: 320)
        #if os(iOS)
        .presentationDetents([.medium, .large])
        #endif
    }

    @ViewBuilder private func row(_ f: FieldSpec) -> some View {
        let opts = f.options ?? options[f.key]
        VStack(alignment: .leading, spacing: 4) {
            if f.kind == "bool" {
                Toggle(f.label, isOn: Binding(get: { draft[f.key]?.bool ?? false }, set: { draft[f.key] = $0 ? .bool(true) : nil }))
            } else {
                Text(f.label).foregroundStyle(.secondary)
                if let opts {
                    Picker(f.label, selection: Binding(get: { draft[f.key]?.text ?? f.default?.text ?? "" }, set: { draft[f.key] = .string($0) })) {
                        if !opts.contains(where: { $0.value.isEmpty }) { Text("Tất cả").tag("") }
                        ForEach(opts, id: \.value) { Text($0.label).tag($0.value) }
                    }
                    .labelsHidden()
                    .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    TextField(f.label, text: Binding(
                        get: { f.kind == "money" ? (draft[f.key]?.double.map(Fmt.number) ?? draft[f.key]?.text ?? "") : (draft[f.key]?.text ?? "") },
                        set: { v in
                            if v.isEmpty { draft[f.key] = nil }
                            else if f.kind == "number", let n = Double(v) { draft[f.key] = .number(n) }
                            else { draft[f.key] = .string(v) }
                        }), prompt: Text(f.placeholder ?? (f.kind == "money" ? "VD 4,5 tỷ" : f.kind == "date" ? "yyyy-mm-dd" : "Tất cả")))
                    .labelsHidden()
                    .textFieldStyle(.roundedBorder)
                }
            }
        }
    }
}

struct KPIRow: View {
    let kpis: [KPI]
    var inset = false  // nằm trên mặt thẻ trắng ⇒ ô xám nhạt, không viền
    var onEdit: ((ActionSpec) -> Void)? = nil   // app tự ghép (0098): ô số có nút sửa ⇒ menu chuột phải
    var body: some View {
        // ≤ 6 ô giãn đều cùng một hàng khi đủ rộng; hẹp vẫn ít nhất hai cột; dòng cuối ít ô ⇒ ô giãn hết dòng (web KpiRow)
        // chưa có gì thì ẩn (23/11/2026): ô 0 / trống không vẽ, cả hàng 0 thì không có hàng (cùng web `shownKpis`)
        let shown = kpis.filter(\.shown)
        if !shown.isEmpty {
            KPIFlow(spacing: 10) {
                ForEach(shown, id: \.label) { k in KPITile(k: k, inset: inset, onEdit: onEdit) }
            }
        }
    }
}

/// Một ô số: nhãn (≤ 2 dòng) · số (một dòng, tự thu nhỏ chứ không cắt) · gợi ý. Ô cùng hàng cao bằng nhau.
struct KPITile: View {
    @Environment(Desk.self) private var desk: Desk?
    let k: KPI
    #if os(iOS)
    static let pad: CGFloat = 12, hintLines = 1   // iPhone: ô thấp, gợi ý một dòng (đủ câu ở menu chạm giữ / bấm xem nguồn) — như app Phê Nâu
    #else
    static let pad: CGFloat = 14, hintLines = 2
    #endif
    var inset = false
    var onEdit: ((ActionSpec) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(k.label).font(.t12).foregroundStyle(.secondary).lineLimit(2).fixedSize(horizontal: false, vertical: true)
                if k.drill != nil { Image(systemName: "chevron.right").font(.system(size: 9, weight: .semibold)).foregroundStyle(.tertiary) }
            }
            Text(Fmt.kpi(k)).font(.t20).monospacedDigit().foregroundStyle(Color.tone(k.tone)).lineLimit(1).minimumScaleFactor(0.55)
            if !k.hint.isEmpty { Text(k.hint).font(.t12).foregroundStyle(.tertiary).lineLimit(Self.hintLines).fixedSize(horizontal: false, vertical: true) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(Self.pad)
        .background(inset ? AnyView(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.surface2))
                          : AnyView(Color.clear))
        .modifier(SurfaceIf(on: !inset))
        .contentShape(Rectangle())
        // nguồn gốc con số (0097): bấm ô số ⇒ mở đúng các dòng tạo nên nó, bằng quyền người xem
        .onTapGesture { if let d = k.drill, let desk { Task { await desk.openTarget(query: d.query, input: d.input, title: k.label) } } }
        .help(k.drill != nil ? (k.hint.isEmpty ? k.label : k.hint) + " · Bấm để xem các dòng tạo nên con số này" : (k.hint.isEmpty ? k.label : k.hint))
        .contextMenu { if let e = k.edit, let onEdit { Button(e.title) { onEdit(e) } } }
    }
}

#if os(macOS)
struct DataTable<Menu: View>: View {
    let page: TablePage
    @Binding var selection: Set<Row.ID>
    var scrolls = true   // iPhone: false khi cả màn đã nằm trong một vùng cuộn (ScreenView.tableArea) — tránh cuộn lồng cuộn
    @ViewBuilder let menu: (Row) -> Menu
    let open: (Row) -> Void
    /// Bấm MỘT lần (màn khai ``row_click``): chọn dòng bằng chuột ⇒ mở luôn. ⌘ / ⇧-bấm, phím mũi tên: chỉ chọn như cũ.
    var activate: ((Row) -> Void)? = nil
    @State private var activatedAt = Date.distantPast   // bấm đúp ngay sau bấm-mở: không mở lần hai

    var body: some View {
        Table(page.rows, selection: $selection) {
            TableColumnForEach(page.shownColumns) { col in
                TableColumn(col.label) { row in
                    Cell(column: col, value: row.values[col.key])
                }
                .width(min: 40, ideal: CGFloat(col.width ?? Self.idealWidth(col, in: page)))
                .alignment(Self.numeric(col) ? .trailing : .leading)   // cột số: số VÀ tiêu đề canh phải (như web)
            }
        }
        .contextMenu(forSelectionType: Row.ID.self) { ids in
            if let id = ids.first, let row = page.rows.first(where: { $0.id == id }) { menu(row) }
        } primaryAction: { ids in
            if activate != nil && Date().timeIntervalSince(activatedAt) < 0.5 { return }
            if let id = ids.first, let row = page.rows.first(where: { $0.id == id }) { open(row) }
        }
        .onChange(of: selection) { old, new in
            // chỉ khi CHUỘT vừa chọn đúng một dòng mới, không giữ ⌘ / ⇧ (chọn thêm) — phím mũi tên đổi dòng không mở
            guard let activate, new.count == 1, new != old, let id = new.first, let row = page.rows.first(where: { $0.id == id }),
                  let e = NSApp.currentEvent, e.type == .leftMouseDown || e.type == .leftMouseUp,
                  e.modifierFlags.intersection([.command, .shift]).isEmpty else { return }
            activatedAt = Date()
            activate(row)
        }
        .alternatingRowBackgrounds(.disabled)   // nhẹ như bds: không sọc xen kẽ kéo dài cả phần dòng trống
        .scrollContentBackground(.hidden)
        .font(.t14)
    }

    static func numeric(_ c: Column) -> Bool { ["money", "number", "percent"].contains(c.format) }

    static func idealWidth(_ c: Column, in page: TablePage) -> Int {
        switch c.format {
        case "money": return 150
        case "number", "percent": return 80
        case "date": return 95
        case "datetime": return 130
        case "phone": return 120
        case "badge": return 110
        default:
            // cột chữ: vừa ĐỦ giá trị dài nhất (≤ 240) và tiêu đề — mã / tên ngắn không bị bẻ thành 2 dòng, cột thừa không phình ra
            let longest = page.rows.prefix(200).map { ($0.values[c.key]?.text ?? "").count }.max() ?? 0
            return max(70, min(240, max(longest * 8, c.label.count * 8) + 28))
        }
    }
}
#else
/// iPhone: bảng nhiều cột không vừa màn hình (``Table`` của SwiftUI chỉ hiện cột đầu) ⇒ mỗi dòng là MỘT THẺ: tên + nhãn ở đầu, các cột
/// còn lại thành cặp "nhãn: giá trị". Chạm = chọn dòng (hiện thanh thao tác của màn), giữ = menu thao tác, SĐT chạm để gọi.
struct DataTable<Menu: View>: View {
    let page: TablePage
    @Binding var selection: Set<Row.ID>
    var scrolls = true   // iPhone: false khi cả màn đã nằm trong một vùng cuộn (ScreenView.tableArea) — tránh cuộn lồng cuộn
    @ViewBuilder let menu: (Row) -> Menu
    let open: (Row) -> Void
    /// Chạm MỘT lần (màn khai ``row_click``): chọn thẻ rồi mở luôn; giữ = menu thao tác như cũ.
    var activate: ((Row) -> Void)? = nil

    private var title: Column? { page.shownColumns.first { $0.format == "text" } ?? page.shownColumns.first { $0.format != "badge" } }
    private var badges: [Column] { page.shownColumns.filter { $0.format == "badge" } }
    private var rest: [Column] { page.shownColumns.filter { $0.key != title?.key && $0.format != "badge" } }

    var body: some View {
        if scrolls {
            ScrollView { rows }
        } else {
            rows
        }
    }

    private var rows: some View {
        LazyVStack(spacing: 8) {
            ForEach(page.rows) { row in card(row) }
        }
        .padding(10)
    }

    @ViewBuilder
    private func card(_ row: Row) -> some View {
        let picked = selection.contains(row.id)
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if let t = title {
                    Text(Fmt.cell(row.values[t.key], t.format, labels: t.labels)).font(.t14m).lineLimit(2)
                }
                Spacer(minLength: 4)
                ForEach(badges) { b in
                    let text = Fmt.cell(row.values[b.key], b.format, labels: b.labels)
                    if !text.isEmpty { Badge(text: text, tone: b.tones?[row.values[b.key]?.text ?? ""]) }
                }
            }
            ForEach(rest) { c in
                let text = Fmt.cell(row.values[c.key], c.format, labels: c.labels)
                if !text.isEmpty {
                    HStack(alignment: .firstTextBaseline) {
                        Text(c.label).font(.t12).foregroundStyle(.secondary).frame(width: 96, alignment: .leading)
                        if c.format == "phone", !text.contains("•"), let url = URL(string: "tel:" + text.filter { $0.isNumber || $0 == "+" }) {   // số đã che (0214): không thành link
                            Link(destination: url) { Label(text, systemImage: "phone.fill").font(.t12m) }
                        } else {
                            Text(text).font(.t12).foregroundStyle((row.values[c.key]?.double ?? 0) < 0 && ["money", "number", "percent"].contains(c.format) ? Color.red : Color.primary)
                                .multilineTextAlignment(.leading)
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(picked ? Color.accent : Color.primary.opacity(0.08), lineWidth: picked ? 2 : 1))
        .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .onTapGesture {
            if let activate {
                selection = [row.id]   // vẫn chọn: thanh thao tác của dòng còn đó sau khi đóng chi tiết
                activate(row)
            } else {
                selection = picked ? [] : [row.id]
            }
        }
        .contextMenu { menu(row); Button("Mở") { open(row) } }
    }
}
#endif

struct Cell: View {
    let column: Column
    let value: JSON?

    var body: some View {
        let text = Fmt.cell(value, column.format, labels: column.labels)
        switch column.format {
        case "badge":
            Badge(text: text, tone: column.tones?[value?.text ?? ""])
        case "money", "number", "percent":
            Text(text).monospacedDigit().lineLimit(1).frame(maxWidth: .infinity, alignment: .trailing)
                .foregroundStyle((value?.double ?? 0) < 0 ? Color.red : Color.primary)
        case "phone", "date", "datetime":
            // không bẻ dòng giữa số ("0100 / 000 024"): cột hẹp thì cắt đuôi, rê chuột xem đủ
            Text(text).monospacedDigit().lineLimit(1).truncationMode(.middle).help(text)
        default:
            Text(text).lineLimit(2).help(text)
        }
    }
}

struct DetailView: View {
    let page: DetailPage
    @Environment(Desk.self) private var desk: Desk?
    /// Chỉ sheet truyền vào (đóng sheet sau "Hỏi trợ lý…"). KHÔNG dùng `dismiss` của môi trường: ở cửa sổ chính nó ĐÓNG
    /// CỬA SỔ (app thoát) — kể cả khi `isPresented` báo true (đã dính 30/10).
    var onClose: (() -> Void)? = nil
    @State private var action: ActionSpec?
    @State private var done: String?
    @State private var pdf: OriginalFile?
    /// 0102: có bản gốc ⇒ hai góc nhìn; chữ bóc ("extracted") là bản phụ ⇒ mặc định BẢN GỐC, giấy tờ tự soạn ⇒ mặc định NỘI DUNG.
    @State private var showOriginal: Bool? = nil

    private var isDocument: Bool { page.style == "document" }
    private var extracted: Bool { page.text_role == "extracted" }
    private var originalShown: Bool { page.original != nil && (showOriginal ?? extracted) }

    /// Cả tài liệu thành một văn bản markdown: mục = tiêu đề ⇒ một ô chữ, chọn / chép liền mạch.
    private var documentText: String {
        page.sections.compactMap { s in s.text.map { "## \(s.title)\n\($0)" } }.joined(separator: "\n\n")
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if !page.subtitle.isEmpty { Text(page.subtitle).font(.t14).foregroundStyle(.secondary).textSelection(.enabled) }
                toolbar
                if let original = page.original {
                    Picker("Góc nhìn", selection: Binding(get: { originalShown }, set: { showOriginal = $0 })) {
                        if extracted {
                            Text(original.title).tag(true)
                            Text("Văn bản trích").tag(false)
                        } else {
                            Text("Nội dung").tag(false)
                            Text(original.title).tag(true)
                        }
                    }
                    .pickerStyle(.segmented).labelsHidden().fixedSize()
                }
                if let original = page.original, originalShown {
                    OriginalPane(original: original)
                } else {
                    if extracted {
                        Text("Chữ bóc từ bản gốc — dùng để tìm kiếm và để trợ lý trích dẫn; bản đúng là bản gốc.")
                            .font(.t12).foregroundStyle(.secondary)
                    }
                    if isDocument {
                        MarkdownText(text: documentText)
                            .padding(.top, 4)
                    } else {
                        ForEach(Array(page.sections.enumerated()), id: \.offset) { _, s in record(s) }
                    }
                }
            }
            .frame(maxWidth: isDocument ? 720 : .infinity, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .sheet(item: $action) { a in
            if let desk { FormSheet(api: desk.api, action: a, row: nil) { done = $0 } }
        }
        .sheet(item: $pdf) { PDFSheet(file: $0) }
    }

    @ViewBuilder
    private var toolbar: some View {
        let links = desk == nil ? [] : (page.links ?? [])
        let actions = desk == nil ? [] : (page.actions ?? [])
        if !links.isEmpty || !actions.isEmpty || (desk != nil && page.ask != nil) || isDocument {
            FlowRow(spacing: 8) {
                if let ask = page.ask, let desk {
                    Button { desk.ask(ask.prompt); onClose?() } label: { Label(ask.title, systemImage: "sparkles") }
                        .buttonStyle(.borderedProminent)
                }
                ForEach(actions) { a in
                    if a.style == "primary" {
                        Button(a.title) { action = a }.buttonStyle(.borderedProminent)
                    } else {
                        Button(a.title, role: a.style == "destructive" ? .destructive : nil) { action = a }.buttonStyle(.bordered)
                    }
                }
                ForEach(links, id: \.path) { l in
                    let isPDF = (l.filename ?? "").lowercased().hasSuffix(".pdf")
                    Button { Task { await download(l) } } label: { Label(l.title, systemImage: isPDF ? "doc.richtext" : "arrow.down.doc") }
                        .buttonStyle(.bordered)
                }
                if isDocument {
                    Button { copy(page.title + "\n\n" + documentText) } label: { Label("Sao chép", systemImage: "doc.on.doc") }
                        .buttonStyle(.bordered)
                        .help("Chép cả tài liệu")
                }
                if let done { Label(done, systemImage: "checkmark.seal.fill").font(.t12m).foregroundStyle(Color.ok) }
            }
            .controlSize(.regular)
        }
    }

    /// Hồ sơ một bản ghi: nhãn – giá trị (bỏ ô trống), đoạn chữ trong khung; chép bằng nút nhỏ có chú thích, như macOS.
    private func record(_ s: DetailPage.Section) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(s.title).font(.t16)
                Spacer()
                if let text = s.text {
                    Button { copy(text) } label: { Image(systemName: "doc.on.doc").font(.system(size: 12)) }
                        .buttonStyle(.borderless)
                        .foregroundStyle(.secondary)
                        .help("Sao chép “\(s.title)”")
                }
            }
            let fields = s.fields.filter { !Fmt.cell($0.value, $0.format).trimmingCharacters(in: .whitespaces).isEmpty }
            if !fields.isEmpty {
                Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 6) {
                    ForEach(fields, id: \.label) { f in
                        GridRow {
                            Text(f.label).font(.t14).foregroundStyle(.secondary)
                            Text(Fmt.cell(f.value, f.format)).font(.t14m).monospacedDigit().textSelection(.enabled)
                        }
                    }
                }
            }
            if let text = s.text {
                Text(text).font(.t14).textSelection(.enabled)
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.background.secondary, in: RoundedRectangle(cornerRadius: 8))
            }
            if let t = s.table { SectionTable(table: t) }
        }
    }

    private func copy(_ text: String) {
        copyToPasteboard(text)
        desk?.show("Đã chép")
    }

    private func download(_ l: DetailPage.Link) async {
        guard let desk else { return }
        do {
            let data = try await desk.api.download(l.path)
            if let name = l.filename, name.lowercased().hasSuffix(".pdf") {   // PDF xem ngay trong app, đúng trang (0052)
                pdf = OriginalFile(title: l.title, filename: name, data: data, page: l.page)
                return
            }
            if let name = saveFile(data, suggestedName: l.filename ?? "tai-lieu") { desk.show("Đã lưu \(name)") }
        } catch {
            desk.show(error.localizedDescription, isError: true)
        }
    }
}

/// Bản gốc nhúng ngay trên trang (0102) — tải PDF qua API (kèm danh tính); máy chủ chưa xem trước được (409: chưa cài LibreOffice…)
/// ⇒ hiện lý do, nút tải file nằm ở hàng liên kết phía trên (app không tự ghép đường tải).
private struct OriginalPane: View {
    let original: DetailPage.Original
    @Environment(Desk.self) private var desk: Desk?
    @State private var data: Data?
    @State private var error: String?

    var body: some View {
        Group {
            if let data {
                PDFKitView(data: data, page: original.page, highlight: nil, value: nil)
                    .frame(height: 720)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.separator))
            } else if let error {
                VStack(alignment: .leading, spacing: 4) {
                    Text(error).font(.t14).textSelection(.enabled)
                    Text("Dùng nút tải ở trên để mở file trên máy.").font(.t12).foregroundStyle(.secondary)
                }
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.background.secondary, in: RoundedRectangle(cornerRadius: 8))
            } else {
                ProgressView("Đang mở bản gốc…").frame(maxWidth: .infinity, minHeight: 240)
            }
        }
        .task(id: original.path) {
            guard let desk else { return }
            do { data = try await desk.api.download(original.path); error = nil } catch { self.error = error.localizedDescription }
        }
    }
}

/// Bảng thật của mục (0102): hàng đầu tiêu đề, cột số canh phải, hàng cộng đậm.
private struct SectionTable: View {
    let table: DetailPage.Table
    private func numeric(_ c: String) -> Bool {
        let s = c.replacingOccurrences(of: " ", with: "")
        return !s.isEmpty && s.allSatisfy { $0.isNumber || $0 == "." || $0 == "," || $0 == "%" }
    }
    /// Cột số = mọi ô có chữ của cột đều là số ⇒ cả cột (kể cả tiêu đề) canh phải.
    private func numericColumn(_ i: Int) -> Bool {
        let cells = (table.rows + [table.total ?? []]).compactMap { i < $0.count ? $0[i] : nil }.filter { !$0.isEmpty }
        return !cells.isEmpty && cells.allSatisfy(numeric)
    }
    var body: some View {
        let width = table.columns.count
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
            GridRow {
                ForEach(Array(table.columns.enumerated()), id: \.offset) { i, c in
                    Text(c).font(.t12m).foregroundStyle(.secondary).gridColumnAlignment(numericColumn(i) ? .trailing : .leading)
                }
            }
            Divider().gridCellUnsizedAxes(.horizontal)
            ForEach(Array(table.rows.enumerated()), id: \.offset) { _, r in row(r, width: width, bold: false) }
            if let total = table.total, !total.isEmpty {
                Divider().gridCellUnsizedAxes(.horizontal)
                row(total, width: width, bold: true)
            }
        }
        .textSelection(.enabled)
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 8))
    }
    private func row(_ r: [String], width: Int, bold: Bool) -> some View {
        GridRow {
            ForEach(0..<max(width, r.count), id: \.self) { i in
                let c = i < r.count ? r[i] : ""
                Text(c).font(bold ? .t14m : .t14).monospacedDigit()
            }
        }
    }
}

/// Mở chi tiết từ một dòng (profile căn, gợi ý căn cho khách…).
struct OpenSheet: View {
    let api: API
    let request: OpenRequest
    @Environment(\.dismiss) private var dismiss
    @State private var page: Page?
    @State private var error: String?
    /// Đã bấm sang (việc → hồ sơ → …) NGAY TRONG cửa sổ này; rỗng = đang ở thứ mở đầu tiên.
    @State private var trail: [OpenRequest] = []
    private var current: OpenRequest { trail.last ?? request }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                if !trail.isEmpty {
                    Button { trail.removeLast(); Task { await load() } } label: { Label("Quay lại", systemImage: "chevron.left") }
                }
                Text(page?.title ?? current.title).font(.t20)
                Spacer()
                Button("Đóng") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            if let error { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
            switch page {
            case .detail(let d)?: DetailView(page: d, onClose: { dismiss() })
            case .card(let c)?: CardView(page: c, onClose: { dismiss() }, onChanged: { Task { await load() } }, onOpen: { go($0) })
            case .table(let t)?:
                if !t.note.isEmpty { Text(t.note).font(.t12).foregroundStyle(.secondary) }
                if t.rows.isEmpty { ContentUnavailableView(t.empty, systemImage: "tray") }
                else { DataTable(page: t, selection: .constant([]), menu: { _ in EmptyView() }, open: { _ in }) }
            case nil: if error == nil { ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity) }
            }
        }
        .padding(20)
        .sheetFrame(minWidth: 760, minHeight: 540)
        .environment(\.screenPick, nil)   // cửa sổ con không phải màn: biểu đồ trong đó không đặt bộ lọc của màn phía sau
        .task { await load() }
    }

    private func go(_ req: OpenRequest) {
        guard !req.same(current) else { return }
        trail.append(req)
        Task { await load() }
    }

    private func load() async {
        let req = current
        do { page = try await api.query(req.query, req.input); error = nil } catch { self.error = error.localizedDescription }
    }
}

private struct SurfaceIf: ViewModifier {
    let on: Bool
    func body(content: Content) -> some View {
        if on { content.cardSurface(radius: 14) } else { content }
    }
}


/// Lịch (0087): các dòng xếp theo ngày của cột server khai — quá hạn / hôm nay nổi lên; dòng chưa có ngày để cuối.
struct AgendaView: View {
    let rows: [Row]
    let spec: TableViews.CalendarSpec
    let open: (Row) -> Void

    private static let iso: DateFormatter = { let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX"); return f }()
    private static let label: DateFormatter = { let f = DateFormatter(); f.dateFormat = "EEEE, dd/MM/yyyy"; f.locale = Locale(identifier: "vi_VN"); return f }()

    private var days: [(key: String, rows: [Row])] {
        let groups = Dictionary(grouping: rows) { r in String((r.values[spec.date]?.text ?? "").prefix(10)) }
        return groups.keys.sorted { a, b in a.isEmpty ? false : b.isEmpty ? true : a < b }.map { ($0, groups[$0] ?? []) }
    }

    private func header(_ key: String) -> (String, Color) {
        guard let d = Self.iso.date(from: key) else { return ("Chưa đặt ngày", .secondary) }
        let today = Calendar.current.startOfDay(for: .now)
        let text = Self.label.string(from: d).capitalized(with: Locale(identifier: "vi_VN"))
        if Calendar.current.isDate(d, inSameDayAs: today) { return ("Hôm nay · " + text, .accentColor) }
        return d < today ? ("Quá hạn · " + text, .red) : (text, .primary)
    }

    var body: some View {
        List {
            ForEach(days, id: \.key) { day in
                let h = header(day.key)
                Section {
                    ForEach(day.rows) { r in
                        Button { open(r) } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(r.values[spec.title]?.text ?? "").font(.t14m)
                                if let s = spec.subtitle, let v = r.values[s]?.text, !v.isEmpty { Text(v).font(.t12).foregroundStyle(.secondary) }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                } header: {
                    Text(h.0).font(.t12).foregroundStyle(h.1)
                }
            }
        }
    }
}
