import Charts
import SwiftUI

private struct DashboardScrollingKey: EnvironmentKey {
    static let defaultValue = false
}

/// Lọc chéo kiểu BI (0173): biểu đồ trong thẻ của MỘT MÀN đọc / đặt bộ lọc của màn đó. Ngoài màn (chat, cửa sổ con) ⇒ nil, không bấm được.
struct ScreenPick {
    let get: (String) -> String?
    let set: (String, String?) -> Void
}

private struct ScreenPickKey: EnvironmentKey {
    static let defaultValue: ScreenPick? = nil
}

extension EnvironmentValues {
    var dashboardIsScrolling: Bool {
        get { self[DashboardScrollingKey.self] }
        set { self[DashboardScrollingKey.self] = newValue }
    }
    var screenPick: ScreenPick? {
        get { self[ScreenPickKey.self] }
        set { self[ScreenPickKey.self] = newValue }
    }
}

/// Khối "chart" (kernel/ui.py): server tính số, app chỉ vẽ — cùng các loại web vẽ (web/src/components/blocks/chart.tsx): bar · hbar · line · area ·
/// donut · funnel · combo (cột + đường, trục phải — 0166) · scatter · treemap (0173). Theo cách Apple dùng Swift Charts: màu hệ thống, một chuỗi = một
/// màu, trục số mảnh, KHÔNG in số lên mọi cột — rê chuột (chạm trên iPhone) mới hiện ô số. null ⇒ không vẽ (không thành 0 — "không đo được" khác "bằng 0").

struct ChartBlock: Decodable, Hashable {
    /// ``as`` / ``axis`` chỉ ở "combo" (0166): cột hay đường, trục trái hay phải. ``x``: hoành độ của "scatter" (0173).
    struct Series: Decodable, Hashable {
        let name: String
        let values: [Double?]
        var tone: String? = nil
        var axis: String? = nil
        var mark: String? = nil
        var x: [Double?]? = nil
        private enum K: String, CodingKey { case name, values, tone, axis, mark = "as", x }
        init(name: String, values: [Double?], tone: String? = nil, axis: String? = nil, mark: String? = nil, x: [Double?]? = nil) {
            self.name = name; self.values = values; self.tone = tone; self.axis = axis; self.mark = mark; self.x = x
        }
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: K.self)
            name = try c.decode(String.self, forKey: .name)
            values = try c.decode([Double?].self, forKey: .values)
            tone = try c.decodeIfPresent(String.self, forKey: .tone)
            axis = try c.decodeIfPresent(String.self, forKey: .axis)
            mark = try c.decodeIfPresent(String.self, forKey: .mark)
            x = try? c.decodeIfPresent([Double?].self, forKey: .x)
        }
        var right: Bool { axis == "right" }
    }
    /// Bấm nhãn i ⇒ bộ lọc ``param`` của màn = ``values[i]`` (bấm lại: bỏ lọc) — 0173.
    struct Pick: Decodable, Hashable { let param: String; let values: [String] }
    let title, kind, unit: String
    let labels: [String]
    let series: [Series]
    let note: String?
    var unit_right: String? = nil
    var pick: Pick? = nil

    /// Đơn vị của MỘT chuỗi: chuỗi trục phải của combo đo bằng ``unit_right`` (trống ⇒ như ``unit``).
    func unit(of s: Series) -> String { s.right && !(unit_right ?? "").isEmpty ? unit_right! : unit }
    static let kinds = ["bar", "hbar", "line", "area", "donut", "funnel", "combo", "scatter", "treemap"]
}

extension Color {
    /// Màu biểu đồ = màu hệ thống (tự đổi theo sáng / tối, giống app của Apple). Không ghi tông ⇒ lần lượt theo bảng (cùng bảng của web).
    static func chart(_ tone: String?, index: Int = 0) -> Color {
        switch tone {
        case "blue": return .blue
        case "teal": return .teal
        case "green": return .green
        case "orange": return .orange
        case "purple": return .purple
        case "red": return .red
        case "pink": return .pink
        case "indigo": return .indigo
        case "mint": return .mint
        default:
            let palette: [Color] = [.blue, .teal, .orange, .purple, .pink, .indigo, .mint]
            return palette[index % palette.count]
        }
    }
}

enum ChartFmt {
    static func value(_ v: Double, unit: String) -> String {
        switch unit {
        case "vnd": Fmt.compactMoney(v)
        case "pct": Fmt.number((v * 10).rounded() / 10) + "%"
        default: Fmt.number(v)
        }
    }

    /// Nhãn trục: gọn hơn ô số ("0" thay "0 đ").
    static func axis(_ v: Double, unit: String) -> String { v == 0 ? "0" : value(v, unit: unit) }

    /// Nhãn quá dài ⇒ cắt đuôi "…" (tên đủ vẫn ở ô số khi rê chuột).
    static func cut(_ s: String, _ n: Int) -> String { s.count > n ? String(s.prefix(max(1, n - 1))) + "…" : s }
}

/// Nhãn trục X của biểu đồ cột / đường (không bao giờ chồng lên nhau): đủ chỗ ⇒ nằm ngang; không ⇒ dựng đứng + cắt "…" (web nghiêng −40°, cùng ý).
/// Hàm thuần — ``ChartLabelTests``.
enum XLabels {
    static let charWidth: Double = 6.8      // ~ cỡ chữ 12
    static let maxChars = 24

    struct Fit: Equatable { let vertical: Bool; let chars: Int; let extra: Double }

    static func fit(_ labels: [String], plotWidth: Double) -> Fit {
        let longest = labels.map(\.count).max() ?? 0
        guard !labels.isEmpty, plotWidth > 0, plotWidth.isFinite else { return Fit(vertical: false, chars: maxChars, extra: 0) }
        let per = plotWidth / Double(labels.count)
        if Double(longest) * charWidth + 6 <= per { return Fit(vertical: false, chars: longest, extra: 0) }
        // nhãn ngắn mà nhiều (12 tháng "T01/26"): vẫn ngang, Swift Charts tự bỏ bớt nhãn xen kẽ (greedy) — không cần dựng đứng
        if longest <= 7 { return Fit(vertical: false, chars: longest, extra: 0) }
        let chars = min(longest, maxChars)
        return Fit(vertical: true, chars: chars, extra: Double(chars) * charWidth + 8)
    }
}

/// Trục chia đều "đẹp" (1 · 2 · 2,5 · 5 × 10ⁿ) — dùng cho combo hai trục: hai trục cùng số vạch nên vạch trái / phải trùng nhau. Hàm thuần.
enum NiceAxis {
    static func step(_ raw: Double) -> Double {
        guard raw > 0, raw.isFinite else { return 1 }
        let p = pow(10, floor(log10(raw)))
        for m in [1.0, 2, 2.5, 5, 10] where m * p >= raw { return m * p }
        return 10 * p
    }

    /// (thấp, cao, bước, số khoảng) phủ [lo, hi] (luôn gồm 0) với ``ticks`` khoảng.
    static func span(_ values: [Double], ticks: Int = 4) -> (lo: Double, hi: Double, step: Double, n: Int) {
        let lo0 = min(0, values.min() ?? 0), hi0 = max(0, values.max() ?? 0)
        let st = step(max(hi0 - lo0, 1e-9) / Double(ticks))
        let lo = (lo0 / st).rounded(.down) * st
        let raw = ((hi0 - lo) / st).rounded(.up)
        let n = raw.isFinite ? max(1, min(50, Int(raw))) : ticks
        return (lo, lo + Double(n) * st, st, n)
    }
}

struct ChartCard: View, Equatable {
    let block: ChartBlock
    @Environment(\.dashboardIsScrolling) private var scrolling
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.screenPick) private var screenPick
    @State private var selected: String?      // nhãn đang rê chuột trên thanh ngang
    @State private var angle: Double?         // biểu đồ tròn: góc đang rê
    @State private var grown = false          // cột mọc lên khi mở
    @State private var plotWidth: Double = 0  // bề rộng thật ⇒ chọn cách đặt nhãn trục X

    private struct Point: Identifiable { let id: String; let index: Int; let label: String; let series: String; let segment: String; let value: Double; let color: Color }

    /// null hết ⇒ chưa đo được; phễu / bánh toàn 0 cũng không có gì để vẽ (cột / đường toàn 0 vẫn là thông tin thật: "chưa phát sinh").
    private let hasData: Bool
    private let points: [Point]
    private let seriesNames: [String]
    private let seriesColors: [Color]

    init(block: ChartBlock) {
        self.block = block
        let all = block.series.flatMap(\.values).compactMap { $0 }
        hasData = !all.isEmpty && !(["funnel", "donut"].contains(block.kind) && all.allSatisfy { $0 == 0 })
        points = block.series.enumerated().flatMap { si, s in
            var segment = 0   // null ngắt đường: mỗi đoạn liền là một chuỗi riêng (không nối qua chỗ không đo được)
            return block.labels.enumerated().compactMap { li, l -> Point? in
                guard li < s.values.count, let v = s.values[li] else { segment += 1; return nil }
                return Point(id: "\(si)-\(li)", index: li, label: l, series: s.name, segment: "\(s.name)#\(segment)", value: v, color: .chart(s.tone, index: si))
            }
        }
        seriesNames = block.series.map(\.name)
        seriesColors = block.series.enumerated().map { Color.chart($1.tone, index: $0) }
    }

    static func == (lhs: Self, rhs: Self) -> Bool { lhs.block == rhs.block }

    private var multi: Bool { block.series.count > 1 }

    /// Lọc chéo: nhãn đang được lọc (−1 = không) và hàm bấm.
    private var pickSel: Int {
        guard let p = block.pick, let sp = screenPick, let cur = sp.get(p.param) else { return -1 }
        return p.values.firstIndex(of: cur) ?? -1
    }
    private func pickAt(_ i: Int) {
        guard let p = block.pick, let sp = screenPick, i >= 0, i < p.values.count else { return }
        let v = p.values[i]
        sp.set(p.param, sp.get(p.param) == v ? nil : v)
    }
    private var clickable: Bool { block.pick != nil && screenPick != nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !block.title.isEmpty { Text(block.title).font(.t14m).fixedSize(horizontal: false, vertical: true) }
            if !hasData {
                Label("Chưa đo được — chưa có dữ liệu đủ tin cậy", systemImage: "chart.bar.xaxis")
                    .font(.t12).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 120)
            } else {
                chart
            }
            if let n = block.note, !n.isEmpty { ClampNote(text: n) }
        }
        .padding(16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.primary.opacity(0.08)))
        .onGeometryChange(for: Double.self) { $0.size.width - 32 } action: { w in if abs(w - plotWidth) > 1 { plotWidth = w } }
        .onAppear {
            guard !grown else { return }
            #if os(macOS)
            grown = true   // biểu đồ nằm trong lưới: vẽ ngay, không chạy lại hàng loạt animation khi cuộn tới
            #else
            if reduceMotion { grown = true }
            else { withAnimation(.smooth(duration: 0.35)) { grown = true } }
            #endif
        }
        .onChange(of: scrolling) { if scrolling { selected = nil; angle = nil } }
    }

    @ViewBuilder private var chart: some View {
        switch block.kind {
        case "line", "area": lineChart.frame(height: 200 + xFit.extra)
        case "hbar": hbar
        case "donut": donut
        case "funnel": funnel
        case "combo": combo.frame(height: 220 + xFit.extra)
        case "scatter": scatter.frame(height: 240)
        case "treemap": TreemapView(block: block, sel: pickSel, onPick: clickable ? { pickAt($0) } : nil).frame(height: 260)
        default: bars.frame(height: 200 + xFit.extra)
        }
    }

    // MARK: trục, ô số khi rê chuột

    /// Trục số chiếm ~64pt ⇒ phần còn lại cho các nhãn.
    private var xFit: XLabels.Fit { XLabels.fit(block.labels, plotWidth: plotWidth - (block.kind == "combo" ? 112 : 64)) }

    private var yAxis: some AxisContent {
        AxisMarks(position: .trailing, values: .automatic(desiredCount: 4)) { v in
            AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5)).foregroundStyle(Color.primary.opacity(0.1))
            AxisValueLabel { if let d = v.as(Double.self) { Text(ChartFmt.axis(d, unit: block.unit)) } }
        }
    }

    private var xAxis: some AxisContent {
        let fit = xFit
        return AxisMarks { v in
            if fit.vertical {
                AxisValueLabel(anchor: .top, collisionResolution: .greedy(minimumSpacing: 2), orientation: .verticalReversed) {
                    if let s = v.as(String.self) { Text(ChartFmt.cut(s, fit.chars)) }
                }
            } else {
                AxisValueLabel(collisionResolution: .greedy(minimumSpacing: 4)) {
                    if let s = v.as(String.self) { Text(s).lineLimit(1) }
                }
            }
        }
    }

    private func dim(_ label: String) -> Double { selected == nil || selected == label ? 1 : 0.35 }
    private func picked(_ i: Int) -> Double { pickSel < 0 ? 1 : (pickSel == i ? 1 : 0.3) }

    // MARK: cột

    private var bars: some View {
        Chart {
            ForEach(points) { p in
                BarMark(x: .value("Nhãn", p.label), y: .value("Giá trị", grown ? p.value : 0), width: .ratio(multi ? 0.75 : 0.55))
                    .position(by: .value("Chuỗi", p.series))
                    .foregroundStyle(by: .value("Chuỗi", p.series))
                    .opacity(picked(p.index))
                    .cornerRadius(4)
            }
        }
        .chartForegroundStyleScale(domain: seriesNames, range: seriesColors.map { AnyShapeStyle($0.gradient) })
        .chartLegend(multi ? .visible : .hidden)
        .chartYAxis { yAxis }
        .chartXAxis { xAxis }
        .modifier(ChartInspection(block: block, onPick: clickable ? { pickAt($0) } : nil))
    }

    // MARK: combo (0166) — cột + đường; chuỗi trục phải đo bằng unit_right, vẽ quy về trục trái (Swift Charts chỉ có một trục số)

    private var combo: some View {
        let left = block.series.filter { !$0.right }.flatMap(\.values).compactMap { $0 }
        let right = block.series.filter(\.right).flatMap(\.values).compactMap { $0 }
        let hasRight = !right.isEmpty
        var l = NiceAxis.span(left), r = NiceAxis.span(right)
        let n = max(l.n, hasRight ? r.n : 0)   // cùng số khoảng ⇒ vạch trái / phải trùng nhau
        l = (l.lo, l.lo + Double(n) * l.step, l.step, n)
        r = (r.lo, r.lo + Double(n) * r.step, r.step, n)
        let toLeft = { (v: Double) in l.lo + (v - r.lo) * (l.hi - l.lo) / max(r.hi - r.lo, 1e-9) }
        let ticks = (0...n).map { l.lo + Double($0) * l.step }
        let unitRight = (block.unit_right ?? "").isEmpty ? block.unit : block.unit_right!
        let barSeries = block.series.filter { $0.mark != "line" }.map(\.name)
        return Chart {
            ForEach(points) { p in
                let s = block.series.first { $0.name == p.series }
                let y = s?.right == true ? toLeft(p.value) : p.value
                if s?.mark == "line" {
                    LineMark(x: .value("Nhãn", p.label), y: .value("Giá trị", y), series: .value("Đoạn", p.segment))
                        .foregroundStyle(p.color)
                        .lineStyle(StrokeStyle(lineWidth: 2.2, lineCap: .round, lineJoin: .round))
                    PointMark(x: .value("Nhãn", p.label), y: .value("Giá trị", y))
                        .foregroundStyle(p.color).symbolSize(28)
                } else {
                    BarMark(x: .value("Nhãn", p.label), y: .value("Giá trị", grown ? y : l.lo), width: .ratio(barSeries.count > 1 ? 0.75 : 0.55))
                        .position(by: .value("Chuỗi", p.series))
                        .foregroundStyle(p.color.gradient)
                        .opacity(picked(p.index))
                        .cornerRadius(4)
                }
            }
        }
        .chartYScale(domain: l.lo...l.hi)
        .chartYAxis {
            AxisMarks(position: hasRight ? .leading : .trailing, values: ticks) { v in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5)).foregroundStyle(Color.primary.opacity(0.1))
                AxisValueLabel { if let d = v.as(Double.self) { Text(ChartFmt.axis(d, unit: block.unit)) } }
            }
            if hasRight {
                AxisMarks(position: .trailing, values: ticks) { v in
                    AxisValueLabel {
                        if let d = v.as(Double.self) {
                            let i = Int(((d - l.lo) / l.step).rounded())
                            Text(ChartFmt.axis(r.lo + Double(i) * r.step, unit: unitRight))
                        }
                    }
                }
            }
        }
        .chartXAxis { xAxis }
        .chartLegend(.hidden)
        .modifier(ChartInspection(block: block, onPick: clickable ? { pickAt($0) } : nil))
        .safeAreaInset(edge: .bottom, spacing: 8) { if multi { legend } }
    }

    /// Chú giải tự xuống dòng (chuỗi nhiều tên dài không chồng nhau).
    private var legend: some View {
        FlowRow(spacing: 12) {
            ForEach(Array(block.series.enumerated()), id: \.offset) { i, s in
                HStack(spacing: 5) {
                    if s.mark == "line" { Capsule().fill(Color.chart(s.tone, index: i)).frame(width: 12, height: 3) }
                    else { RoundedRectangle(cornerRadius: 2).fill(Color.chart(s.tone, index: i)).frame(width: 8, height: 8) }
                    Text(s.name).font(.t12).foregroundStyle(.secondary).lineLimit(1)
                    if s.right { Text("(trục phải)").font(.t12).foregroundStyle(.tertiary) }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: đường / miền

    private var lineChart: some View {
        Chart {
            ForEach(points) { p in
                if block.kind == "area" || !multi {
                    AreaMark(x: .value("Nhãn", p.label), y: .value("Giá trị", p.value), series: .value("Đoạn", p.segment))
                        .foregroundStyle(LinearGradient(colors: [p.color.opacity(block.kind == "area" ? 0.3 : 0.18), p.color.opacity(0)],
                                                        startPoint: .top, endPoint: .bottom))
                        .interpolationMethod(.monotone)
                }
                LineMark(x: .value("Nhãn", p.label), y: .value("Giá trị", p.value), series: .value("Đoạn", p.segment))
                    .foregroundStyle(p.color)
                    .lineStyle(StrokeStyle(lineWidth: 2.2, lineCap: .round, lineJoin: .round))
                    .interpolationMethod(.monotone)
                if block.labels.count <= 2 { PointMark(x: .value("Nhãn", p.label), y: .value("Giá trị", p.value)).foregroundStyle(p.color) }
            }
        }
        .chartLegend(.hidden)
        .chartYAxis { yAxis }
        .chartXAxis { xAxis }
        .modifier(ChartInspection(block: block, onPick: nil))
        .safeAreaInset(edge: .bottom, spacing: 8) { if multi { legend } }
    }

    // MARK: scatter (0173) — mỗi nhãn một điểm: hoành độ series[0].x (đơn vị unit_right), tung độ values

    private var scatter: some View {
        let s0 = block.series.first
        let pts: [(Int, String, Double, Double)] = block.labels.enumerated().compactMap { i, l in
            guard let xs = s0?.x, i < xs.count, let x = xs[i], i < (s0?.values.count ?? 0), let y = s0?.values[i] else { return nil }
            return (i, l, x, y)
        }
        let color = Color.chart(s0?.tone, index: 0)
        let xUnit = (block.unit_right ?? "").isEmpty ? "count" : block.unit_right!
        let near: (Int, String, Double, Double)? = selectedX.flatMap { sx in pts.min { abs($0.2 - sx) < abs($1.2 - sx) } }
        return Chart {
            ForEach(pts, id: \.0) { p in
                PointMark(x: .value("x", p.2), y: .value("y", p.3))
                    .foregroundStyle(color)
                    .opacity(pickSel >= 0 ? (p.0 == pickSel ? 1 : 0.3) : (near == nil || near?.0 == p.0 ? 0.85 : 0.35))
                    .symbolSize(near?.0 == p.0 ? 110 : 70)
            }
        }
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { v in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5)).foregroundStyle(Color.primary.opacity(0.1))
                AxisValueLabel { if let d = v.as(Double.self) { Text(ChartFmt.axis(d, unit: xUnit)) } }
            }
        }
        .chartYAxis { yAxis }
        .chartXSelection(value: $selectedX)
        .chartOverlay { _ in
            if let near {
                VStack(alignment: .leading, spacing: 2) {
                    Text(near.1).font(.t12m)
                    Text("\(ChartFmt.value(near.2, unit: xUnit)) · \(ChartFmt.value(near.3, unit: block.unit))").font(.t12).monospacedDigit()
                }
                .padding(.horizontal, 10).padding(.vertical, 6)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Color.surface))
                .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Color.primary.opacity(0.1)))
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .allowsHitTesting(false)
            }
        }
        .onTapGesture { if clickable, let near { pickAt(near.0) } }
    }
    @State private var selectedX: Double?

    // MARK: thanh ngang — kiểu danh sách xếp hạng của Screen Time: tên + số một dòng, thanh mảnh trong rãnh bên dưới

    private var hbar: some View {
        let top = max(points.map(\.value).max() ?? 1, 1)
        return VStack(alignment: .leading, spacing: 12) {
            ForEach(points) { p in
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 8) {
                        Text(multi ? "\(p.label) · \(p.series)" : p.label).font(selected == p.label ? .t12m : .t12).lineLimit(1).truncationMode(.tail)
                            .help(p.label)
                        Spacer(minLength: 8)
                        Text(ChartFmt.value(p.value, unit: block.unit)).font(.t12m).monospacedDigit().lineLimit(1).fixedSize()
                    }
                    Track(fraction: grown ? p.value / top : 0, color: p.color)
                }
                .opacity(pickSel >= 0 ? picked(p.index) : dim(p.label))
                .contentShape(Rectangle())
                .onHover {
                    guard !scrolling else { return }
                    let next = $0 ? p.label : (selected == p.label ? nil : selected)
                    if selected != next { selected = next }
                }
                .onTapGesture { if clickable { pickAt(p.index) } }
            }
        }
    }

    // MARK: bánh

    private var donut: some View {
        let s = block.series.first
        let items = zip(block.labels, s?.values ?? []).enumerated().compactMap { i, pair -> (Int, String, Double)? in
            pair.1.map { (i, pair.0, $0) }
        }
        let total = items.reduce(0) { $0 + $1.2 }
        // góc đang rê ⇒ miếng nào (cộng dồn theo thứ tự vẽ)
        let hovered: (Int, String, Double)? = angle.flatMap { a in
            var acc = 0.0
            return items.first { acc += $0.2; return a <= acc }
        }
        let sel = pickSel
        let fade = { (i: Int) -> Double in sel >= 0 ? (sel == i ? 1 : 0.3) : (hovered == nil || hovered?.0 == i ? 1 : 0.4) }
        let pie = Chart(items, id: \.0) { it in
            SectorMark(angle: .value("Giá trị", it.2), innerRadius: .ratio(0.64), outerRadius: .ratio(hovered?.0 == it.0 ? 1 : 0.94),
                       angularInset: 1.5)
                .foregroundStyle(Color.chart(nil, index: it.0).gradient)
                .cornerRadius(5)
                .opacity(fade(it.0))
        }
        .chartAngleSelection(value: Binding(get: { angle }, set: {
            if !scrolling, angle != $0 { angle = $0 }
        }))
        .onTapGesture { if clickable, let h = hovered { pickAt(h.0) } }
        .frame(width: 168, height: 168)
        .chartBackground { _ in
            VStack(spacing: 1) {
                Text(ChartFmt.value(hovered?.2 ?? total, unit: block.unit)).font(.t16).monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
                Text(hovered?.1 ?? "tổng").font(.t12).foregroundStyle(.secondary).lineLimit(1)
            }
            .frame(width: 100)
        }
        let legend = Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 8) {
            ForEach(items, id: \.0) { it in
                GridRow {
                    Circle().fill(Color.chart(nil, index: it.0)).frame(width: 8, height: 8)
                    Text(it.1).font(hovered?.0 == it.0 ? .t12m : .t12).lineLimit(1).truncationMode(.tail).frame(maxWidth: 180, alignment: .leading)
                        .help(it.1)
                    Text(ChartFmt.value(it.2, unit: block.unit)).font(.t12).monospacedDigit().foregroundStyle(.secondary).lineLimit(1).fixedSize()
                        .gridColumnAlignment(.trailing)
                    Text(total > 0 ? "\(Int((it.2 / total * 100).rounded()))%" : "—").font(.t12m).monospacedDigit().fixedSize()
                        .gridColumnAlignment(.trailing)
                }
                .opacity(sel >= 0 ? fade(it.0) : 1)
                .contentShape(Rectangle())
                .onTapGesture { if clickable { pickAt(it.0) } }
            }
        }
        // rộng: bánh + chú giải cạnh nhau; hẹp: chú giải xuống dưới (không ép chữ chồng lên nhau)
        return ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 24) { pie; legend.fixedSize(); Spacer(minLength: 0) }
            VStack(alignment: .leading, spacing: 14) { pie; legend }
        }
    }

    // MARK: phễu — một số đo ⇒ một màu, nhạt dần theo bước; nhãn trái cố định, số + % so bước trước bên phải

    private var funnel: some View {
        let vals = zip(block.labels, block.series.first?.values ?? []).compactMap { l, v in v.map { (l, $0) } }
        let top = max(vals.map(\.1).max() ?? 1, 1)
        let base = Color.chart(block.series.first?.tone, index: 0)
        return Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 12) {
            ForEach(Array(vals.enumerated()), id: \.offset) { i, v in
                GridRow(alignment: .center) {
                    Text(v.0).font(.t12).lineLimit(1).truncationMode(.tail).frame(maxWidth: 170, alignment: .trailing).help(v.0)
                        .gridColumnAlignment(.trailing)
                    Track(fraction: grown ? v.1 / top : 0, color: base.opacity(1 - Double(i) * 0.16), height: 18)
                    HStack(spacing: 6) {
                        Text(ChartFmt.value(v.1, unit: block.unit)).font(.t12m).monospacedDigit().fixedSize()
                        Text(i > 0 && vals[i - 1].1 > 0 ? "\(Int((v.1 / vals[i - 1].1 * 100).rounded()))%" : " ")
                            .font(.t12).monospacedDigit().foregroundStyle(.secondary)
                            .frame(width: 36, alignment: .trailing)
                            .help("so với bước trước")
                    }
                    .gridColumnAlignment(.trailing)
                }
            }
        }
    }
}

/// Thanh trong rãnh (danh sách xếp hạng, phễu): rãnh xám nhạt hết bề ngang, phần màu theo tỷ lệ.
private struct Track: View {
    let fraction: Double
    let color: Color
    var height: CGFloat = 8

    var body: some View {
        GeometryReader { g in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.07))
                Capsule().fill(color.gradient).frame(width: max(g.size.width * min(max(fraction, 0), 1), fraction > 0 ? height : 0))
            }
        }
        .frame(height: height)
    }
}

/// Chia ô treemap (squarified — Bruls, Huizing, van Wijk): ô gần vuông, diện tích ∝ giá trị. Hàm thuần — ``ChartLabelTests``.
enum Squarify {
    static func layout(_ values: [Double], in rect: CGRect) -> [CGRect] {
        let total = values.reduce(0, +)
        guard total > 0, rect.width > 0, rect.height > 0 else { return values.map { _ in .zero } }
        let scale = Double(rect.width * rect.height) / total
        let areas = values.map { $0 * scale }
        var out = [CGRect](repeating: .zero, count: values.count)
        var free = rect
        var i = 0
        while i < areas.count {
            let side = Double(min(free.width, free.height))
            var row = [i]
            var j = i + 1
            while j < areas.count, worst(row.map { areas[$0] }, side) >= worst((row + [j]).map { areas[$0] }, side) { row.append(j); j += 1 }
            let sum = row.reduce(0) { $0 + areas[$1] }
            if free.width >= free.height {   // cột dọc ở bên trái
                let w = CGFloat(sum / Double(free.height))
                var y = free.minY
                for k in row { let h = CGFloat(areas[k]) / w; out[k] = CGRect(x: free.minX, y: y, width: w, height: h); y += h }
                free = CGRect(x: free.minX + w, y: free.minY, width: max(0, free.width - w), height: free.height)
            } else {                         // hàng ngang ở trên
                let h = CGFloat(sum / Double(free.width))
                var x = free.minX
                for k in row { let w = CGFloat(areas[k]) / h; out[k] = CGRect(x: x, y: free.minY, width: w, height: h); x += w }
                free = CGRect(x: free.minX, y: free.minY + h, width: free.width, height: max(0, free.height - h))
            }
            i = j
        }
        return out
    }

    private static func worst(_ row: [Double], _ side: Double) -> Double {
        let s = row.reduce(0, +)
        guard s > 0, side > 0, let mx = row.max(), let mn = row.min(), mn > 0 else { return .infinity }
        return max(side * side * mx / (s * s), (s * s) / (side * side * mn))
    }
}

/// Treemap (0173): diện tích ô = giá trị; ô quá nhỏ không in tên (rê chuột / giữ xem). Giá trị ≤ 0 / null ⇒ không có ô.
private struct TreemapView: View {
    let block: ChartBlock
    let sel: Int
    let onPick: ((Int) -> Void)?

    var body: some View {
        let vals = block.series.first?.values ?? []
        let items = block.labels.enumerated().compactMap { i, l -> (Int, String, Double)? in
            guard i < vals.count, let v = vals[i], v > 0 else { return nil }
            return (i, l, v)
        }
        GeometryReader { g in
            let rects = Squarify.layout(items.map(\.2), in: CGRect(origin: .zero, size: g.size))
            ZStack(alignment: .topLeading) {
                ForEach(Array(items.enumerated()), id: \.offset) { k, it in
                    let r = rects[k]
                    let big = r.width > 70 && r.height > 34
                    RoundedRectangle(cornerRadius: 6, style: .continuous)
                        .fill(Color.chart(nil, index: k).opacity(sel >= 0 ? (sel == it.0 ? 1 : 0.3) : 0.9))
                        .overlay(alignment: .topLeading) {
                            if big {
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(it.1).font(.t12m).lineLimit(1).truncationMode(.tail)
                                    Text(ChartFmt.value(it.2, unit: block.unit)).font(.t12).monospacedDigit().opacity(0.85).lineLimit(1)
                                }
                                .foregroundStyle(.white)
                                .padding(.horizontal, 8).padding(.vertical, 6)
                            }
                        }
                        .frame(width: max(0, r.width - 2), height: max(0, r.height - 2))
                        .offset(x: r.minX + 1, y: r.minY + 1)
                        .help("\(it.1): \(ChartFmt.value(it.2, unit: block.unit))")
                        .onTapGesture { onPick?(it.0) }
                }
            }
        }
    }
}

/// Các khối biểu đồ liền nhau trên một thẻ (không khai ``w``): lưới tự co 1–2 cột; số biểu đồ lẻ ⇒ biểu đồ cuối trải hết hai cột (như web).
struct ChartGrid: View {
    let blocks: [ChartBlock]

    var body: some View {
        SpanGrid(spans: blocks.indices.map { _ in 6 }, spacing: 12) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, b in ChartCard(block: b).equatable() }
        }
    }
}

/// Tách trạng thái lựa chọn sang lớp phủ; closure dựng marks và trục không phụ thuộc nhãn đang rê chuột.
private struct ChartInspection: ViewModifier {
    let block: ChartBlock
    var onPick: ((Int) -> Void)? = nil
    @Environment(\.dashboardIsScrolling) private var scrolling
    @State private var selected: String?

    func body(content: Content) -> some View {
        content
            .chartXSelection(value: Binding(get: { selected }, set: {
                if !scrolling, selected != $0 { selected = $0 }
            }))
            .chartOverlay { proxy in
                GeometryReader { geometry in
                    if let selected, let index = block.labels.firstIndex(of: selected),
                       let anchor = proxy.plotFrame, let x = proxy.position(forX: selected) {
                        let plot = geometry[anchor]
                        let width = min(240.0, max(0, geometry.size.width - 16))
                        ZStack(alignment: .topLeading) {
                            Rectangle().fill(Color.primary.opacity(0.18))
                                .frame(width: 1, height: plot.height)
                                .offset(x: plot.minX + x, y: plot.minY)
                            tooltip(selected, index: index)
                                .frame(width: width, alignment: .leading)
                                .offset(x: min(max(plot.minX + x - width / 2, 8), geometry.size.width - width - 8), y: plot.minY + 4)
                        }
                    }
                }
                .allowsHitTesting(false)
            }
            .onTapGesture { if let onPick, let selected, let i = block.labels.firstIndex(of: selected) { onPick(i) } }
            .onChange(of: scrolling) { if scrolling { selected = nil } }
            .onChange(of: block) { selected = nil }
    }

    private func tooltip(_ label: String, index: Int) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label).font(.t12m).fixedSize(horizontal: false, vertical: true)
            ForEach(Array(block.series.enumerated()), id: \.offset) { i, series in
                if index < series.values.count {
                    HStack(spacing: 5) {
                        if block.series.count > 1 {
                            Circle().fill(Color.chart(series.tone, index: i)).frame(width: 7, height: 7)
                            Text(series.name).font(.t12).foregroundStyle(.secondary).lineLimit(1)
                        }
                        Text(series.values[index].map { ChartFmt.value($0, unit: block.unit(of: series)) } ?? "chưa đo được")
                            .font(.t12m).monospacedDigit().lineLimit(1).fixedSize()
                    }
                }
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Color.surface))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Color.primary.opacity(0.1)))
        .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
    }
}
