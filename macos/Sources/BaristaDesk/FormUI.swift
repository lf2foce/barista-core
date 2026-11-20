import SwiftUI

/// Logic THUẦN của lớp vẽ form (agent form D, 18/11/2026) — cùng hành vi với `web/src/lib/form-ui.ts`: đánh dấu bắt buộc, hai cột, ô chọn có tìm
/// (lọc / nhớ lựa chọn gần nhất), ô phụ thuộc, tiền có dấu chấm nghìn. Không tự tính nghiệp vụ; server vẫn là nơi kiểm.
enum FormUI {
    // MARK: bắt buộc

    enum Mark { case none, star, optional }

    /// Hầu hết ô bắt buộc ⇒ ô TUỲ CHỌN mới ghi "không bắt buộc"; ít ô bắt buộc ⇒ dấu * ở ô bắt buộc; đủ cả / không ô nào ⇒ không ghi gì.
    static func markMode(_ visible: [FieldSpec]) -> Mark {
        let all = visible.filter { $0.kind != "bool" && $0.kind != "rows" }
        let req = all.filter(\.required).count
        if req == 0 || req == all.count { return .none }
        return req * 2 > all.count ? .optional : .star
    }

    static func isBlank(_ v: JSON?) -> Bool {
        switch v {
        case nil, .null?: true
        case .string(let s)?: s.trimmingCharacters(in: .whitespaces).isEmpty
        case .array(let a)?: a.isEmpty
        default: false
        }
    }

    // MARK: bố cục

    private static let shortKinds: Set<String> = ["date", "time", "month", "money", "number", "percent", "phone"]

    /// Ô ngắn: hai ô ngắn liền nhau xếp một hàng hai cột.
    static func isShort(_ f: FieldSpec) -> Bool {
        if f.help.count > 56 { return false }
        if shortKinds.contains(f.kind) { return true }
        return f.kind == "select" && f.options_query == nil && (1...6).contains(f.options?.count ?? 0)
    }

    static func pairs(_ fields: [FieldSpec]) -> [[FieldSpec]] {
        var out: [[FieldSpec]] = []
        var i = 0
        while i < fields.count {
            if i + 1 < fields.count, isShort(fields[i]), isShort(fields[i + 1]) { out.append([fields[i], fields[i + 1]]); i += 2 }
            else { out.append([fields[i]]); i += 1 }
        }
        return out
    }

    // MARK: ô chọn có tìm

    /// Chữ thường, bỏ dấu, đ→d — khớp "pham" với "Phạm".
    static func fold(_ s: String) -> String {
        s.lowercased().replacingOccurrences(of: "đ", with: "d").folding(options: .diacriticInsensitive, locale: nil)
    }

    /// Mọi từ có trong nhãn / dòng phụ / giá trị. Hạng: nhãn bắt đầu bằng cả cụm gõ → mọi từ khớp ĐẦU TỪ → khớp giữa từ; cùng hạng giữ thứ tự gốc.
    static func filter(_ opts: [Option], _ q: String) -> [Option] {
        let words = fold(q).split(whereSeparator: \.isWhitespace).map(String.init)
        if words.isEmpty { return opts }
        let first = fold(q.trimmingCharacters(in: .whitespaces))
        var ranked: [(Int, Int, Option)] = []
        for (i, o) in opts.enumerated() {
            let hay = fold("\(o.label) \(o.hint ?? "") \(o.value)")
            guard words.allSatisfy({ hay.contains($0) }) else { continue }
            let rank = fold(o.label).hasPrefix(first) ? 0 : words.allSatisfy({ hay.hasPrefix($0) || hay.contains(" \($0)") || hay.contains("-\($0)") }) ? 1 : 2
            ranked.append((rank, i, o))
        }
        return ranked.sorted { ($0.0, $0.1) < ($1.0, $1.1) }.map(\.2)
    }

    private static let recentMax = 5
    private static func recentKey(_ query: String) -> String { "barista.recent-options.\(query)" }

    /// Lựa chọn gần nhất của một truy vấn chọn (máy này) — mới nhất trước.
    static func recents(_ query: String) -> [Option] {
        guard let raw = UserDefaults.standard.array(forKey: recentKey(query)) as? [[String: String]] else { return [] }
        return raw.compactMap { d in d["value"].flatMap { v in d["label"].map { Option(value: v, label: $0, hint: d["hint"]) } } }
    }

    static func remember(_ query: String, _ o: Option) {
        let next = ([o] + recents(query).filter { $0.value != o.value }).prefix(recentMax)
        UserDefaults.standard.set(next.map { var d = ["value": $0.value, "label": $0.label]; if let h = $0.hint { d["hint"] = h }; return d }, forKey: recentKey(query))
    }

    /// Chưa gõ gì: nhóm "Gần đây" (chỉ dòng còn trong danh sách) lên đầu, phần còn lại giữ thứ tự gốc.
    static func withRecents(_ opts: [Option], _ recent: [Option]) -> (recent: [Option], rest: [Option]) {
        let have = Dictionary(opts.map { ($0.value, $0) }, uniquingKeysWith: { a, _ in a })
        let r = recent.compactMap { have[$0.value] }
        let ids = Set(r.map(\.value))
        return (r, opts.filter { !ids.contains($0.value) })
    }

    // MARK: ô phụ thuộc

    /// Tham số truy vấn chọn → khoá trường của form: ``options_with`` (cùng tên) hợp với ``options_args`` (đổi tên).
    static func optionsArgs(_ f: FieldSpec) -> [String: String] {
        var out = Dictionary(uniqueKeysWithValues: (f.options_with ?? []).map { ($0, $0) })
        for (k, v) in f.options_args ?? [:] { out[k] = v }
        return out
    }

    /// Tham số kèm khi tải danh sách của ô ``f``; ``waiting``: phụ thuộc một ô ĐANG HIỆN mà người chưa chọn ⇒ chưa tải.
    static func optionsParams(_ f: FieldSpec, _ values: [String: JSON], visible: Set<String>) -> (params: [String: String], waiting: Bool) {
        var params: [String: String] = [:]
        var waiting = false
        for (param, key) in optionsArgs(f) {
            if let v = values[key], !v.isNull, !v.text.isEmpty { params[param] = v.text }
            else if visible.contains(key) { waiting = true }
        }
        return (params, waiting)
    }

    static func dependents(_ fields: [FieldSpec], of key: String) -> [FieldSpec] {
        fields.filter { $0.options_query != nil && optionsArgs($0).values.contains(key) }
    }

    // MARK: tiền / ngày

    /// Chỉ chữ số / dấu chấm ⇒ gom nghìn bằng dấu chấm; có chữ ("4,5 tỷ") hoặc dấu phẩy ⇒ giữ nguyên chữ người gõ (server hiểu đơn vị).
    static func groupMoney(_ raw: String) -> String {
        let t = raw.filter { !$0.isWhitespace }
        guard !t.isEmpty, t.allSatisfy({ $0.isNumber || $0 == "." }) else { return raw }
        var digits = String(t.filter(\.isNumber))
        while digits.count > 1, digits.hasPrefix("0") { digits.removeFirst() }
        var out = ""
        for (i, ch) in digits.reversed().enumerated() {
            if i > 0, i % 3 == 0 { out.append(".") }
            out.append(ch)
        }
        return String(out.reversed())
    }

    static let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    /// "yyyy-MM-dd" hoặc "dd/MM/yyyy" → ngày.
    static func day(_ s: String) -> Date? {
        if let d = isoDay.date(from: s) { return d }
        let p = s.split(separator: "/").compactMap { Int($0) }
        guard p.count == 3 else { return nil }
        return Calendar(identifier: .gregorian).date(from: DateComponents(year: p[2], month: p[1], day: p[0]))
    }

    /// Ngày không nhập nhằng: "1 tháng 12, 2026" (ADR 0225).
    static func long(_ d: Date) -> String {
        let c = Calendar(identifier: .gregorian).dateComponents([.year, .month, .day], from: d)
        return "\(c.day ?? 0) tháng \(c.month ?? 0), \(c.year ?? 0)"
    }
}

// MARK: - Ô chọn có tìm

/// Ô chọn có tìm cho ``options_query`` (khách, căn, người…): bấm ⇒ danh sách có ô gõ tìm (bỏ dấu, nhiều từ), nhãn + dòng phụ; ↑ ↓ Enter chọn, Esc đóng;
/// lựa chọn gần nhất lên đầu. Danh sách chưa đủ (``remote``) ⇒ gõ là hỏi lại server (``search``).
struct SearchPicker: View {
    let label: String
    let options: [Option]
    let value: String
    var optional = false
    var query: String? = nil
    var remote = false
    var search: ((String) async -> [Option])? = nil
    var placeholder = "Chọn…"
    var disabled = false
    var invalid = false
    let onPick: (String?) -> Void

    @State private var open = false
    @State private var q = ""
    @State private var active = 0
    @State private var found: [Option]?
    @State private var extra: Option?
    @FocusState private var searching: Bool

    private var searchable: Bool { options.count >= 8 || remote }
    private var current: Option? { options.first { $0.value == value } ?? (extra?.value == value ? extra : nil) }

    private var shown: (recent: [Option], rest: [Option]) {
        if !q.trimmingCharacters(in: .whitespaces).isEmpty { return ([], remote ? (found ?? FormUI.filter(options, q)) : FormUI.filter(options, q)) }
        return FormUI.withRecents(options, options.count >= 6 ? (query.map(FormUI.recents) ?? []) : [])
    }
    private var items: [Option] {
        let s = shown
        return (optional && value != "" && q.isEmpty ? [Option(value: "", label: "Bỏ chọn")] : []) + s.recent + s.rest
    }

    var body: some View {
        Button { open = true } label: {
            HStack(spacing: 6) {
                if let c = current {
                    Text(c.label).font(.t14).lineLimit(1)
                    if let h = c.hint { Text(h).font(.t12).foregroundStyle(.secondary).lineLimit(1) }
                } else {
                    Text(value.isEmpty ? placeholder : value).font(.t14).foregroundStyle(value.isEmpty ? .secondary : .primary).lineLimit(1)
                }
                Spacer(minLength: 4)
                Image(systemName: "chevron.up.chevron.down").font(.system(size: 10)).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 8).frame(minHeight: 28)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Color.primary.opacity(0.04)))
            .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(invalid ? Color.red : Color.primary.opacity(0.15), lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.5 : 1)
        .accessibilityLabel(label)
        .popover(isPresented: $open, arrowEdge: .bottom) { list }
        .task(id: value) {   // giá trị có sẵn mà danh sách đã cắt không có ⇒ hỏi server nhãn của nó
            guard !value.isEmpty, remote, let search, !options.contains(where: { $0.value == value }) else { return }
            extra = await search(value).first { $0.value == value }
        }
    }

    private var list: some View {
        let rows = items
        let s = shown
        let offset = rows.count - s.recent.count - s.rest.count
        return VStack(alignment: .leading, spacing: 4) {
            if searchable {
                HStack(spacing: 6) {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    TextField("Gõ để tìm…", text: $q).textFieldStyle(.plain).font(.t14).focused($searching)
                        .onSubmit { if rows.indices.contains(active) { pick(rows[active]) } }
                }
                .padding(.horizontal, 6).padding(.bottom, 4)
                Divider()
            }
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 1) {
                        if rows.count == offset { Text(q.trimmingCharacters(in: .whitespaces).isEmpty ? "Chưa có lựa chọn nào" : "Không có “\(q)”").font(.t14).foregroundStyle(.secondary).padding(8) }
                        if offset == 1 { row(rows[0], 0) }
                        if !s.recent.isEmpty { Text("Gần đây").font(.t12m).foregroundStyle(.secondary).padding(.horizontal, 8).padding(.top, 4) }
                        ForEach(Array(s.recent.enumerated()), id: \.element.value) { i, o in row(o, offset + i) }
                        if !s.recent.isEmpty && !s.rest.isEmpty { Text("Tất cả").font(.t12m).foregroundStyle(.secondary).padding(.horizontal, 8).padding(.top, 4) }
                        ForEach(Array(s.rest.enumerated()), id: \.element.value) { i, o in row(o, offset + s.recent.count + i) }
                    }
                }
                .frame(maxHeight: 260)
                .onChange(of: active) { _, a in proxy.scrollTo(a, anchor: nil) }
            }
        }
        .padding(8).frame(minWidth: 280, idealWidth: 340)
        .onKeyPress(.downArrow) { active = min(active + 1, max(rows.count - 1, 0)); return .handled }
        .onKeyPress(.upArrow) { active = max(active - 1, 0); return .handled }
        .onKeyPress(.return) { if rows.indices.contains(active) { pick(rows[active]) }; return .handled }
        .onAppear { q = ""; active = 0; found = nil; searching = true }
        .onChange(of: q) { _, _ in active = 0; found = nil }
        .task(id: q) {   // gõ ⇒ hỏi lại server sau một nhịp (danh sách chưa đủ)
            guard remote, let search, !q.trimmingCharacters(in: .whitespaces).isEmpty else { return }
            try? await Task.sleep(for: .milliseconds(250))
            guard !Task.isCancelled else { return }
            found = await search(q.trimmingCharacters(in: .whitespaces))
        }
    }

    private func row(_ o: Option, _ i: Int) -> some View {
        Button { pick(o) } label: {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(o.label).font(.t14).lineLimit(1)
                    if let h = o.hint { Text(h).font(.t12).foregroundStyle(.secondary).lineLimit(1) }
                }
                Spacer(minLength: 4)
                if !o.value.isEmpty, o.value == value { Image(systemName: "checkmark").foregroundStyle(Color.accentColor) }
            }
            .padding(.horizontal, 8).padding(.vertical, 5)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(i == active ? Color.primary.opacity(0.09) : .clear))
            .foregroundStyle(o.value.isEmpty ? Color.secondary : Color.primary)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .id(i)
        .onHover { if $0 { active = i } }
    }

    private func pick(_ o: Option) {
        if o.value.isEmpty { onPick(nil) } else { onPick(o.value); if let query { FormUI.remember(query, o) } }
        open = false
    }
}

// MARK: - Ô chọn ngày

/// Ô chọn ngày: hiện "1 tháng 12, 2026" (không nhập nhằng); bấm ⇒ lịch. Gửi ISO yyyy-MM-dd (lõi nhận cả hai).
struct DayField: View {
    let value: String
    var placeholder = "Chọn ngày"
    var invalid = false
    let onChange: (String?) -> Void
    @State private var open = false

    var body: some View {
        let picked = FormUI.day(value)
        Button { open = true } label: {
            HStack {
                Text(picked.map(FormUI.long) ?? placeholder).font(.t14).foregroundStyle(picked == nil ? .secondary : .primary).lineLimit(1)
                Spacer(minLength: 4)
                Image(systemName: "calendar").font(.system(size: 12)).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 8).frame(minHeight: 28)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Color.primary.opacity(0.04)))
            .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(invalid ? Color.red : Color.primary.opacity(0.15), lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .popover(isPresented: $open, arrowEdge: .bottom) {
            VStack(spacing: 6) {
                DatePicker("", selection: Binding(get: { picked ?? Date() }, set: { onChange(FormUI.isoDay.string(from: $0)); open = false }), displayedComponents: .date)
                    .datePickerStyle(.graphical).labelsHidden().environment(\.locale, Locale(identifier: "vi_VN"))
                HStack {
                    Button("Hôm nay") { onChange(FormUI.isoDay.string(from: Date())); open = false }
                    Spacer()
                    if picked != nil { Button("Xoá") { onChange(nil); open = false }.foregroundStyle(.secondary) }
                }
                .buttonStyle(.borderless).font(.t12m)
            }
            .padding(10).frame(width: 260)
        }
    }
}

// MARK: - Chip điền sẵn

/// Giá trị máy đoán / điền sẵn: một dòng chip sửa được (✨ = máy đoán chưa xem; ✓ = đã chấp nhận). Bấm ⇒ sửa.
struct GuessChip: View {
    let text: String
    let guessed: Bool
    let onEdit: () -> Void
    var body: some View {
        Button(action: onEdit) {
            HStack(spacing: 6) {
                Image(systemName: guessed ? "sparkles" : "checkmark").font(.system(size: 11)).foregroundStyle(guessed ? Color.accentColor : Color.ok)
                Text(text).font(.t14).lineLimit(1)
                Spacer(minLength: 4)
                Image(systemName: "pencil").font(.system(size: 11)).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 8).frame(minHeight: 28)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(guessed ? Color.accentColor.opacity(0.07) : Color.primary.opacity(0.04)))
            .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(guessed ? Color.accentColor.opacity(0.3) : Color.primary.opacity(0.15), lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("Bấm để sửa")
    }
}
