import SwiftUI
import UniformTypeIdentifiers

/// Tải PDF / Word / chữ lên kho tài liệu (quyết định 0051). Kệ chỉ gồm kệ người tải ĐỌC được (server kiểm lại); PDF scan do model đọc —
/// có thể mất vài chục giây, hiện thời gian chờ.
struct KBUploadSheet: View {
    let api: API
    let onDone: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var file: (name: String, data: Data)?
    @State private var title = ""
    @State private var shelf = "common"
    @State private var kind = "other"
    @State private var shelves: [Option] = []
    @State private var kinds: [Option] = []
    @State private var importingFile = false
    @State private var sending = false
    @State private var started = Date()
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Tải file lên kho tài liệu").font(.t20)
            Text("PDF, Word (.docx), .txt, .md — tối đa 30 MB. PDF chụp / scan trợ lý tự đọc chữ; tìm kiếm và trợ lý trích được theo trang.")
                .font(.t12).foregroundStyle(.secondary)
            Form {
                LabeledContent("File") {
                    HStack {
                        Text(file?.name ?? "Chưa chọn").foregroundStyle(file == nil ? .secondary : .primary).lineLimit(1)
                        Spacer()
                        Button("Chọn file…") { importingFile = true }
                    }
                }
                TextField("Tên tài liệu", text: $title)
                Picker("Kệ (ai được đọc)", selection: $shelf) { ForEach(shelves, id: \.value) { Text($0.label).tag($0.value) } }
                Picker("Loại", selection: $kind) { ForEach(kinds, id: \.value) { Text($0.label).tag($0.value) } }
            }
            .formStyle(.grouped)
            if let error { Label(error, systemImage: "exclamationmark.triangle.fill").font(.t14).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                if sending {
                    TimelineView(.periodic(from: .now, by: 1)) { ctx in
                        Label("Đang đọc file… \(Int(ctx.date.timeIntervalSince(started))) giây", systemImage: "hourglass").font(.t12)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Button("Huỷ") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(sending ? "Đang tải…" : "Tải lên") { Task { await send() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(sending || file == nil || title.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20)
        .sheetFrame(minWidth: 520)
        .fileImporter(isPresented: $importingFile, allowedContentTypes: [.pdf, .plainText, UTType(filenameExtension: "docx") ?? .data,
                                                                         UTType(filenameExtension: "md") ?? .plainText]) { result in
            guard case .success(let url) = result else { return }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            guard let data = try? Data(contentsOf: url) else { error = "Không đọc được file"; return }
            file = (url.lastPathComponent, data)
            if title.isEmpty { title = url.deletingPathExtension().lastPathComponent }
        }
        .task { await loadOptions() }
    }

    private func loadOptions() async {
        shelves = (try? await api.options("kb.shelf_options")) ?? [Option(value: "common", label: "Chung")]
        if let spec = try? await api.form("kb.add_document"), let k = spec.fields.first(where: { $0.key == "kind" })?.options {
            kinds = k
        }
        if kinds.isEmpty { kinds = [Option(value: "other", label: "Khác")] }
        if !kinds.contains(where: { $0.value == kind }) { kind = kinds[0].value }
    }

    private func send() async {
        guard let file else { return }
        sending = true
        started = .now
        error = nil
        defer { sending = false }
        do {
            let r = try await api.uploadDocument(file.data, filename: file.name, title: title.trimmingCharacters(in: .whitespaces),
                                                 shelf: shelf, kind: kind)
            if r.status == "ok" {
                onDone("Đã thêm “\(title)” vào kho")
                dismiss()
            } else {
                error = r.error.isEmpty ? r.status : r.error
            }
        } catch {
            self.error = error.localizedDescription
        }
    }
}
