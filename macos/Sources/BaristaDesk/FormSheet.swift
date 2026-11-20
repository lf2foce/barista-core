import SwiftUI

/// Form của một lệnh — dựng từ FormSpec server gửi (sinh từ model Pydantic của lệnh). Trường gắn với dòng (`bind`) và
/// giá trị cố định (`preset`) không hiện. Tiền / ngày gửi đúng chữ người gõ ("4,5 tỷ", "05/11/2026") — server hiểu.
struct FormSheet: View {
    let api: API
    let action: ActionSpec
    let row: Row?
    let initial: Input
    let prefetched: [FieldSpec]?
    let embedded: Bool
    let onEdit: ((Input) -> Void)?
    /// Kết quả lệnh (vd mã phòng / tin riêng vừa tạo ⇒ mở luôn) — chỉ gọi khi lệnh chạy xong.
    let onOutput: ((JSON?) -> Void)?
    let onDone: (String) -> Void

    init(api: API, action: ActionSpec, row: Row?, initial: Input = [:], prefetched: [FieldSpec]? = nil, embedded: Bool = false,
         onEdit: ((Input) -> Void)? = nil, submitter: ((Input, String) async throws -> CommandResult)? = nil,
         onOutput: ((JSON?) -> Void)? = nil, onDone: @escaping (String) -> Void) {
        self.onEdit = onEdit
        self.onOutput = onOutput
        self.submitter = submitter
        self.api = api
        self.action = action
        self.row = row
        self.initial = initial
        self.prefetched = prefetched
        self.embedded = embedded
        self.onDone = onDone
    }

    /// Gửi thay cho gọi lệnh trực tiếp (vd thẻ đề xuất của lượt đang dừng — 0049: lệnh chạy qua ``decide``, trợ lý chạy tiếp sau đó).
    let submitter: ((Input, String) async throws -> CommandResult)?
    @Environment(\.dismiss) private var dismiss
    @State private var spec: FormSpec?
    @State private var values: Input = [:]
    @State private var dynamicOptions: [String: [Option]] = [:]
    @State private var more: [String: Bool] = [:]   // danh sách của ô này đã cắt ở giới hạn ⇒ gõ tìm hỏi server
    @State private var touched: Set<String> = []     // ô đã chạm rồi rời đi — chỉ ô này (hoặc sau khi bấm gửi) mới hiện lỗi
    @State private var tried = false
    @State private var chips: [String: Chip] = [:]
    @State private var openGroups: [String: Bool] = [:]
    @State private var optSeq: [String: Int] = [:]
    @State private var didFocus = false
    @FocusState private var focusKey: String?
    /// Trạng thái chip của ô điền sẵn: máy đoán (✨, chờ xem) · đã chấp nhận · đang sửa.
    enum Chip { case guess, ok, edit }
    private let optionsLimit = 100
    @State private var error: String?
    @State private var sending = false
    @State private var key = UUID().uuidString  // chống bấm hai lần / gửi lại khi mạng chập
    /// Lệnh trả link cần mở (tham gia cuộc gọi — 0114): giữ hộp với một nút mở thật thay vì tự bật trình duyệt (giống web ``openLink``).
    @State private var link: (url: URL, host: String, message: String)?
    @Environment(\.openURL) private var openURL

    /// ``once_open_url`` của kết quả lệnh — chỉ https, có tên máy chủ (không mở link lạ khác giao thức).
    static func openLink(_ output: JSON?) -> (url: URL, host: String)? {
        guard case .object(let o) = output, case .string(let raw) = o["once_open_url"], let u = URL(string: raw), u.scheme == "https",
              let host = u.host(), !host.isEmpty else { return nil }
        return (u, host)
    }

    /// ``call_page`` của kết quả (``/goi/<mã>`` + tuỳ chọn ``?auto=1[&video=1]``) và link mở có cùng đường + truy vấn. Không có ``call_page`` ⇒ false.
    static func isOwnCallPage(_ url: URL, _ output: JSON?) -> Bool {
        guard case .object(let o) = output, case .string(let page) = o["call_page"],
              page.range(of: #"^/goi/[0-9a-f-]{36}(\?auto=1(&video=1)?)?$"#, options: .regularExpression) != nil else { return false }
        return url.path + (url.query.map { "?" + $0 } ?? "") == page
    }

    private var fixed: Input {
        var out = action.preset
        if let row { for (field, col) in action.bind { out[field] = row.values[col] ?? .null } }
        return out
    }

    private var visible: [FieldSpec] {
        (spec?.fields ?? []).filter { !$0.hidden && fixed[$0.key] == nil }
    }

    var body: some View {
        if let link { linkView(link) } else { formView }
    }

    private func linkView(_ link: (url: URL, host: String, message: String)) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(link.message, systemImage: "checkmark.circle.fill").font(.t14m).foregroundStyle(Color.ok)
            Button { openURL(link.url) } label: {
                Label("Mở \(link.host)", systemImage: "arrow.up.right.square").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent).controlSize(.large).keyboardShortcut(.defaultAction)
            Text("Mở bằng trình duyệt. Xong việc quay lại đây để bấm Rời hoặc Kết thúc.").font(.t12).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Xong") { onDone(link.message); dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding(20)
        .sheetFrame(minWidth: 380)
    }

    private var visibleKeys: Set<String> { Set(visible.map(\.key)) }
    private var mark: FormUI.Mark { FormUI.markMode(visible) }
    /// Ô bắt buộc còn trống (kể cả ô trong nhóm gấp) — nút gửi mờ tới khi đủ.
    private var missing: [FieldSpec] { visible.filter { $0.required && $0.kind != "bool" && FormUI.isBlank(values[$0.key]) } }
    private var guessKeys: [String] { chips.filter { $0.value == .guess && visibleKeys.contains($0.key) }.map(\.key) }
    private var listsPending: Bool {
        visible.contains { f in
            f.options_query != nil && f.options == nil && dynamicOptions[f.key] == nil
                && !FormUI.optionsParams(f, values.merging(fixed) { a, _ in a }, visible: visibleKeys).waiting
        }
    }

    private var formView: some View {
        let blocked = spec != nil && !missing.isEmpty
        let plain = visible.filter { $0.group == nil }
        let groupNames = Array(Set(visible.compactMap(\.group))).sorted()
        return VStack(alignment: .leading, spacing: 14) {
            if !embedded {
                Text(action.title).font(.t20)
                if let d = spec?.description, !d.isEmpty { Text(d).font(.t12).foregroundStyle(.secondary) }
            }
            if spec == nil && error == nil { ProgressView() }
            if !visible.isEmpty {
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        if guessKeys.count >= 2 {
                            HStack {
                                Label("Máy điền sẵn \(guessKeys.count) ô — xem lại, bấm vào ô để sửa", systemImage: "sparkles").font(.t12).foregroundStyle(.secondary)
                                Spacer()
                                Button("Chấp nhận tất cả") { for k in guessKeys { chips[k] = .ok } }.buttonStyle(.borderless).font(.t12m)
                            }
                            .padding(.horizontal, 10).padding(.vertical, 6)
                            .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Color.accentColor.opacity(0.06)))
                        }
                        fieldRows(plain)
                        // ô hiếm cần (vd dán link Meet / Zoom): gấp lại theo nhóm server khai; tự mở khi có ô đã có giá trị (điền sẵn / sửa), ô bắt buộc còn trống hoặc có lỗi
                        ForEach(groupNames, id: \.self) { g in
                            let inGroup = visible.filter { $0.group == g }
                            let auto = inGroup.contains { f in
                                (f.required && f.kind != "bool" && FormUI.isBlank(values[f.key])) || (!FormUI.isBlank(values[f.key]) && values[f.key] != f.default)
                            }
                            DisclosureGroup(isExpanded: Binding(get: { openGroups[g] ?? auto }, set: { openGroups[g] = $0 })) {
                                VStack(alignment: .leading, spacing: 12) { fieldRows(inGroup) }.padding(.top, 8)
                            } label: {
                                Text("\(g) · \(inGroup.count) ô").font(.t14).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .padding(2)
                }
            } else if spec != nil {
                Text("Không cần điền gì thêm — bấm “\(action.title)” để xác nhận.").font(.t14).foregroundStyle(.secondary)
            }
            if let error {
                Label(error, systemImage: "exclamationmark.triangle.fill").font(.t14).foregroundStyle(.red)
                    .textSelection(.enabled)
            }
            VStack(alignment: .trailing, spacing: 6) {
                if blocked { Text("Cần điền: " + missing.map(\.label).joined(separator: ", ")).font(.t12).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading) }
                HStack {
                    Spacer()
                    if !embedded { Button("Huỷ") { dismiss() }.keyboardShortcut(.cancelAction) }
                    Button(sending ? "Đang gửi…" : action.title) { Task { await submit() } }
                        .keyboardShortcut(.defaultAction)
                        .buttonStyle(.borderedProminent)
                        .tint(action.style == "destructive" ? .red : .accentColor)
                        .disabled(sending || spec == nil || blocked)
                }
            }
        }
        .padding(20)
        .sheetFrame(minWidth: embedded ? nil : 520, idealWidth: embedded ? nil : 560, minHeight: embedded ? nil : 300,
                    idealHeight: embedded ? nil : CGFloat(min(720, 200 + visible.count * 56)))
        .task { await load() }
        .onChange(of: values) { if spec != nil { onEdit?(values) } }
        .onChange(of: focusKey) { old, _ in if let old { touched.insert(old) } }
        .onChange(of: listsPending) { _, pending in focusFirstIfNeeded(pending: pending) }
        .onChange(of: specLoaded) { _, _ in focusFirstIfNeeded(pending: listsPending) }
    }

    private var specLoaded: Bool { spec != nil }

    /// Tự đặt con trỏ vào ô gõ đầu tiên khi form đã vẽ và danh sách chọn đã về (ô chọn đang tải bị khoá).
    private func focusFirstIfNeeded(pending: Bool) {
        guard !didFocus, spec != nil, !pending else { return }
        didFocus = true
        if let f = visible.first, !["select", "multiselect", "bool", "date", "rows"].contains(f.kind), (dynamicOptions[f.key] ?? []).isEmpty, f.options == nil, chips[f.key] == nil { focusKey = f.key }
    }

    /// Hai ô ngắn liền nhau chung một hàng; còn lại một ô một hàng.
    @ViewBuilder
    private func fieldRows(_ list: [FieldSpec]) -> some View {
        ForEach(FormUI.pairs(list), id: \.first!.key) { pair in
            if pair.count == 2 {
                HStack(alignment: .top, spacing: 12) { fieldView(pair[0]); fieldView(pair[1]) }
            } else {
                fieldView(pair[0])
            }
        }
    }

    // MARK: trường

    private func showError(_ f: FieldSpec) -> Bool {
        (tried || touched.contains(f.key)) && f.required && f.kind != "bool" && FormUI.isBlank(values[f.key])
    }

    private func chipable(_ f: FieldSpec) -> Bool { !["textarea", "multiselect", "rows", "bool"].contains(f.kind) }

    /// Chữ hiện cho giá trị đã chọn: nhãn của lựa chọn nếu có; ngày dài; còn lại chính giá trị.
    private func shown(_ f: FieldSpec, _ opts: [Option]?) -> String {
        let t = values[f.key]?.text ?? ""
        if f.kind == "date", let d = FormUI.day(t) { return FormUI.long(d) }
        return opts?.first { $0.value == t }?.label ?? t
    }

    @ViewBuilder
    private func labelView(_ f: FieldSpec) -> some View {
        HStack(spacing: 4) {
            Text(f.label).font(.t12m).foregroundStyle(.secondary)
            if mark == .star && f.required { Text("*").font(.t12m).foregroundStyle(.secondary) }
            if mark == .optional && !f.required && f.kind != "bool" { Text("không bắt buộc").font(.t12).foregroundStyle(.tertiary) }
        }
    }

    @ViewBuilder
    private func fieldView(_ f: FieldSpec) -> some View {
        let opts = f.options ?? dynamicOptions[f.key]
        let ctx = values.merging(fixed) { a, _ in a }
        let waiting = FormUI.optionsParams(f, ctx, visible: visibleKeys).waiting
        let fromQuery = f.options_query != nil && f.options == nil && f.kind != "multiselect" && f.kind != "bool"
        let loading = fromQuery && !waiting && dynamicOptions[f.key] == nil
        let useList = !(opts ?? []).isEmpty || (fromQuery && (waiting || loading))
        let checkbox = f.kind == "bool" && (f.required || f.default != nil)
        VStack(alignment: .leading, spacing: 4) {
            if !checkbox { labelView(f) }
            if let c = chips[f.key], c != .edit, chipable(f) {
                GuessChip(text: shown(f, opts), guessed: c == .guess) { chips[f.key] = .edit; focusKey = f.key }
            } else {
                control(f, opts: opts, useList: useList, fromQuery: fromQuery, waiting: waiting, loading: loading)
            }
            if !f.help.isEmpty { Text(f.help).font(.t12).foregroundStyle(.secondary) }
            if showError(f) { Text(["select", "date"].contains(f.kind) || fromQuery ? "Chưa chọn" : "Chưa nhập").font(.t12).foregroundStyle(.red) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func control(_ f: FieldSpec, opts: [Option]?, useList: Bool, fromQuery: Bool, waiting: Bool, loading: Bool) -> some View {
        let bad = showError(f)
        let hasList = f.kind != "multiselect" && f.kind != "bool" && useList
        if hasList, fromQuery {
            SearchPicker(
                label: f.label, options: opts ?? [], value: values[f.key]?.text ?? "", optional: !f.required, query: f.options_query,
                remote: more[f.key] ?? false,
                search: { term in
                    guard let q = f.options_query else { return [] }
                    let base = FormUI.optionsParams(f, values.merging(fixed) { a, _ in a }, visible: visibleKeys).params
                    return (try? await api.options(q, base.merging(["q": term, "limit": String(optionsLimit)]) { _, n in n })) ?? []
                },
                placeholder: waiting ? waitText(f) : loading ? "Đang tải…" : f.required ? "Chọn…" : "—",
                disabled: waiting || (loading && (opts ?? []).isEmpty), invalid: bad,
                onPick: { v in
                    if let v { values[f.key] = .string(v) } else { values[f.key] = nil }
                    changed(f.key)
                    touched.insert(f.key)
                })
        } else if hasList || f.kind == "select" {
            let list = opts ?? []
            Picker(f.label, selection: stringBinding(f)) {
                if !f.required && !list.contains(where: { $0.value.isEmpty }) { Text("—").tag("") }
                if f.required && (values[f.key]?.text ?? "").isEmpty { Text("Chọn…").tag("") }
                ForEach(list, id: \.value) { Text($0.label).tag($0.value) }
            }
            .labelsHidden().frame(maxWidth: .infinity, alignment: .leading)
        } else {
            switch f.kind {
            case "multiselect":
                VStack(alignment: .leading) {
                    ForEach(opts ?? [], id: \.value) { o in Toggle(o.label, isOn: multiBinding(f.key, o.value)) }
                }
                .padding(8).background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Color.primary.opacity(0.04)))
            case "bool":
                if f.required || f.default != nil {
                    Toggle(f.label, isOn: Binding(get: { values[f.key]?.bool ?? false }, set: { values[f.key] = .bool($0) }))
                } else {
                    Picker(f.label, selection: Binding(
                        get: { values[f.key]?.bool.map { $0 ? "1" : "0" } ?? "" },
                        set: { values[f.key] = $0.isEmpty ? nil : .bool($0 == "1") })) {
                        Text("Không đổi").tag(""); Text("Có").tag("1"); Text("Không").tag("0")
                    }
                    .labelsHidden()
                }
            case "textarea":
                TextEditor(text: stringBinding(f)).font(.t14).frame(minHeight: 70)
                    .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(bad ? Color.red : Color.primary.opacity(0.15), lineWidth: 1))
            case "rows":
                rowsView(f, label: f.label)
            case "date":
                DayField(value: values[f.key]?.text ?? "", placeholder: f.placeholder ?? "Chọn ngày", invalid: bad) { v in
                    values[f.key] = v.map(JSON.string)
                    touched.insert(f.key)
                }
            case "money", "percent", "number":
                HStack(spacing: 4) {
                    TextField(f.label, text: f.kind == "money" ? moneyBinding(f) : stringBinding(f), prompt: Text(placeholder(f))).labelsHidden()
                        .textFieldStyle(.roundedBorder).focused($focusKey, equals: f.key).monospacedDigit()
                    Text(f.kind == "money" ? "đ" : f.kind == "percent" ? "%" : "").font(.t12).foregroundStyle(.secondary)
                }
            default:
                TextField(f.label, text: stringBinding(f), prompt: Text(placeholder(f))).labelsHidden()
                    .textFieldStyle(.roundedBorder).focused($focusKey, equals: f.key)
            }
        }
    }

    private func waitText(_ f: FieldSpec) -> String {
        let src = Array((f.options_args ?? [:]).values) + (f.options_with ?? [])
        if let k = src.first(where: { visibleKeys.contains($0) }), let l = spec?.fields.first(where: { $0.key == k })?.label { return "Chọn \(l.lowercased()) trước" }
        return "Chọn…"
    }

    /// Ô khác phụ thuộc ô này: xoá giá trị (có thể không còn thuộc danh sách mới) + tải lại danh sách theo giá trị mới.
    private func changed(_ key: String) {
        for d in FormUI.dependents(spec?.fields ?? [], of: key) where visibleKeys.contains(d.key) && d.key != key {
            values[d.key] = nil
            chips[d.key] = nil
            dynamicOptions[d.key] = nil
            optSeq[d.key, default: 0] += 1
            Task { await fetchOptions(d) }
        }
    }

    /// Tải danh sách của MỘT ô (chống lượt về trễ: chỉ lượt mới nhất của ô được nhận). Lỗi ⇒ danh sách rỗng (ô gõ chữ như trước).
    private func fetchOptions(_ f: FieldSpec) async {
        guard let q = f.options_query else { return }
        let (params, waiting) = FormUI.optionsParams(f, values.merging(fixed) { a, _ in a }, visible: visibleKeys)
        if waiting { return }
        optSeq[f.key, default: 0] += 1
        let mine = optSeq[f.key] ?? 0
        let rows = (try? await api.options(q, params.merging(["limit": String(optionsLimit)]) { _, n in n })) ?? []
        guard optSeq[f.key] == mine else { return }
        dynamicOptions[f.key] = rows
        more[f.key] = rows.count >= optionsLimit
    }

    // MARK: bảng nhiều dòng (kind "rows") — mỗi dòng một object; ô con vẽ gọn: dòng 1 tên + xoá, dòng 2 các ô còn lại

    private func rows(_ key: String) -> [[String: JSON]] {
        guard case .array(let a)? = values[key] else { return [] }
        return a.map { if case .object(let o) = $0 { return o } else { return [:] } }
    }

    private func setRows(_ key: String, _ r: [[String: JSON]]) { values[key] = .array(r.map { .object($0) }) }

    private func cellBinding(_ key: String, _ i: Int, _ sub: FieldSpec) -> Binding<String> {
        Binding(
            get: { let r = rows(key); return i < r.count ? (r[i][sub.key]?.text ?? "") : "" },
            set: { v in
                var r = rows(key)
                guard i < r.count else { return }
                let t = v.trimmingCharacters(in: .whitespaces)
                if t.isEmpty { r[i][sub.key] = nil }
                else if sub.kind == "number", let n = Double(t) { r[i][sub.key] = .number(n) }
                else { r[i][sub.key] = .string(v) }
                setRows(key, r)
            })
    }

    @ViewBuilder
    private func cellView(_ key: String, _ i: Int, _ sub: FieldSpec) -> some View {
        let opts = sub.options ?? dynamicOptions["\(key).\(sub.key)"] ?? []
        if !opts.isEmpty {
            Picker(sub.label, selection: cellBinding(key, i, sub)) {
                let cur = cellBinding(key, i, sub).wrappedValue
                if cur.isEmpty || !opts.contains(where: { $0.value == cur }) { Text(cur.isEmpty ? "—" : cur).tag(cur) }
                ForEach(opts, id: \.value) { Text($0.label).tag($0.value) }
            }
            .labelsHidden()
            .help(sub.label)
        } else {
            TextField(sub.label, text: cellBinding(key, i, sub), prompt: Text(sub.label + (sub.kind == "date" ? " (dd/mm/yyyy)" : "")))
                .textFieldStyle(.roundedBorder)
        }
    }

    @ViewBuilder
    private func rowsView(_ f: FieldSpec, label: String) -> some View {
        let subs = (f.fields ?? []).filter { !$0.hidden }
        let head = subs.first
        let rest = Array(subs.dropFirst()).filter { $0.kind != "textarea" }   // ghi chú dài để trợ lý / form riêng — dòng gọn
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text(label).font(.t14m)
                Text("\(rows(f.key).count)").font(.t12m).foregroundStyle(.secondary).monospacedDigit()
                    .padding(.horizontal, 7).padding(.vertical, 1).background(Capsule().fill(Color.primary.opacity(0.08)))
            }
            if f.required && rows(f.key).isEmpty && tried {
                Text("Cần ít nhất một dòng").font(.t12).foregroundStyle(.red)
            }
            ForEach(Array(rows(f.key).indices), id: \.self) { i in
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        Text("\(i + 1)").font(.t12m).foregroundStyle(.secondary).frame(width: 18, alignment: .trailing)
                        if let head { cellView(f.key, i, head) }
                        Button { var r = rows(f.key); r.remove(at: i); setRows(f.key, r) } label: { Image(systemName: "minus.circle.fill") }
                            .buttonStyle(.plain).foregroundStyle(.red).help("Bỏ dòng này")
                    }
                    HStack(spacing: 8) {
                        Spacer().frame(width: 18)
                        ForEach(rest) { sub in cellView(f.key, i, sub) }
                    }
                }
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Color.primary.opacity(0.04)))
            }
            Button { setRows(f.key, rows(f.key) + [[:]]) } label: { Label("Thêm dòng", systemImage: "plus.circle.fill") }
                .buttonStyle(.plain).foregroundStyle(Color.accentColor)
        }
    }

    private func placeholder(_ f: FieldSpec) -> String {
        switch f.kind {
        case "money": "VD 4,5 tỷ hoặc 4.500.000.000"
        case "phone": "0912345678"
        case "percent": "VD 2,5"
        default: f.example.map(\.text) ?? ""
        }
    }

    private func stringBinding(_ f: FieldSpec) -> Binding<String> {
        Binding(
            get: { values[f.key]?.text ?? "" },
            set: { v in
                let t = v.trimmingCharacters(in: .whitespaces)
                if t.isEmpty { values[f.key] = nil }
                else if f.kind == "number", let n = Double(t) { values[f.key] = .number(n) }
                else { values[f.key] = .string(v) }
                changed(f.key)
            })
    }

    /// Tiền: gõ số thì gom nghìn bằng dấu chấm; có chữ ("4,5 tỷ") giữ nguyên.
    private func moneyBinding(_ f: FieldSpec) -> Binding<String> {
        Binding(
            get: { FormUI.groupMoney(values[f.key]?.text ?? "") },
            set: { v in
                let g = FormUI.groupMoney(v)
                values[f.key] = g.trimmingCharacters(in: .whitespaces).isEmpty ? nil : .string(g)
            })
    }

    private func multiBinding(_ key: String, _ value: String) -> Binding<Bool> {
        Binding(
            get: {
                if case .array(let a)? = values[key] { return a.contains(.string(value)) }
                return false
            },
            set: { on in
                var a: [JSON] = []
                if case .array(let cur)? = values[key] { a = cur }
                a.removeAll { $0 == .string(value) }
                if on { a.append(.string(value)) }
                values[key] = .array(a)
            })
    }

    // MARK: tải / gửi

    private func load() async {
        do {
            let s: FormSpec
            if let prefetched {
                s = FormSpec(command: action.command, title: action.title, description: "", importable: false, fields: prefetched)
            } else {
                s = try await api.form(action.command)
            }
            // giá trị ban đầu: mặc định của form → điền sẵn từ dòng (form "Sửa") → ô server điền sẵn (``values`` của nút) → giá trị trợ lý đã soạn
            var vals: Input = [:]
            for f in s.fields { if let d = f.default, !d.isNull { vals[f.key] = d } }
            if let row { for (k, col) in action.prefill ?? [:] { if let v = row.values[col], !v.isNull { vals[k] = v } } }
            for (k, v) in action.values ?? [:] where !v.isNull { vals[k] = v }
            for (k, v) in initial where !v.isNull { vals[k] = v }
            // danh sách tuỳ trường khác của form (``options_with`` / ``options_args``, vd mã tin nhắn, dự án): gửi kèm giá trị cố định / điền sẵn
            let known = vals.merging(fixed) { _, n in n }
            let vk = Set(s.fields.filter { !$0.hidden && fixed[$0.key] == nil }.map(\.key))
            var got: [String: [Option]] = [:]
            for f in s.fields {
                let (params, waiting) = FormUI.optionsParams(f, known, visible: vk)
                if f.options == nil, let q = f.options_query, !waiting {
                    got[f.key] = (try? await api.options(q, params.merging(["limit": String(optionsLimit)]) { _, n in n })) ?? []
                }
                for sub in f.fields ?? [] where sub.options == nil {   // ô con của bảng nhiều dòng (vd "Giao cho" xếp theo khối lượng)
                    if let q = sub.options_query, let opts = try? await api.options(q) { got["\(f.key).\(sub.key)"] = opts }
                }
            }
            // chip máy đoán: ô server điền sẵn + ô người gọi truyền vào (không tính ``preset``); hộp trong thẻ đề xuất vẽ ô thường
            var chip: [String: Chip] = [:]
            if !embedded {
                for k in Set((action.values ?? [:]).keys).union(initial.keys.filter { action.preset[$0] == nil }) {
                    if let f = s.fields.first(where: { $0.key == k }), vk.contains(k), chipable(f), !FormUI.isBlank(vals[k]) { chip[k] = .guess }
                }
                // máy đoán: ô bắt buộc còn trống mà danh sách chỉ có ĐÚNG MỘT dòng ⇒ điền luôn (chip ✨, sửa được); điền xong thì ô phụ thuộc nó tải tiếp (tối đa vài vòng)
                for _ in 0..<3 {
                    var picked = false
                    for f in s.fields where vk.contains(f.key) && f.required && FormUI.isBlank(vals[f.key]) && chipable(f) {
                        if let opts = f.options ?? got[f.key], opts.count == 1 { vals[f.key] = .string(opts[0].value); chip[f.key] = .guess; picked = true }
                    }
                    if !picked { break }
                    for f in s.fields where f.options == nil && got[f.key] == nil {
                        guard let q = f.options_query else { continue }
                        let (params, waiting) = FormUI.optionsParams(f, vals.merging(fixed) { _, n in n }, visible: vk)
                        if !waiting { got[f.key] = (try? await api.options(q, params.merging(["limit": String(optionsLimit)]) { _, n in n })) ?? [] }
                    }
                }
            }
            values = vals
            dynamicOptions = got
            more = got.mapValues { $0.count >= optionsLimit }
            chips = chip
            spec = s
            // nút "instant" (vd chọn đáp án) không còn gì để điền ⇒ chạy luôn, không hiện hộp xác nhận rỗng. Nút khác vẫn hỏi xác nhận.
            if action.style == "instant", visible.isEmpty, submitter == nil, !embedded { await submit() }
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func submit() async {
        tried = true
        if !missing.isEmpty {
            error = "Còn thiếu: " + missing.map(\.label).joined(separator: ", ")
            return
        }
        sending = true
        defer { sending = false }
        var input = values.filter { k, _ in visible.contains { $0.key == k } }
        // trường ẨN do trợ lý / nút soạn sẵn (vd lead_id của đề xuất) phải đi theo — người dùng không sửa được nhưng lệnh cần nó
        for f in spec?.fields ?? [] where f.hidden && fixed[f.key] == nil { if let v = values[f.key], !v.isNull { input[f.key] = v } }
        for (k, v) in fixed { input[k] = v }
        do {
            let r: CommandResult
            if let submitter { r = try await submitter(input, key) } else { r = try await api.command(action.command, input, key: key) }
            switch r.status {
            case "ok":
                // lệnh đã ghi nhưng báo điều người phải biết (vd phiếu duyệt hết căn cứ — 0071) ⇒ giữ hộp thoại, hiện câu server trả (như web)
                if case .object(let o) = r.output, case .string(let warning) = o["warning"], !warning.isEmpty {
                    error = warning
                    return
                }
                onOutput?(r.output)
                let msg = r.replayed ? "Đã gửi trước đó — không chạy lại" : "Đã xong: \(action.title)"
                if let open = Self.openLink(r.output), !embedded {
                    // gọi trực tiếp một chạm: trang gọi của CHÍNH bản cài mở ngay (trang tự vào cuộc gọi). Chỉ khi server gửi `call_page` (chỉ cuộc gọi trực tiếp có)
                    // và link khớp đúng đường đó; link ngoài (vd Meet / Zoom dán vào, kể cả có đuôi /goi/) luôn qua hộp "Mở <máy chủ>" cho người xem tên máy.
                    if Self.isOwnCallPage(open.url, r.output) {
                        openCallPage(open.url)   // Mac: trình duyệt mặc định; iPhone: Safari ngay trong app (Platform.swift)
                        onDone(msg)
                        dismiss()
                        return
                    }
                    var summary = msg
                    if case .object(let o) = r.output, case .string(let s) = o["summary"], !s.isEmpty { summary = s }
                    link = (open.url, open.host, summary)
                    return
                }
                onDone(msg)
                dismiss()
            case "pending_approval":
                onDone("Đã gửi phiếu duyệt")
                dismiss()
            default:
                error = r.error.isEmpty ? r.status : r.error
                if r.status == "conflict" || r.status == "invalid" { key = UUID().uuidString }  // sửa rồi gửi lại = yêu cầu mới
            }
        } catch {
            self.error = error.localizedDescription
        }
    }
}
