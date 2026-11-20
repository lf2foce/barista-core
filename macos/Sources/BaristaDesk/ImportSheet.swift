import SwiftUI
import UniformTypeIdentifiers
#if os(macOS)
import AppKit
#endif

/// Nhập hàng loạt cho một lệnh: tải mẫu → chọn file .xlsx hoặc dán từ Excel → CHẠY THỬ (server chạy thật rồi bỏ, báo lỗi
/// từng dòng) → nhập thật. Nhập lại cùng file không nhân đôi (server khoá theo nội dung dòng).
struct ImportSheet: View {
    let api: API
    let action: ActionSpec
    let onDone: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var file: (name: String, data: Data)?
    @State private var pasted = ""
    @State private var report: ImportReport?
    @State private var error: String?
    @State private var busy = false
    @State private var pickingFile = false  // iPhone: hộp chọn tệp của hệ thống

    private var pastedRows: [Input] {
        let lines = pasted.replacingOccurrences(of: "\r", with: "").split(separator: "\n", omittingEmptySubsequences: true)
        guard let head = lines.first else { return [] }
        let headers = head.split(separator: "\t", omittingEmptySubsequences: false).map { $0.trimmingCharacters(in: .whitespaces) }
        return lines.dropFirst().enumerated().map { i, line in
            var row: Input = ["_line": .number(Double(i + 2))]
            for (h, v) in zip(headers, line.split(separator: "\t", omittingEmptySubsequences: false)) where !h.isEmpty {
                let t = v.trimmingCharacters(in: .whitespaces)
                if !t.isEmpty { row[h] = .string(t) }
            }
            return row
        }
    }

    private var hasInput: Bool { file != nil || !pastedRows.isEmpty }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Nhập từ Excel — \(action.title)").font(.t20)
            HStack {
                Button { Task { await saveTemplate() } } label: { Label("Tải mẫu Excel", systemImage: "doc.badge.arrow.up") }
                Button { pickFile() } label: { Label("Chọn file .xlsx…", systemImage: "folder") }
                if let file { Text(file.name).font(.t14m); Button("Bỏ") { self.file = nil; report = nil } .controlSize(.small) }
                Spacer()
            }
            if file == nil {
                Text("…hoặc dán vùng đã sao chép từ Excel (gồm dòng tiêu đề):").font(.t12).foregroundStyle(.secondary)
                TextEditor(text: $pasted)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(minHeight: 110)
                    .border(.separator)
                    .onChange(of: pasted) { report = nil }
            }
            if let error { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red).font(.t14) }
            if let r = report { reportView(r) }
            Spacer(minLength: 0)
            HStack {
                Spacer()
                Button("Đóng") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Chạy thử") { Task { await run(dryRun: true) } }
                    .disabled(!hasInput || busy)
                Button(report.map { "Nhập thật \($0.ok) dòng hợp lệ" } ?? "Nhập thật") { Task { await run(dryRun: false) } }
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || report?.dry_run != true || (report?.ok ?? 0) == 0)
            }
        }
        .padding(20)
        #if os(macOS)
        .frame(minWidth: 720, minHeight: 520)
        #endif
        .fileImporter(isPresented: $pickingFile, allowedContentTypes: [xlsxType, .commaSeparatedText]) { result in
            guard case .success(let url) = result else { return }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            if let data = try? Data(contentsOf: url) { file = (url.lastPathComponent, data); report = nil }
        }
    }

    private func reportView(_ r: ImportReport) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Text(r.dry_run ? "Chạy thử (chưa ghi gì)" : "Đã nhập").font(.t16)
                Badge(text: "\(r.ok) hợp lệ", tone: "green")
                if r.failed > 0 { Badge(text: "\(r.failed) lỗi", tone: "red") }
            }
            if !r.unknown_columns.isEmpty {
                Text("Cột không nhận ra (bỏ qua): \(r.unknown_columns.joined(separator: ", "))").font(.t12).foregroundStyle(.orange)
            }
            List(r.rows.filter { $0.status != "ok" || !r.dry_run }) { line in
                HStack(alignment: .firstTextBaseline) {
                    Text("Dòng \(line.line)").font(.t12m).frame(width: 70, alignment: .leading)
                    Badge(text: Self.status[line.status] ?? line.status, tone: line.status == "ok" ? "green" : line.status == "pending_approval" ? "orange" : "red")
                    Text(line.error ?? "").font(.t12).textSelection(.enabled)
                }
            }
            .frame(minHeight: 140)
        }
    }

    private static let status = ["ok": "Hợp lệ", "invalid": "Sai dữ liệu", "conflict": "Trùng", "denied": "Không có quyền",
                                 "not_found": "Không thấy", "pending_approval": "Chờ duyệt", "error": "Lỗi hệ thống"]

    private func pickFile() {
        #if os(macOS)
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [xlsxType, .commaSeparatedText]
        if panel.runModal() == .OK, let url = panel.url, let data = try? Data(contentsOf: url) {
            file = (url.lastPathComponent, data)
            report = nil
        }
        #else
        pickingFile = true
        #endif
    }

    private func saveTemplate() async {
        do {
            let data = try await api.template(action.command)
            _ = saveFile(data, suggestedName: "Mẫu nhập - \(action.title).xlsx", type: xlsxType)
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func run(dryRun: Bool) async {
        busy = true
        defer { busy = false }
        do {
            if let file {
                report = try await api.importXLSX(action.command, data: file.data, dryRun: dryRun)
            } else {
                report = try await api.importRows(action.command, rows: pastedRows, dryRun: dryRun)
            }
            error = nil
            if !dryRun { onDone() }
        } catch {
            self.error = error.localizedDescription
        }
    }
}
