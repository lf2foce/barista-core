import SwiftUI

// Khối dùng chung chép từ Barista Desk: chữ lấp lánh "đang nghĩ", ba chấm đang soạn, danh sách bước, markdown gọn.

struct ShimmerText: View {
    let text: String
    @State private var phase: CGFloat = -1

    var body: some View {
        Text(text)
            .font(.t12m)
            .foregroundStyle(.secondary)
            .overlay {
                GeometryReader { g in
                    LinearGradient(colors: [.clear, .white.opacity(0.85), .clear], startPoint: .leading, endPoint: .trailing)
                        .frame(width: g.size.width * 0.5)
                        .offset(x: phase * g.size.width * 1.5)
                }
                .mask(Text(text).font(.t12m))
                .blendMode(.plusLighter)
            }
            .onAppear { withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) { phase = 1 } }
    }
}

struct TypingDots: View {
    @State private var on = false
    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3) { i in
                Circle().fill(Color.secondary).frame(width: 6, height: 6)
                    .opacity(on ? 1 : 0.3)
                    .animation(.easeInOut(duration: 0.6).repeatForever().delay(Double(i) * 0.2), value: on)
            }
        }
        .padding(.vertical, 6)
        .onAppear { on = true }
    }
}

struct StepList: View {
    @Environment(Desk.self) private var desk
    let steps: [Step]
    var cards: [ChatCard] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(steps) { s in
                let card = cards.first { $0.id == s.card_id }
                HStack(alignment: .top, spacing: 8) {
                    Group {
                        if s.done == nil {
                            ProgressView().controlSize(.mini)
                        } else {
                            Image(systemName: s.done == true ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                                .foregroundStyle(s.done == true ? Color.ok : Color.warn)
                        }
                    }
                    .frame(width: 16, height: 16)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(s.title).font(.t12m)
                        let detail = (s.error?.isEmpty == false ? s.error : nil) ?? s.summary
                        if let detail, !detail.isEmpty {
                            Text(detail).font(.t12).foregroundStyle(.secondary).lineLimit(3)
                        }
                    }
                    Spacer(minLength: 0)
                    if card != nil { Image(systemName: "arrow.up.right.square").foregroundStyle(Color.accent) }
                }
                // chạm đâu trên dòng cũng mở thẻ của bước — không bắt nhắm đúng mũi tên nhỏ
                .contentShape(Rectangle())
                .onTapGesture { if let card { withAnimation(.snappy) { desk.open(.card(card), show: true) } } }
                .help(card == nil ? "" : "Mở kết quả bước này trên khung làm việc")
                .transition(.move(edge: .leading).combined(with: .opacity))
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.primary.opacity(0.04)))
    }
}

// MARK: - Markdown gọn (đoạn, tiêu đề, gạch đầu dòng, bảng) — như Barista Desk

struct MarkdownText: View {
    let text: String

    private enum Block {
        case heading(String, Int), bullet(marker: String, text: String, level: Int), para(String), table([[String]]), rule
    }

    private static let bulletRegex = try! NSRegularExpression(pattern: #"^([-*•◦]|\d+[.)])\s+"#)
    private static let tableSepRegex = try! NSRegularExpression(pattern: #"^\|[\s:\-|]+\|?$"#)
    private static let ruleRegex = try! NSRegularExpression(pattern: #"^[-*_]{3,}$"#)

    private static func matchPrefix(_ regex: NSRegularExpression, in s: String) -> Range<String.Index>? {
        let ns = NSRange(s.startIndex..<s.endIndex, in: s)
        guard let m = regex.firstMatch(in: s, range: ns) else { return nil }
        return Range(m.range, in: s)
    }

    private var blocks: [Block] {
        let lines = text.components(separatedBy: "\n")
        let widths = Set(lines.compactMap { raw -> Int? in
            let t = raw.trimmingCharacters(in: .whitespaces)
            guard Self.matchPrefix(Self.bulletRegex, in: t) != nil else { return nil }
            return raw.prefix(while: { $0 == " " || $0 == "\t" }).count
        }).sorted()
        var out: [Block] = []
        var table: [[String]] = []
        func flush() { if !table.isEmpty { out.append(.table(table)); table = [] } }
        for raw in lines {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("|") {
                if Self.matchPrefix(Self.tableSepRegex, in: line) == nil {
                    table.append(line.trimmingCharacters(in: CharacterSet(charactersIn: "|"))
                        .components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) })
                }
                continue
            }
            flush()
            if line.isEmpty { continue }
            if Self.matchPrefix(Self.ruleRegex, in: line) != nil {
                out.append(.rule)
            } else if line.hasPrefix("#") {
                let level = line.prefix(while: { $0 == "#" }).count
                out.append(.heading(line.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces), level))
            } else if let r = Self.matchPrefix(Self.bulletRegex, in: line) {
                let level = widths.firstIndex(of: raw.prefix(while: { $0 == " " || $0 == "\t" }).count) ?? 0
                let marker = line[r].trimmingCharacters(in: .whitespaces)
                let m = marker.first?.isNumber == true ? marker : ["•", "◦", "–"][min(level, 2)]
                out.append(.bullet(marker: m, text: String(line[r.upperBound...]), level: level))
            } else {
                out.append(.para(line))
            }
        }
        flush()
        return out
    }

    /// Đoạn chữ liền nhau (tiêu đề, đoạn, gạch đầu dòng) gộp vào MỘT ô chữ AppKit ⇒ bôi chọn / chép được cả câu trả lời
    /// (SwiftUI `Text` chỉ chọn được trong từng ô riêng). Bảng / kẻ ngang vẫn là khối riêng.
    private enum Run { case text([Block]), table([[String]]), rule }

    private var runs: [Run] {
        var out: [Run] = []
        var buf: [Block] = []
        func flush() { if !buf.isEmpty { out.append(.text(buf)); buf = [] } }
        for b in blocks {
            switch b {
            case .table(let rows): flush(); out.append(.table(rows))
            case .rule: flush(); out.append(.rule)
            default: buf.append(b)
            }
        }
        flush()
        return out
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            ForEach(Array(runs.enumerated()), id: \.offset) { _, r in
                switch r {
                case .text(let bs): SelectableText(attributed: Self.attributed(bs))
                case .table(let rows): TableBlock(rows: rows).textSelection(.enabled)
                case .rule: Rectangle().fill(Color.primary.opacity(0.08)).frame(height: 1)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .fixedSize(horizontal: false, vertical: true)
    }

    private static func attributed(_ blocks: [Block]) -> NSAttributedString {
        let out = NSMutableAttributedString()
        for (i, b) in blocks.enumerated() {
            let para = NSMutableParagraphStyle()
            para.lineSpacing = 3
            para.paragraphSpacingBefore = i == 0 ? 0 : 9
            var font = PFont.systemFont(ofSize: 14)
            var prefix = NSAttributedString()
            var body = ""
            switch b {
            case let .heading(s, level):
                font = .systemFont(ofSize: level <= 2 ? 16 : 15, weight: .semibold)
                para.paragraphSpacingBefore = i == 0 ? 0 : 18  // tách mục rõ hơn khoảng giữa hai đoạn
                para.paragraphSpacing = 2
                body = s
            case let .bullet(marker, text, level):
                let indent = CGFloat(level) * 18 + 2
                let numbered = marker.first?.isNumber == true
                let mark = numbered ? marker : (level == 0 ? "•" : "◦")
                let tab = indent + (numbered ? 24 : 14)
                para.headIndent = tab
                para.firstLineHeadIndent = indent
                para.tabStops = [NSTextTab(textAlignment: .left, location: tab)]
                para.paragraphSpacingBefore = i == 0 ? 0 : 5
                prefix = NSAttributedString(string: mark + "\t", attributes: [
                    .font: numbered ? PFont.monospacedDigitSystemFont(ofSize: 14, weight: .medium) : PFont.systemFont(ofSize: 14, weight: .bold),
                    .foregroundColor: numbered ? PColor.rSecondary : PColor.rAccent])
                body = text
            case .para(let s): body = s
            case .table, .rule: continue
            }
            let line = NSMutableAttributedString(attributedString: prefix)
            line.append(inline(body, font: font))
            if i < blocks.count - 1 { line.append(NSAttributedString(string: "\n")) }
            line.addAttribute(.paragraphStyle, value: para, range: NSRange(location: 0, length: line.length))
            out.append(line)
        }
        return out
    }

    /// **đậm**, *nghiêng*, `mã`, [liên kết] trong một dòng ⇒ thuộc tính AppKit (giữ cỡ chữ của khối).
    private static func inline(_ s: String, font: PFont) -> NSAttributedString {
        let parsed = (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(s)
        let out = NSMutableAttributedString()
        for run in parsed.runs {
            var f = font
            let intent = run.inlinePresentationIntent ?? []
            if intent.contains(.stronglyEmphasized) { f = PFont.systemFont(ofSize: font.pointSize, weight: .semibold) }
            if intent.contains(.emphasized) { f = f.italicized() }
            if intent.contains(.code) { f = PFont.monospacedSystemFont(ofSize: font.pointSize - 1, weight: .regular) }
            var attrs: [NSAttributedString.Key: Any] = [.font: f, .foregroundColor: PColor.rLabel]
            if let url = run.link { attrs[.link] = url }
            out.append(NSAttributedString(string: String(parsed[run.range].characters), attributes: attrs))
        }
        return out
    }

    private struct TableBlock: View {
        let rows: [[String]]
        var body: some View {
            ScrollView(.horizontal) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { i, row in
                        GridRow {
                            ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                                Text((try? AttributedString(markdown: cell)) ?? AttributedString(cell))
                                    .font(i == 0 ? .t12m : .t12)
                                    .foregroundStyle(i == 0 ? .secondary : .primary)
                            }
                        }
                        if i == 0 { Divider() }
                    }
                }
                .padding(10)
            }
            .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.04)))
        }
    }
}

/// Xếp con theo hàng, hết chỗ thì xuống dòng (chép FlowRow của Barista Desk) — bộ lọc, nút thao tác khi cửa sổ hẹp.
struct FlowRow: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache _: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0, widest: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x > 0 && x + s.width > width { y += row + spacing; x = 0; row = 0 }
            x += s.width + spacing
            row = max(row, s.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: min(widest, width), height: y + row)
    }

    func placeSubviews(in bounds: CGRect, proposal _: ProposedViewSize, subviews: Subviews, cache _: inout ()) {
        // chia hàng trước, rồi đặt từng phần tử GIỮA chiều cao hàng — ô chọn / nút / ô chữ cao khác nhau vẫn thẳng hàng
        var rows: [[(LayoutSubview, CGSize)]] = [[]]
        var x: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x > 0 && x + s.width > bounds.width { rows.append([]); x = 0 }
            rows[rows.count - 1].append((v, s))
            x += s.width + spacing
        }
        var y = bounds.minY
        for row in rows where !row.isEmpty {
            let h = row.map(\.1.height).max() ?? 0
            var x = bounds.minX
            for (v, s) in row {
                v.place(at: CGPoint(x: x, y: y + (h - s.height) / 2), proposal: ProposedViewSize(s))
                x += s.width + spacing
            }
            y += h + spacing
        }
    }
}


#if os(macOS)
/// Ô chữ chỉ đọc, chọn / chép được (NSTextView) — tự cao theo bề ngang được cấp, nền trong suốt, màu theo sáng / tối.
struct SelectableText: NSViewRepresentable {
    let attributed: NSAttributedString

    func makeNSView(context: Context) -> NSTextView {
        let v = NSTextView()
        v.isEditable = false
        v.isSelectable = true
        v.drawsBackground = false
        v.textContainerInset = .zero
        v.textContainer?.lineFragmentPadding = 0
        v.textContainer?.widthTracksTextView = true
        v.isVerticallyResizable = false
        v.isHorizontallyResizable = false
        v.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return v
    }

    func updateNSView(_ v: NSTextView, context: Context) {
        if v.attributedString() != attributed { v.textStorage?.setAttributedString(attributed) }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView v: NSTextView, context: Context) -> CGSize? {
        let width = proposal.width ?? 480
        guard let container = v.textContainer, let layout = v.layoutManager else { return nil }
        container.containerSize = NSSize(width: width, height: .greatestFiniteMagnitude)
        layout.ensureLayout(for: container)
        return CGSize(width: width, height: ceil(layout.usedRect(for: container).height))
    }
}
#else
/// Ô chữ chỉ đọc, chọn / chép được (UITextView) — cao theo bề ngang được cấp; bấm giữ để chọn, chạm liên kết để mở.
struct SelectableText: UIViewRepresentable {
    let attributed: NSAttributedString

    func makeUIView(context: Context) -> UITextView {
        let v = UITextView()
        v.isEditable = false
        v.isSelectable = true
        v.isScrollEnabled = false
        v.backgroundColor = .clear
        v.textContainerInset = .zero
        v.textContainer.lineFragmentPadding = 0
        v.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return v
    }

    func updateUIView(_ v: UITextView, context: Context) {
        if v.attributedText != attributed { v.attributedText = attributed }
    }

    func sizeThatFits(_ proposal: ProposedViewSize, uiView v: UITextView, context: Context) -> CGSize? {
        let width = proposal.width ?? 320
        return CGSize(width: width, height: ceil(v.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude)).height))
    }
}
#endif
