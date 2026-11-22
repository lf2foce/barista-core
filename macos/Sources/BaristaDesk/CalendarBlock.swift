import SwiftUI

// KHỐI LỊCH (kind "calendar" — 0240, cùng hợp đồng với web/src/lib/types.ts ``CalendarData``): server khai mọi thứ (ngày, người, mục, màu, cách tạo, đi ngày);
// app chỉ vẽ. Khung rộng (≥ 560 điểm): lưới giờ — Ngày mỗi người một cột, Tuần mỗi ngày một cột; khung hẹp (iPhone dọc) và "Danh sách": danh sách theo ngày.
// Đi ngày / đổi chế độ xem / "lịch của ai" = đặt bộ lọc của màn (``screenPick``) rồi tải lại — như web. Thêm lịch: gõ một câu ⇒ máy điền form tạo lịch (ô ✨),
// hoặc mở form trống. Kéo thả dời lịch chỉ có trên web (bản này: bấm mục ⇒ mở hồ sơ / xem chi tiết).

struct CalendarData: Decodable, Hashable {
    struct Option: Decodable, Hashable { let key, title: String }
    struct Pick: Decodable, Hashable { let param, current: String; let options: [Option] }
    struct Nav: Decodable, Hashable {
        let param_view, param_on, prev, next: String
        let views: [Option]
        let who: Pick?
        let unit: Pick?
    }
    struct Day: Decodable, Hashable { let on, label: String }
    struct Lane: Decodable, Hashable { let key, title: String; let subtitle: String?; let me: Bool? }
    struct Kind: Decodable, Hashable { let key, label, tone: String }
    struct Item: Decodable, Hashable, Identifiable {
        let id, lane, title, start, end: String
        let subtitle: String?
        let all_day: Bool?
        let kind, tone, status: String?
        let busy: Bool?
        let open: CardPage.Open?
        var day: String { String(start.prefix(10)) }
        var allDay: Bool { all_day ?? (start.count <= 10) }
    }
    struct Creator: Decodable, Hashable {
        let key, label, command: String
        let tone: String?
        let preset: [String: JSON]?
        let fields: [String: String]?
    }
    struct Create: Decodable, Hashable { let creators: [Creator]; let understand: String? }

    let title, view, start, end, today, now: String
    let hours: [Int]
    let days: [Day]
    let lanes: [Lane]
    let kinds: [Kind]
    let items: [Item]
    let nav: Nav?
    let create: Create?
    let empty, note: String

    private enum K: String, CodingKey { case title, view, start, end, today, now, hours, days, lanes, kinds, items, nav, create, empty, note }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        view = (try? c.decode(String.self, forKey: .view)) ?? "day"
        start = try c.decode(String.self, forKey: .start)
        end = (try? c.decode(String.self, forKey: .end)) ?? start
        today = (try? c.decode(String.self, forKey: .today)) ?? ""
        now = (try? c.decode(String.self, forKey: .now)) ?? ""
        let h = (try? c.decode([Int].self, forKey: .hours)) ?? []
        hours = h.count == 2 && h[0] < h[1] ? h : [7, 21]
        days = (try? c.decode([Day].self, forKey: .days)) ?? []
        lanes = (try? c.decode([Lane].self, forKey: .lanes)) ?? []
        kinds = (try? c.decode([Kind].self, forKey: .kinds)) ?? []
        items = (try? c.decode([Item].self, forKey: .items)) ?? []
        nav = try? c.decodeIfPresent(Nav.self, forKey: .nav)
        create = try? c.decodeIfPresent(Create.self, forKey: .create)
        empty = (try? c.decode(String.self, forKey: .empty)) ?? "Chưa có lịch."
        note = (try? c.decode(String.self, forKey: .note)) ?? ""
    }

    /// Phút trong ngày của "yyyy-mm-ddTHH:MM…" (giờ công ty server đã đổi sẵn — không đọc đồng hồ / múi máy).
    static func minutes(_ iso: String) -> Int {
        let s = Array(iso)
        guard s.count >= 16, let h = Int(String(s[11...12])), let m = Int(String(s[14...15])) else { return 0 }
        return h * 60 + m
    }

    static func hhmm(_ m: Int) -> String { String(format: "%02d:%02d", max(0, m) / 60, max(0, m) % 60) }

    /// Khoảng phút của mục trong ``day`` (mục qua đêm bị cắt ở 24:00).
    func span(_ i: Item, on day: String) -> (Int, Int) {
        let s = i.day < day ? 0 : Self.minutes(i.start)
        let e = String(i.end.prefix(10)) > day ? 24 * 60 : Self.minutes(i.end)
        return (s, max(e, s + 15))
    }

    /// Đầu vào lệnh tạo lịch từ ngày / giờ / tiêu đề (như web ``createPayload``): tên trường do server khai ở ``fields`` của cách tạo.
    static func payload(_ c: Creator, day: String, start: Int, end: Int, title: String) -> [String: JSON] {
        let f = c.fields ?? [:]
        var out: [String: JSON] = [:]
        if let k = f["day"] { out[k] = .string(day) }
        if let k = f["start"] { out[k] = .string(hhmm(start)) }
        if let k = f["end"] { out[k] = .string(hhmm(end)) }
        if let k = f["minutes"] { out[k] = .number(Double(max(0, end - start))) }
        if let k = f["title"], !title.trimmingCharacters(in: .whitespaces).isEmpty { out[k] = .string(title) }
        return out
    }
}

struct CalendarBlockView: View {
    let block: CalendarData
    let onOpen: (CardPage.Open) -> Void
    /// Mở form tạo lịch (ô máy đoán là chip ✨ — ``ActionSpec.values``).
    let onCreate: (ActionSpec) -> Void
    @Environment(\.screenPick) private var pick
    @State private var width: CGFloat = 0
    @State private var adding = false
    @State private var shown: CalendarData.Item?

    private var canNav: Bool { pick != nil && block.nav != nil }
    private var creators: [CalendarData.Creator] { block.create?.creators ?? [] }
    private var grid: Bool { block.view != "agenda" && width >= BlockLayout.wide }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            toolbar
            if !block.note.isEmpty || !usedKinds.isEmpty { legend }
            if grid { CalendarGrid(block: block, columns: columns, tap: tap) } else { CalendarList(block: block, tap: tap) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .sheet(isPresented: $adding) { CalendarAddSheet(block: block) { adding = false; if let a = $0 { onCreate(a) } } }
        .sheet(item: $shown) { i in CalendarItemSheet(block: block, item: i) }
    }

    private func tap(_ i: CalendarData.Item) { if let o = i.open { onOpen(o) } else { shown = i } }

    // MARK: thanh công cụ — một hàng: đi ngày · tên · lịch của ai · chế độ xem · Thêm lịch (hẹp ⇒ tự xuống dòng)

    @ViewBuilder private var toolbar: some View {
        FlowRow(spacing: 8) {
            if canNav, let nav = block.nav {
                let isToday = block.today >= block.start && block.today <= block.end
                Button("Hôm nay") { pick?.set(nav.param_on, nil) }.disabled(isToday && block.view != "agenda")
                ControlGroup {
                    Button { pick?.set(nav.param_on, nav.prev) } label: { Image(systemName: "chevron.left") }.accessibilityLabel("Trước")
                    Button { pick?.set(nav.param_on, nav.next) } label: { Image(systemName: "chevron.right") }.accessibilityLabel("Sau")
                }
                .fixedSize()
            }
            Text(block.title).font(.t16).lineLimit(1)
            if canNav, let nav = block.nav {
                if let who = nav.who {
                    Picker("Lịch của", selection: Binding(get: { who.current }, set: { pick?.set(who.param, $0) })) {
                        ForEach(who.options, id: \.key) { Text($0.title).tag($0.key) }
                    }
                    .pickerStyle(.segmented).labelsHidden().fixedSize()
                    if who.current != "me", let unit = nav.unit {
                        Picker("Đơn vị", selection: Binding(get: { unit.current }, set: { pick?.set(unit.param, $0.isEmpty ? nil : $0) })) {
                            ForEach(unit.options, id: \.key) { Text($0.title).tag($0.key) }
                        }
                        .pickerStyle(.menu).labelsHidden().fixedSize()
                    }
                }
                Picker("Xem theo", selection: Binding(get: { block.view }, set: { pick?.set(nav.param_view, $0) })) {
                    ForEach(nav.views, id: \.key) { Text($0.title).tag($0.key) }
                }
                .pickerStyle(.segmented).labelsHidden().fixedSize()
            }
            if !creators.isEmpty {
                Button { adding = true } label: { Label("Thêm lịch", systemImage: "plus") }.buttonStyle(.borderedProminent)
            }
        }
        .controlSize(.regular)
    }

    private var usedKinds: [CalendarData.Kind] {
        let used = Set(block.items.compactMap(\.kind))
        return block.kinds.filter { used.contains($0.key) }
    }

    private var legend: some View {
        FlowRow(spacing: 12) {
            ForEach(usedKinds, id: \.key) { k in
                HStack(spacing: 5) { Circle().fill(Color.named(k.tone) ?? .accentColor).frame(width: 8, height: 8); Text(k.label) }
                    .font(.t12).foregroundStyle(.secondary)
            }
            if !block.note.isEmpty { Text(block.note).font(.t12).foregroundStyle(.secondary) }
        }
    }

    /// Cột lưới: Ngày có người ⇒ mỗi người một cột; còn lại mỗi ngày một cột.
    private var columns: [CalendarGrid.Column] {
        if block.view == "day", !block.lanes.isEmpty {
            return block.lanes.map { .init(id: $0.key, title: $0.title, subtitle: $0.subtitle ?? "", day: block.start, lane: $0.key) }   // tên / phụ đề do server đặt (vd "Tôi" · tên)
        }
        return block.days.map { .init(id: $0.on, title: $0.label, subtitle: $0.on == block.today ? "Hôm nay" : "", day: $0.on, lane: nil) }
    }
}

// MARK: - Lưới giờ

struct CalendarGrid: View {
    struct Column: Identifiable { let id, title, subtitle, day: String; let lane: String? }
    let block: CalendarData
    let columns: [Column]
    let tap: (CalendarData.Item) -> Void
    private let hour: CGFloat = 48
    private let gutter: CGFloat = 52

    private var h0: Int { block.hours[0] }
    private var h1: Int { block.hours[1] }
    private func y(_ m: Int) -> CGFloat { CGFloat(m - h0 * 60) * hour / 60 }

    private func items(_ c: Column) -> [CalendarData.Item] {
        block.items.filter { i in
            (c.lane == nil || i.lane == c.lane) && i.day <= c.day && String(i.end.prefix(10)) >= c.day
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                Color.clear.frame(width: gutter)
                ForEach(columns) { c in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(c.title).font(.t12m).foregroundStyle(c.day == block.today && c.lane == nil ? Color.accentColor : .primary).lineLimit(1)
                        if !c.subtitle.isEmpty { Text(c.subtitle).font(.t12).foregroundStyle(.secondary).lineLimit(1) }
                    }
                    .padding(.horizontal, 8).padding(.vertical, 8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .overlay(alignment: .leading) { Rectangle().fill(Color.hairline).frame(width: 1) }
                }
            }
            allDayRow
            Divider()
            ScrollView(.vertical) {
                HStack(alignment: .top, spacing: 0) {
                    VStack(spacing: 0) {
                        ForEach(h0..<h1, id: \.self) { h in
                            Text(String(format: "%02d:00", h)).font(.t12).foregroundStyle(.secondary)
                                .frame(width: gutter - 8, height: hour, alignment: .topTrailing).offset(y: -7)
                        }
                    }
                    .frame(width: gutter)
                    ForEach(columns) { c in column(c) }
                }
                .padding(.top, 8)
            }
            .frame(minHeight: 320, maxHeight: 620)
        }
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.hairline))
        .overlay {
            if !block.items.contains(where: { !$0.allDay }) && !block.items.contains(where: \.allDay) {
                Text(block.empty).font(.t14).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    .padding(14).frame(maxWidth: 360)
                    .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.surface.opacity(0.95)))
            }
        }
    }

    @ViewBuilder private var allDayRow: some View {
        if block.items.contains(where: \.allDay) {
            HStack(alignment: .top, spacing: 0) {
                Text("Cả ngày").font(.t12).foregroundStyle(.secondary).frame(width: gutter - 8, alignment: .trailing).padding(.trailing, 8)
                ForEach(columns) { c in
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(items(c).filter(\.allDay)) { i in
                            Button { tap(i) } label: { chip(i) }.buttonStyle(.plain)
                        }
                    }
                    .padding(4).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(.vertical, 4)
        }
    }

    private func chip(_ i: CalendarData.Item) -> some View {
        Text(i.busy == true ? "Bận" : i.title).font(.t12m).lineLimit(1)
            .padding(.horizontal, 6).padding(.vertical, 3)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(CalendarList.color(i).opacity(0.18)))
    }

    private func column(_ c: Column) -> some View {
        let timed = items(c).filter { !$0.allDay }
        let slots = Self.slots(timed.map { ($0.id, block.span($0, on: c.day)) })
        return GeometryReader { g in
            ZStack(alignment: .topLeading) {
                ForEach(h0..<h1, id: \.self) { h in
                    Rectangle().fill(Color.hairline).frame(height: 1).offset(y: y(h * 60))
                }
                if c.day == block.today {
                    let n = CalendarData.minutes(block.now)
                    if n >= h0 * 60 && n <= h1 * 60 { Rectangle().fill(Color.bad).frame(height: 2).offset(y: y(n)) }
                }
                ForEach(timed) { i in
                    let (s, e) = block.span(i, on: c.day)
                    let (slot, of) = slots[i.id] ?? (0, 1)
                    let w = (g.size.width - 4) / CGFloat(of)
                    Button { tap(i) } label: { box(i, s, e) }
                        .buttonStyle(.plain)
                        .frame(width: max(0, w - 2), height: max(20, y(e) - y(s) - 2))
                        .offset(x: 2 + CGFloat(slot) * w, y: y(max(s, h0 * 60)))
                }
            }
        }
        .frame(height: CGFloat(h1 - h0) * hour)
        .frame(maxWidth: .infinity)
        .overlay(alignment: .leading) { Rectangle().fill(Color.hairline).frame(width: 1) }
    }

    private func box(_ i: CalendarData.Item, _ s: Int, _ e: Int) -> some View {
        let color = CalendarList.color(i)
        return VStack(alignment: .leading, spacing: 1) {
            Text(i.busy == true ? "Bận" : i.title).font(.t12m).lineLimit(2)
            Text("\(CalendarData.hhmm(s))–\(CalendarData.hhmm(e))").font(.t12).foregroundStyle(.secondary).lineLimit(1)
        }
        .padding(.horizontal, 6).padding(.vertical, 3)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 6, style: .continuous).fill(color.opacity(i.status == "tentative" ? 0.08 : 0.18)))
        .overlay(alignment: .leading) { RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 3) }
        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
    }

    /// Mục chồng giờ chia nhau bề ngang: cụm mục chồng nhau ⇒ mỗi mục một rãnh (rãnh trống đầu tiên), cả cụm chung số rãnh.
    static func slots(_ xs: [(String, (Int, Int))]) -> [String: (Int, Int)] {
        var out: [String: (Int, Int)] = [:]
        var cluster: [(String, Int)] = []
        var ends: [Int] = []
        var clusterEnd = -1
        func flush() { for (id, s) in cluster { out[id] = (s, max(1, ends.count)) }; cluster = []; ends = [] }
        for (id, (s, e)) in xs.sorted(by: { $0.1.0 < $1.1.0 }) {
            if s >= clusterEnd { flush(); clusterEnd = e } else { clusterEnd = max(clusterEnd, e) }
            if let k = ends.firstIndex(where: { $0 <= s }) { ends[k] = e; cluster.append((id, k)) } else { ends.append(e); cluster.append((id, ends.count - 1)) }
        }
        flush()
        return out
    }
}

// MARK: - Danh sách theo ngày (khung hẹp / "Danh sách")

struct CalendarList: View {
    let block: CalendarData
    let tap: (CalendarData.Item) -> Void

    static func color(_ i: CalendarData.Item) -> Color { i.busy == true ? .gray : (Color.named(i.tone) ?? .accentColor) }

    private var groups: [(CalendarData.Day, [CalendarData.Item])] {
        block.days.map { d in
            (d, block.items.filter { $0.day <= d.on && String($0.end.prefix(10)) >= d.on }
                .sorted { ($0.allDay ? -1 : CalendarData.minutes($0.start)) < ($1.allDay ? -1 : CalendarData.minutes($1.start)) })
        }
        .filter { !$0.1.isEmpty || $0.0.on == block.today }
    }

    var body: some View {
        if block.items.isEmpty {
            Text(block.empty).font(.t14).foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(.vertical, 24)
                .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface))
        } else {
            VStack(alignment: .leading, spacing: 14) {
                ForEach(groups, id: \.0.on) { d, xs in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(d.on == block.today ? "Hôm nay · \(d.label)" : d.label).font(.t12m)
                            .foregroundStyle(d.on == block.today ? Color.accentColor : .secondary)
                        if xs.isEmpty {
                            Text("Chưa có lịch.").font(.t12).foregroundStyle(.secondary)
                        } else {
                            VStack(spacing: 0) {
                                ForEach(Array(xs.enumerated()), id: \.element.id) { n, i in
                                    if n > 0 { Divider() }
                                    Button { tap(i) } label: { row(i, on: d.on) }.buttonStyle(.plain)
                                }
                            }
                            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.surface))
                        }
                    }
                }
            }
        }
    }

    private func row(_ i: CalendarData.Item, on day: String) -> some View {
        let (s, e) = block.span(i, on: day)
        return HStack(alignment: .top, spacing: 10) {
            RoundedRectangle(cornerRadius: 2).fill(Self.color(i)).frame(width: 4).frame(maxHeight: .infinity)
            Text(i.allDay ? "Cả ngày" : "\(CalendarData.hhmm(s))\n\(CalendarData.hhmm(e))").font(.t12).monospacedDigit()
                .foregroundStyle(.secondary).frame(width: 46, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text(i.busy == true ? "Bận" : i.title).font(.t14m).lineLimit(2)
                let lane = block.lanes.count > 1 ? block.lanes.first { $0.key == i.lane }?.title : nil
                let sub = [i.subtitle, lane].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
                if !sub.isEmpty { Text(sub).font(.t12).foregroundStyle(.secondary).lineLimit(2) }
            }
            Spacer(minLength: 0)
            if i.open != nil { Image(systemName: "chevron.right").font(.t12).foregroundStyle(.tertiary) }
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .contentShape(Rectangle())
    }
}

// MARK: - Thêm lịch: gõ một câu ⇒ máy điền form; hoặc mở form trống

private struct CalendarAddSheet: View {
    let block: CalendarData
    let done: (ActionSpec?) -> Void
    @Environment(Desk.self) private var desk: Desk?
    @State private var said = ""
    @State private var thinking = false
    @State private var error = ""

    private var creators: [CalendarData.Creator] { block.create?.creators ?? [] }

    var body: some View {
        NavigationStack {
            Form {
                if let understand = block.create?.understand, !understand.isEmpty {
                    Section {
                        TextField("Thứ 5 2h chiều gặp khách", text: $said)
                            .onSubmit { Task { await fill(understand) } }
                        Button { Task { await fill(understand) } } label: {
                            HStack { if thinking { ProgressView().controlSize(.small) }; Text("Điền hộ") }
                        }
                        .disabled(said.trimmingCharacters(in: .whitespaces).isEmpty || thinking)
                        if !error.isEmpty { Text(error).font(.t12).foregroundStyle(Color.bad) }
                    } header: { Text("Gõ một câu, máy điền hộ") } footer: { Text("Máy điền ngày, giờ, tiêu đề vào form — anh/chị xem lại rồi Lưu.") }
                }
                Section("Hoặc tự điền") {
                    ForEach(creators, id: \.key) { c in
                        Button {
                            let s = defaultStart
                            done(spec(c, values: CalendarData.payload(c, day: defaultDay, start: s, end: s + 60, title: "")))
                        } label: {
                            HStack { Circle().fill(Color.named(c.tone) ?? .accentColor).frame(width: 8, height: 8); Text(c.label) }
                        }
                    }
                }
            }
            .formStyle(.grouped)
            .navigationTitle("Thêm lịch")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Đóng") { done(nil) } } }
        }
        #if os(macOS)
        .frame(minWidth: 420, minHeight: 340)
        #endif
    }

    private var defaultDay: String { block.today >= block.start && block.today <= block.end ? block.today : block.start }
    /// Hôm nay ⇒ giờ tròn kế tiếp (≤ 22:00 để giờ kết thúc còn trong ngày); ngày khác ⇒ 9:00.
    private var defaultStart: Int {
        defaultDay == block.today ? min(22 * 60, (CalendarData.minutes(block.now) / 60 + 1) * 60) : 9 * 60
    }

    private func spec(_ c: CalendarData.Creator, values: [String: JSON]) -> ActionSpec {
        ActionSpec(command: c.command, title: c.label, style: "primary", bind: [:], preset: c.preset ?? [:], when: [:], importable: false, values: values)
    }

    private func fill(_ understand: String) async {
        guard let desk, !said.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        thinking = true; error = ""
        defer { thinking = false }
        do {
            let g: [String: JSON] = try await desk.api.data(understand, ["text": .string(said), "on": .string(block.start)])
            let c = creators.first { $0.key == g["creator"]?.text } ?? creators.first
            guard let c else { return }
            let (h, m) = { () -> (Int, Int) in
                let p = (g["start"]?.text ?? "09:00").split(separator: ":").compactMap { Int($0) }
                return (p.first ?? 9, p.count > 1 ? p[1] : 0)
            }()
            let s = h * 60 + m
            let len: Int = { if case .number(let n) = g["minutes"] ?? .null, n > 0 { return Int(n) }; return 60 }()
            done(spec(c, values: CalendarData.payload(c, day: g["day"]?.text ?? defaultDay, start: s, end: s + len, title: g["title"]?.text ?? "")))
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Mục không có hồ sơ để mở (lịch riêng, "Bận"): xem nhanh giờ + ghi chú.
private struct CalendarItemSheet: View {
    let block: CalendarData
    let item: CalendarData.Item
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let (s, e) = block.span(item, on: item.day)
        NavigationStack {
            Form {
                Section {
                    LabeledContent("Ngày", value: block.days.first { $0.on == item.day }?.label ?? item.day)
                    LabeledContent("Giờ", value: item.allDay ? "Cả ngày" : "\(CalendarData.hhmm(s))–\(CalendarData.hhmm(e))")
                    if let lane = block.lanes.first(where: { $0.key == item.lane }) { LabeledContent("Của", value: lane.title) }
                    if let sub = item.subtitle, !sub.isEmpty { Text(sub).font(.t14).foregroundStyle(.secondary) }
                }
            }
            .formStyle(.grouped)
            .navigationTitle(item.busy == true ? "Bận" : item.title)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Xong") { dismiss() } } }
        }
        #if os(macOS)
        .frame(minWidth: 360, minHeight: 240)
        #endif
    }
}
