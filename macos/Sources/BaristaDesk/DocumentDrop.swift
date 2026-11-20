import Foundation
import SwiftUI
#if os(macOS)
import AppKit
#endif

/// Thả CHỨNG TỪ (PDF / Word) vào ô chat ⇒ hộp tải lên: NGƯỜI chọn kệ (thấy rõ ai đọc được), rồi mới tải vào kho tài liệu như nút "Tải lên"
/// (đọc chữ; bản scan do model chép — 0051; file gốc lưu kèm — 0052). KHÔNG tự lưu vào kệ "Chung" (review 04/11 — bản demo mở công khai). Chỉ
/// tài liệu ĐÃ LƯU vào câu mời trợ lý (kèm mã tài liệu); chờ duyệt / lỗi báo riêng. Người sửa / bấm gửi — app không tự gửi. Ảnh vẫn đi đường cũ
/// (đọc trong lượt, 0050).
extension Desk {
    static let documentTypes: Set<String> = ["pdf", "docx"]

    #if os(macOS)
    /// Một lần thả: có tệp PDF / Word ⇒ trả về cho hộp tải lên; không thì đường ảnh như cũ. Kiểm đường dẫn TRƯỚC — NSImage đọc được cả PDF nên
    /// thử ảnh trước sẽ biến chứng từ thành ảnh đính kèm.
    func handleDrop(_ providers: [NSItemProvider], documents: @escaping ([URL]) -> Void) {
        Task { @MainActor in
            var docs: [URL] = []
            for p in providers where p.canLoadObject(ofClass: URL.self) {
                let url: URL? = await withCheckedContinuation { cont in _ = p.loadObject(ofClass: URL.self) { u, _ in cont.resume(returning: u) } }
                if let url, url.isFileURL, Desk.documentTypes.contains(url.pathExtension.lowercased()) { docs.append(url) }
            }
            if !docs.isEmpty {
                documents(docs)
                return
            }
            for p in providers where p.canLoadObject(ofClass: NSImage.self) {
                let img: NSImage? = await withCheckedContinuation { cont in _ = p.loadObject(ofClass: NSImage.self) { o, _ in cont.resume(returning: o as? NSImage) } }
                if let img, let a = ImageIntake.attachment(from: img) { attach(a) }
            }
        }
    }
    #endif
}

/// Kệ người này thêm được (``kb.upload_shelves``) — kệ hẹp trước, kệ cả công ty sau cùng; KHÔNG chọn sẵn.
struct UploadShelf: Decodable, Hashable, Identifiable {
    let value, label, audience: String
    let everyone: Bool
    var id: String { value }
}

/// Một tệp đã lưu + gợi ý loại SERVER soạn sẵn (``kb.suggest_type``). ``hint`` nil + ``reading`` true = đang đọc; app chỉ VẼ, không tự suy.
struct Detected: Identifiable, Hashable {
    let title: String
    let documentId: String
    var reading = true
    var chip = ""
    var warn = false
    var prompt = ""
    var id: String { documentId }
}

struct DocumentDropSheet: View {
    let urls: [URL]
    @Environment(Desk.self) private var desk
    @Environment(\.dismiss) private var dismiss
    @State private var shelves: [UploadShelf]?
    @State private var shelf = ""
    @State private var busy = ""
    @State private var detected: [Detected]?
    @State private var pending: [String] = []
    @State private var failed: [String] = []
    @State private var work: Task<Void, Never>?
    @State private var closed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(detected == nil ? "Tải \(urls.count) chứng từ vào kho tài liệu" : "Đã lưu \(detected?.count ?? 0)/\(urls.count) chứng từ").font(.t16)
            Text(detected == nil
                 ? "Chọn kệ — ai đọc được kệ đó sẽ thấy các chứng từ này. Xong em điền sẵn câu mời trợ lý."
                 : "Loại do hệ thống đọc từ nội dung và đề xuất. Tệp ghi “chưa chắc” sẽ được hỏi lại, không tự xếp loại.")
                .font(.t12).foregroundStyle(.secondary)
            if let detected {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(detected) { d in
                        HStack(spacing: 8) {
                            Label(d.title, systemImage: "doc.text").font(.t14).lineLimit(1)
                            Spacer(minLength: 8)
                            chip(d)
                        }
                    }
                    ForEach(pending, id: \.self) { n in Text("\(n) — chờ duyệt, chưa vào kho").font(.t12).foregroundStyle(Color.warn) }
                    ForEach(failed, id: \.self) { n in Text("\(n) — không tải lên được").font(.t12).foregroundStyle(Color.bad) }
                }
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(urls, id: \.self) { u in Label(u.lastPathComponent, systemImage: "doc.text").font(.t14) }
                }
            }
            if detected == nil {
                if let shelves {
                    if shelves.isEmpty { Text("Bạn chưa thêm được tài liệu vào kệ nào.").font(.t12).foregroundStyle(Color.bad) }
                    VStack(spacing: 6) { ForEach(shelves) { s in shelfRow(s) } }
                } else {
                    ProgressView().controlSize(.small)
                }
            }
            if detected == nil, shelves?.first(where: { $0.value == shelf })?.everyone == true {
                Text("Kệ này cả công ty đọc được.").font(.t12).foregroundStyle(Color.warn)
            }
            HStack {
                if !busy.isEmpty {
                    Text(busy).font(.t12).foregroundStyle(.secondary)
                } else if detected?.contains(where: \.reading) == true {
                    Text("Đang đọc loại chứng từ…").font(.t12).foregroundStyle(.secondary)
                }
                Spacer()
                if detected != nil {
                    Button("Xong") { finish() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)   // bấm được khi model còn chậm: không khoá người
                } else {
                    Button(busy.isEmpty ? "Huỷ" : "Dừng tải") {
                        if busy.isEmpty { dismiss() } else { work?.cancel() }
                    }.keyboardShortcut(.cancelAction)
                    Button("Tải lên") { work = Task { await upload() } }.buttonStyle(.borderedProminent).disabled(shelf.isEmpty || !busy.isEmpty)
                }
            }
        }
        .padding(20)
        .sheetFrame(minWidth: 460)
        .task {
            shelves = (try? await desk.api.data("kb.upload_shelves") as [UploadShelf]) ?? []
        }
    }

    private func shelfRow(_ s: UploadShelf) -> some View {
        Button { shelf = s.value } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: shelf == s.value ? "largecircle.fill.circle" : "circle").foregroundStyle(shelf == s.value ? Color.accent : .secondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(s.label).font(.t14m)
                    Text("Ai đọc được: \(s.audience)").font(.t12).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 8, style: .continuous).stroke(shelf == s.value ? Color.accent : Color.secondary.opacity(0.3)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!busy.isEmpty)
    }

    private func upload() async {
        var saved: [(title: String, id: String)] = []
        var pending: [String] = []
        var failed: [String] = []
        for (i, url) in urls.enumerated() {
            if Task.isCancelled { break }
            let name = url.lastPathComponent
            busy = "Đang tải lên \(i + 1)/\(urls.count): \(name)…"
            guard let data = try? Data(contentsOf: url) else { failed.append(name); continue }
            let title = url.deletingPathExtension().lastPathComponent
            do {
                let r = try await desk.api.uploadDocument(data, filename: name, title: title, shelf: shelf, kind: "other")
                if r.status == "ok", case .object(let o) = r.output, case .string(let id) = o["document_id"], !id.isEmpty {
                    saved.append((title, id))
                } else if r.status == "pending_approval" {
                    pending.append(name)   // chờ duyệt ⇒ tài liệu CHƯA có trong kho — không mời trợ lý đọc
                } else {
                    failed.append(name)
                }
            } catch {
                failed.append(name)
            }
        }
        busy = ""
        self.pending = pending
        self.failed = failed
        if Task.isCancelled && saved.isEmpty { dismiss(); return }
        // Đọc loại SAU khi lưu (cần mã tài liệu): server soạn sẵn chip; tệp nào xong hiện trước. Lỗi ⇒ không chip, không chặn.
        detected = saved.map { Detected(title: $0.title, documentId: $0.id) }
        await withTaskGroup(of: (String, DocumentTypeHint?).self) { group in
            for item in saved {
                group.addTask {
                    (item.id, try? await desk.api.data("kb.suggest_type", ["document_id": .string(item.id)]) as DocumentTypeHint)
                }
            }
            for await (id, hint) in group {
                guard let i = detected?.firstIndex(where: { $0.documentId == id }) else { continue }
                detected?[i].reading = false
                detected?[i].chip = hint?.chip ?? ""
                detected?[i].warn = hint?.tone == "warn"
                detected?[i].prompt = hint?.prompt ?? ""
            }
        }
        if !closed, pending.isEmpty, failed.isEmpty, detected?.contains(where: { !$0.chip.isEmpty }) == false { finish() }   // không tệp nào có gợi ý ⇒ như hộp tải lên thường
    }

    /// Đóng + đưa câu mời (kèm gợi ý loại server soạn) vào ô soạn tin — người sửa rồi gửi. Không tự gửi.
    private func finish() {
        closed = true
        work?.cancel()
        let rows = detected ?? []
        dismiss()
        if !pending.isEmpty { desk.show("Chờ duyệt, chưa vào kho: \(pending.joined(separator: ", "))") }
        if !failed.isEmpty { desk.show("Không tải lên được: \(failed.joined(separator: ", "))", isError: true) }
        guard !rows.isEmpty else { return }
        if pending.isEmpty && failed.isEmpty { desk.show("Đã lưu \(rows.count) chứng từ vào kho") }
        let list = rows.map { "“\($0.title)” (mã tài liệu \($0.documentId))" + ($0.prompt.isEmpty ? "" : " · \($0.prompt)") }.joined(separator: ", ")
        desk.draft = (desk.draft.isEmpty ? "" : desk.draft + "\n") + "Tôi vừa tải lên \(rows.count) chứng từ: \(list). "
    }

    @ViewBuilder private func chip(_ d: Detected) -> some View {
        if d.reading {
            Text("đang đọc…").font(.t12).foregroundStyle(.secondary)
        } else if !d.chip.isEmpty {
            Text(d.chip)
                .font(.t12m)
                .foregroundStyle(d.warn ? Color.warn : Color.accent)
                .padding(.horizontal, 10).padding(.vertical, 3)
                .overlay(Capsule().stroke(d.warn ? Color.secondary.opacity(0.35) : Color.accent.opacity(0.4)))
        }
    }
}

/// Kết quả ``kb.suggest_type`` — server soạn sẵn chữ + tông; app không tự chọn thay người.
struct DocumentTypeHint: Decodable {
    var applicable = false
    var chip = ""
    var tone = ""
    var prompt = ""
}
