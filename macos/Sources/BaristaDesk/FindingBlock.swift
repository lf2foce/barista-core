import PDFKit
import SwiftUI

/// Khối "finding" (kernel/ui.py b_finding): một phát hiện có BẰNG CHỨNG mở được — sai biệt chứng từ (0072), điều khoản lệch… Mỗi bằng chứng
/// là một dòng bấm được: file gốc PDF ⇒ mở đúng trang, tô vàng câu nguyên văn (bản scan ⇒ chỉ mở đúng trang, nói rõ không tô được); tài liệu
/// khác (Word, chữ dán) ⇒ mở trang tài liệu trong khung làm việc — không hứa "đúng trang trong file".
struct FindingBlock: Decodable {
    struct Evidence: Decodable, Hashable {
        let label, quote: String
        let value: String?
        let page: Int?
        let document_id: String?
        let scan: Bool?
        let filename: String?
        let note: String?
        var isPDF: Bool { (filename ?? "").lowercased().hasSuffix(".pdf") }
    }
    let tone, title, text: String
    let badge: String?
    let facts: [String]
    let evidence: [Evidence]
    let actions: [ActionSpec]

    private enum K: String, CodingKey { case tone, title, text, badge, facts, evidence, actions }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        tone = try c.decodeIfPresent(String.self, forKey: .tone) ?? "muted"
        title = try c.decode(String.self, forKey: .title)
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        badge = try c.decodeIfPresent(String.self, forKey: .badge)
        facts = try c.decodeIfPresent([String].self, forKey: .facts) ?? []
        evidence = try c.decodeIfPresent([Evidence].self, forKey: .evidence) ?? []
        actions = try c.decodeIfPresent([ActionSpec].self, forKey: .actions) ?? []
    }
}

struct FindingView: View {
    let block: FindingBlock
    let run: (ActionSpec) -> Void
    @Environment(Desk.self) private var desk: Desk?
    @State private var pdf: OriginalFile?
    @State private var loading: String?

    private var accent: Color { block.tone == "muted" ? .secondary : Color.card(block.tone) }

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            Rectangle().fill(accent).frame(width: 4)
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(block.title).font(.t14m).foregroundStyle(block.tone == "muted" ? Color.secondary : Color.primary)
                    Spacer(minLength: 8)
                    if let b = block.badge, !b.isEmpty {
                        Text(b).font(.t12m).foregroundStyle(accent)
                            .padding(.horizontal, 8).padding(.vertical, 3)
                            .background(Capsule().fill(accent.opacity(0.12)))
                    }
                }
                Text(block.text).font(.t16).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                if !block.evidence.isEmpty {
                    VStack(spacing: 6) {
                        ForEach(block.evidence, id: \.self) { e in
                            EvidenceRow(e: e, loading: loading == e.document_id && e.document_id != nil, enabled: e.document_id != nil && desk != nil) {
                                Task { await open(e) }
                            }
                        }
                    }
                }
                ForEach(block.facts, id: \.self) { f in
                    Text(f).font(.t12).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                if !block.actions.isEmpty {
                    HStack(spacing: 8) {
                        ForEach(block.actions) { a in
                            if a.style == "primary" { Button(a.title) { run(a) }.buttonStyle(.borderedProminent) }
                            else { Button(a.title) { run(a) }.buttonStyle(.bordered) }
                        }
                    }
                    .controlSize(.regular)
                }
            }
            .padding(14)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.surface2))
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .sheet(item: $pdf) { PDFSheet(file: $0) }
    }

    private func open(_ e: FindingBlock.Evidence) async {
        guard let desk, let id = e.document_id else { return }
        guard e.isPDF else {   // Word / chữ dán: không có trang PDF để mở — mở trang tài liệu (chữ) trong kho
            var input: Input = ["document_id": .string(id)]
            if let p = e.page { input["page"] = .number(Double(p)) }
            await desk.openTarget(query: "kb.document", input: input, title: e.label)
            return
        }
        loading = id
        defer { loading = nil }
        do {
            let data = try await desk.api.download("/v1/kb/documents/\(id)/file")
            guard PDFDocument(data: data) != nil else { desk.show("Chứng từ này không có file PDF gốc", isError: true); return }
            pdf = OriginalFile(title: e.label, filename: e.label, data: data, page: e.page, highlight: e.quote.isEmpty ? nil : e.quote,
                               value: e.value, scan: e.scan == true)
        } catch {
            desk.show(error.localizedDescription, isError: true)
        }
    }
}

/// Một phía bằng chứng: văn bản · ô, trang, câu nguyên văn. Có tài liệu ⇒ cả dòng bấm được, rê chuột thấy đổi nền + con trỏ bàn tay, mở file gốc đúng trang, tô câu.
private struct EvidenceRow: View {
    let e: FindingBlock.Evidence
    let loading: Bool
    let enabled: Bool
    let open: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: open) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: e.scan == true ? "doc.viewfinder" : "doc.text.magnifyingglass")
                    .font(.system(size: 15)).foregroundStyle(Color.accent).frame(width: 18)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(e.label).font(.t12m)
                        if let p = e.page { Text("trang \(p)").font(.t12).foregroundStyle(.secondary) }
                        if e.scan == true { Text("bản scan").font(.t12).foregroundStyle(Color.warn) }
                    }
                    // khoảng trắng của chữ rút từ PDF gộp lại khi HIỂN THỊ; câu gốc giữ nguyên để tìm / tô trên trang
                    Text(quote)
                        .font(.system(.callout, design: .monospaced)).foregroundStyle(e.quote.isEmpty ? Color.secondary : Color.primary)
                        .lineLimit(3).multilineTextAlignment(.leading)
                }
                Spacer(minLength: 4)
                if loading { ProgressView().controlSize(.small) }
                else if enabled { Label("Xem bản gốc", systemImage: "arrow.up.forward.square").labelStyle(.titleAndIcon).font(.t12).foregroundStyle(hovering ? Color.accent : Color.secondary) }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(hovering && enabled ? Color.accent.opacity(0.10) : Color.surface))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .textSelection(.disabled)
        .disabled(!enabled)
        .onHover { inside in
            hovering = inside
            #if os(macOS)
            if inside && enabled { NSCursor.pointingHand.push() } else { NSCursor.pop() }
            #endif
        }
        .help(!enabled ? "" : e.isPDF ? "Mở chứng từ gốc ở đúng trang, tô câu nguyên văn" : "Mở tài liệu trong kho, đối chiếu câu nguyên văn")
    }

    private var quote: String {
        if e.quote.isEmpty { return (e.note ?? "").isEmpty ? "(không có trên chứng từ)" : (e.note ?? "") }
        return "“\(e.quote.split(whereSeparator: \.isWhitespace).joined(separator: " "))”"
    }
}
