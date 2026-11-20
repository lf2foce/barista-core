import PDFKit
import SwiftUI

/// File gốc của tài liệu kho (0052) — PDF mở ngay trong app, nhảy tới đúng trang trích dẫn; Word thì lưu ra máy như cũ.
struct OriginalFile: Identifiable {
    let id = UUID()
    let title: String
    let filename: String
    let data: Data
    let page: Int?
    /// Câu nguyên văn cần TÔ trên trang (bằng chứng của một phát hiện, 0070 / 0072) — nil = chỉ mở đúng trang.
    var highlight: String? = nil
    var value: String? = nil
    /// Chữ của tài liệu do model chép từ ảnh ⇒ file không có lớp chữ, không tô được — người đối chiếu bằng mắt.
    var scan: Bool = false
}

struct PDFSheet: View {
    let file: OriginalFile
    @Environment(\.dismiss) private var dismiss

    /// Có tô được câu không (tính một lần, cùng phép tìm với khung PDF) — để nói rõ khi không tô được, không im lặng.
    private var marked: Bool {
        guard file.highlight != nil, let doc = PDFDocument(data: file.data) else { return false }
        return !quoteSelections(in: doc, quote: file.highlight ?? "", value: file.value, page: file.page).isEmpty
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "doc.richtext").foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(file.filename).font(.t14m).lineLimit(1)
                    if let page = file.page { Text("Trang \(page)").font(.t12).foregroundStyle(.secondary) }
                    if let q = file.highlight, !q.isEmpty {
                        Text(file.scan ? "Bản scan — không tô được trên ảnh, đối chiếu bằng mắt: “\(q)”"
                                       : marked ? "Đã tô vàng câu nguyên văn: “\(q)”" : "Không tìm thấy nguyên câu trên trang — đối chiếu: “\(q)”")
                            .font(.t12).foregroundStyle(file.scan || !marked ? Color.warn : Color.secondary).lineLimit(2)
                    }
                }
                Spacer()
                Button { _ = saveFile(file.data, suggestedName: file.filename) } label: { Label("Lưu", systemImage: "arrow.down.doc") }
                    .buttonStyle(.bordered)
                Button("Đóng") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            .padding(12)
            Divider()
            PDFKitView(data: file.data, page: file.page, highlight: file.highlight, value: file.value)
        }
        .sheetFrame(minWidth: 640, idealWidth: 820, minHeight: 600, idealHeight: 880)
    }
}

#if os(macOS)
struct PDFKitView: NSViewRepresentable {
    let data: Data
    let page: Int?
    let highlight: String?
    let value: String?
    func makeNSView(context: Context) -> PDFView { configured(PDFView()) }
    func updateNSView(_ view: PDFView, context: Context) {}
}
#else
struct PDFKitView: UIViewRepresentable {
    let data: Data
    let page: Int?
    let highlight: String?
    let value: String?
    func makeUIView(context: Context) -> PDFView { configured(PDFView()) }
    func updateUIView(_ view: PDFView, context: Context) {}
}
#endif

private extension PDFKitView {
    func configured(_ view: PDFView) -> PDFView {
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.document = PDFDocument(data: data)
        // "## Trang N" trong chữ rút ra = trang N của file (đếm từ 1) ⇒ PDFKit đếm từ 0
        if let q = highlight, let doc = view.document {
            let found = quoteSelections(in: doc, quote: q, value: value, page: page)
            for sel in found {
                for line in sel.selectionsByLine() {
                    guard let pg = line.pages.first else { continue }
                    let mark = PDFAnnotation(bounds: line.bounds(for: pg).insetBy(dx: -2, dy: -1), forType: .highlight, withProperties: nil)
                    mark.color = PlatformColor.systemYellow.withAlphaComponent(0.55)
                    pg.addAnnotation(mark)
                }
            }
            if let first = found.first {
                DispatchQueue.main.async { view.go(to: first) }
                return view
            }
        }
        if let n = page, let doc = view.document, let p = doc.page(at: max(0, min(n - 1, doc.pageCount - 1))) {
            DispatchQueue.main.async { view.go(to: p) }
        }
        return view
    }
}

#if os(macOS)
private typealias PlatformColor = NSColor
#else
private typealias PlatformColor = UIColor
#endif

/// Tìm câu nguyên văn trong PDF: nguyên câu → câu gộp khoảng trắng → giá trị → các cụm có chữ số (số tiền, số hiệu, ngày) dài nhất trước.
/// Chữ trong PDF hay khác khoảng trắng so với câu rút ra ⇒ lùi dần tới phần đặc trưng nhất thay vì không tô gì. Ưu tiên trang của bằng chứng.
func quoteSelections(in doc: PDFDocument, quote: String, value: String?, page: Int?) -> [PDFSelection] {
    let squeezed = quote.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    let tokens = squeezed.split(separator: " ").map(String.init).filter { $0.count >= 4 && $0.contains(where: \.isNumber) }
        .sorted { $0.count > $1.count }
    var tried = Set<String>()
    for c in [quote, squeezed, value ?? ""] + tokens {
        let s = c.trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: "“”\"|*")))
        guard s.count >= 3, tried.insert(s.lowercased()).inserted else { continue }
        let hits = doc.findString(s, withOptions: .caseInsensitive)
        guard !hits.isEmpty else { continue }
        if let n = page, let target = doc.page(at: n - 1) {
            let onPage = hits.filter { $0.pages.contains(target) }
            if !onPage.isEmpty { return onPage }
        }
        return hits
    }
    return []
}
