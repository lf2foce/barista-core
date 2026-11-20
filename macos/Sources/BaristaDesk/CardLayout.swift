import SwiftUI

// BỐ CỤC THẺ — cùng luật web (web/src/lib/layout.ts, responsive-grid.tsx, table.tsx KpiRow, filter-layout.ts, clamp-note.tsx, primitives LoadError).
// Server mô tả (``w`` của khối, cờ ``primary`` của ô lọc…), app chỉ quyết cách bày theo bề rộng có sẵn.

/// Lưới 12 cột của thẻ (0166). Hàm thuần — ``CardGridTests``.
enum CardGrid {
    static let columns = 12
    /// Bề rộng tối thiểu của MỘT ô để đọc được (nhãn trục không dính nhau) và bề rộng để chia hai ô một hàng.
    static let minCell: Double = 360
    static let twoUp: Double = 2 * minCell + 24

    /// Khối có ``w`` LIỀN NHAU mà tổng ≤ 12 nằm cùng một hàng; khối không có ``w`` chiếm cả hàng (ngắt hàng đang dồn). Trả chỉ số khối theo hàng.
    static func rows(_ widths: [Int?]) -> [[Int]] {
        var rows: [[Int]] = []
        var used = 0
        var open = false   // hàng cuối là hàng của khối có ``w``
        for (i, w) in widths.enumerated() {
            guard let w else { rows.append([i]); open = false; used = columns; continue }
            if open, used + w <= columns { rows[rows.count - 1].append(i); used += w } else { rows.append([i]); used = w; open = true }
        }
        return rows
    }

    /// Bề rộng mỗi ô theo bề rộng CÓ SẴN: đủ ``minCell`` ⇒ giữ ``w`` của server; không ⇒ hai ô một hàng (ô lẻ cuối cả hàng); hẹp hơn ⇒ mỗi ô một hàng.
    static func effectiveSpans(_ ws: [Int], width: Double) -> [Int] {
        if width <= 0 || !width.isFinite || ws.count < 2 { return ws }
        if ws.allSatisfy({ width * Double($0) / Double(columns) >= minCell }) { return ws }
        if width >= twoUp { return ws.indices.map { ws.count % 2 == 1 && $0 == ws.count - 1 ? columns : columns / 2 } }
        return ws.map { _ in columns }
    }

    /// Chia các ô (theo span) thành dòng: cộng dồn ≤ 12.
    static func lines(_ spans: [Int]) -> [[Int]] {
        var out: [[Int]] = []
        var used = columns
        for (i, s) in spans.enumerated() {
            if used + s > columns { out.append([i]); used = s } else { out[out.count - 1].append(i); used += s }
        }
        return out
    }

    /// Số ô số một hàng (KpiRow của web): ≤ 6 ô thì giãn đều cùng một hàng khi đủ rộng (ô ≥ 150), nhiều hơn thì ô ≥ 180; hẹp vẫn ÍT NHẤT hai cột.
    static func kpiPerLine(count: Int, width: Double, spacing: Double) -> Int {
        guard count > 1, width > 0, width.isFinite else { return max(1, count) }
        let minTile = min(count <= 6 ? 150 : 180, (width - spacing) / 2)
        let fit = max(1, min(count, Int((width + spacing) / (minTile + spacing))))
        // chia đều số dòng (6 ô, vừa 4 ⇒ 3 + 3 chứ không 4 + 2)
        let lines = (count + fit - 1) / fit
        return (count + lines - 1) / lines
    }
}

/// Lưới 12 cột tự đo bề rộng có sẵn (không theo cửa sổ): ô cùng dòng cao bằng nhau (ô cao nhất), không ô nào tràn sang ô khác.
struct SpanGrid: Layout {
    let spans: [Int]
    var spacing: CGFloat = 12

    private func plan(_ width: CGFloat, _ n: Int) -> (spans: [Int], lines: [[Int]], unit: CGFloat) {
        let ws = Array(spans.prefix(n)) + Array(repeating: CardGrid.columns, count: max(0, n - spans.count))
        let eff = CardGrid.effectiveSpans(ws, width: Double(width))
        let unit = (width - spacing * CGFloat(CardGrid.columns - 1)) / CGFloat(CardGrid.columns)
        return (eff, CardGrid.lines(eff), unit)
    }
    /// Đề xuất bề rộng không xác định / vô hạn (đo "lý tưởng") ⇒ coi như 900 — không bao giờ tính trên ∞.
    static func finite(_ w: CGFloat?) -> CGFloat { if let w, w.isFinite, w > 0 { w } else { 900 } }
    private func cellWidth(_ span: Int, _ unit: CGFloat) -> CGFloat { unit * CGFloat(span) + spacing * CGFloat(span - 1) }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache _: inout ()) -> CGSize {
        let width = Self.finite(proposal.width)
        let p = plan(width, subviews.count)
        var h: CGFloat = 0
        for (k, line) in p.lines.enumerated() {
            let lh = line.map { subviews[$0].sizeThatFits(ProposedViewSize(width: cellWidth(p.spans[$0], p.unit), height: nil)).height }.max() ?? 0
            h += lh + (k > 0 ? spacing : 0)
        }
        return CGSize(width: width, height: h)
    }

    func placeSubviews(in bounds: CGRect, proposal _: ProposedViewSize, subviews: Subviews, cache _: inout ()) {
        let p = plan(bounds.width, subviews.count)
        var y = bounds.minY
        for line in p.lines {
            let lh = line.map { subviews[$0].sizeThatFits(ProposedViewSize(width: cellWidth(p.spans[$0], p.unit), height: nil)).height }.max() ?? 0
            var x = bounds.minX
            for i in line {
                let w = cellWidth(p.spans[i], p.unit)
                subviews[i].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(width: w, height: lh))
                x += w + spacing
            }
            y += lh + spacing
        }
    }
}

/// Hàng ô số: ô chia đều theo ``CardGrid.kpiPerLine``; dòng cuối ít ô hơn thì các ô giãn hết dòng (không để nửa hàng trống). Ô cùng dòng cao bằng nhau.
struct KPIFlow: Layout {
    var spacing: CGFloat = 10

    private func lines(_ n: Int, _ width: CGFloat) -> [[Int]] {
        let per = CardGrid.kpiPerLine(count: n, width: Double(width), spacing: Double(spacing))
        return stride(from: 0, to: n, by: per).map { Array($0..<min(n, $0 + per)) }
    }
    private func w(_ count: Int, _ width: CGFloat) -> CGFloat { (width - spacing * CGFloat(count - 1)) / CGFloat(count) }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache _: inout ()) -> CGSize {
        let width = SpanGrid.finite(proposal.width)
        var h: CGFloat = 0
        for (k, line) in lines(subviews.count, width).enumerated() {
            let cw = w(line.count, width)
            h += (line.map { subviews[$0].sizeThatFits(ProposedViewSize(width: cw, height: nil)).height }.max() ?? 0) + (k > 0 ? spacing : 0)
        }
        return CGSize(width: width, height: h)
    }

    func placeSubviews(in bounds: CGRect, proposal _: ProposedViewSize, subviews: Subviews, cache _: inout ()) {
        var y = bounds.minY
        for line in lines(subviews.count, bounds.width) {
            let cw = w(line.count, bounds.width)
            let lh = line.map { subviews[$0].sizeThatFits(ProposedViewSize(width: cw, height: nil)).height }.max() ?? 0
            var x = bounds.minX
            for i in line {
                subviews[i].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(width: cw, height: lh))
                x += cw + spacing
            }
            y += lh + spacing
        }
    }
}

/// Chú thích / ghi chú dài (rà bố cục 18/11): gấp còn MỘT dòng, "Xem thêm" chỉ khi chữ thật sự bị cắt; ngắn vừa một dòng thì không có nút.
struct ClampNote: View {
    let text: String
    @State private var open = false

    var body: some View {
        if open {
            VStack(alignment: .leading, spacing: 4) {
                Text(text).font(.t12).foregroundStyle(.secondary).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                Button("Thu gọn") { open = false }.buttonStyle(.plain).foregroundStyle(Color.accent).font(.t12m)
            }
        } else {
            ViewThatFits(in: .horizontal) {
                Text(text).font(.t12).foregroundStyle(.secondary).lineLimit(1).fixedSize()
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(text).font(.t12).foregroundStyle(.secondary).lineLimit(1).truncationMode(.tail).help(text)
                    Button("Xem thêm") { open = true }.buttonStyle(.plain).foregroundStyle(Color.accent).font(.t12m).fixedSize()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// Màn / thẻ không tải được: thông báo + "Thử lại" + giờ lỗi (giờ Việt Nam). ``compact``: màn VẪN hiện bản cũ ⇒ một dòng mỏng trên nội dung cũ.
struct LoadError: View {
    let message: String
    let at: Date?
    var busy = false
    var compact = false
    let retry: () -> Void

    private var when: String { at.map { "lúc \(Fmt.time($0))" } ?? "" }

    private var button: some View {
        Button(action: retry) {
            Label(busy ? "Đang thử lại…" : "Thử lại", systemImage: "arrow.clockwise")
        }
        .buttonStyle(.bordered).disabled(busy).fixedSize()
    }

    var body: some View {
        if compact {
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.icloud").foregroundStyle(Color.bad)
                (Text("Chưa tải lại được").font(.t12m) + Text(" — đang xem bản trước. \(message)\(when.isEmpty ? "" : " · \(when)")").font(.t12))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                button
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.bad.opacity(0.08)))
        } else {
            VStack(spacing: 8) {
                Image(systemName: "exclamationmark.icloud").font(.system(size: 26)).foregroundStyle(Color.bad)
                Text("Chưa tải được").font(.t16)
                Text(message).font(.t14).foregroundStyle(.secondary).multilineTextAlignment(.center).textSelection(.enabled).frame(maxWidth: 520)
                if !when.isEmpty {
                    Text("\(when) — thử lại sau ít phút; còn lỗi thì báo quản trị kèm giờ này").font(.t12).monospacedDigit().foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                button.padding(.top, 4)
            }
            .padding(.horizontal, 16).padding(.vertical, 32)
            .frame(maxWidth: .infinity)
            .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface2))
        }
    }
}

/// Bày bộ lọc (web filter-layout.ts): ô CHÍNH ở ngoài, ô còn lại (kể cả nhóm gấp) vào nút "Bộ lọc"; ô phụ đang bật thành chip. Hàm thuần — ``FilterSplitTests``.
enum FilterSplit {
    /// Màn có tới ngần này ô (không ô nào thuộc nhóm gấp) thì bày hết ra ngoài.
    static let inlineMax = 3
    /// Server cũ chưa gửi cờ ``primary`` ⇒ ngần này ô đầu không thuộc nhóm gấp là chính.
    static let guessPrimary = 2

    static func split(_ fields: [FieldSpec]) -> (inline: [FieldSpec], more: [FieldSpec]) {
        let plain = fields.filter { $0.group == nil }
        if fields.count <= inlineMax { return (plain, fields.filter { $0.group != nil }) }
        let flagged = fields.contains { $0.primary != nil } ? plain.filter { $0.primary == true } : []
        let keys = Set((flagged.isEmpty ? Array(plain.prefix(guessPrimary)) : flagged).map(\.key))
        return (fields.filter { keys.contains($0.key) }, fields.filter { !keys.contains($0.key) })
    }

    private static func empty(_ v: JSON?) -> Bool {
        guard let v else { return true }
        switch v { case .null: return true; case .string(let s): return s.isEmpty; case .bool(let b): return !b; default: return false }
    }

    /// Ô đang thật sự lọc: khác mặc định server khai (trống mà mặc định có giá trị cũng là đang lọc).
    static func isActive(_ f: FieldSpec, _ v: JSON?) -> Bool {
        if empty(v) { return !empty(f.default) }
        return v != f.default
    }

    /// Chữ trên chip: "Nhãn: giá trị" — lựa chọn ⇒ nhãn lựa chọn; tiền ⇒ gọn; có / không ⇒ chỉ nhãn.
    static func chip(_ f: FieldSpec, _ v: JSON?, options: [Option]?) -> String {
        if f.kind == "bool" { return f.label }
        if let o = (options ?? f.options)?.first(where: { $0.value == (v?.text ?? "") }) { return "\(f.label): \(o.label)" }
        guard let v, !empty(v) else { return "\(f.label): Tất cả" }
        if f.kind == "money", let n = v.double { return "\(f.label): \(Fmt.compactMoney(n))" }
        return "\(f.label): \(f.kind == "date" ? Fmt.date(v.text) : v.text)"
    }
}

/// Tên biểu tượng server khai (SF Symbols) → biểu tượng app vẽ. Cùng MỘT bảng ánh xạ với web (web/src/components/icons.tsx ánh xạ sang lucide):
/// tên nào SF vẽ ra hình khác hẳn hình web chọn (vd "chart.bar.doc.horizontal" là tờ giấy, web vẽ biểu đồ) thì đổi sang SF gần hình web nhất.
/// Thêm tên mới ở server ⇒ thêm ở icons.tsx; chỉ khi hai bên lệch hình mới thêm dòng ở đây.
enum SymbolMap {
    static let alias: [String: String] = [
        "chart.bar.doc.horizontal": "chart.bar.xaxis",        // web ChartBarBig — biểu đồ, không phải tờ giấy
        "books.vertical": "book",                             // web BookOpen — sách mở
        "checkmark.rectangle.stack": "list.clipboard",        // web ClipboardCheck
        "list.bullet.rectangle": "checklist",                 // web ListTodo
        "list.bullet.clipboard": "checklist",                 // web ListTodo
        "list.bullet.rectangle.portrait": "scroll",           // web ScrollText
        "doc.plaintext": "receipt",                           // web Receipt
        "tray.full": "square.3.layers.3d",                    // web Layers
        "person.crop.rectangle.stack": "person.2",            // web Users
        "person.2.wave.2": "person.2",                        // web Users
        "building.columns": "building",                       // web Building
        "lock.badge.clock": "lock",                           // web Lock
    ]

    /// Tên đích không kèm ".fill" (không phải tên nào cũng có bản kín — tên sai ⇒ ô trống); nơi vẽ tự tô kín bằng ``symbolVariant`` nếu cần.
    static func name(_ server: String) -> String {
        let base = server.hasSuffix(".fill") ? String(server.dropLast(5)) : server
        return alias[base] ?? server
    }
}
