import ImageIO
import SwiftUI

/// Tab "Tệp" của một trao đổi (0128, như "Ảnh, file, link" của Zalo): trang thẻ do server mô tả (``talk.files``) — các nhóm là ``tabs`` của trang
/// (Ảnh / Tài liệu / Link, thêm "Tài liệu của đội" ở phòng đội), dòng là khối danh sách. App chỉ vẽ: nhóm Ảnh thành lưới ảnh thu nhỏ (tải qua đường tệp
/// sẵn có, server kiểm quyền đọc luồng mỗi lần), bấm một dòng ⇒ mở đúng tin trong luồng; dòng không phải tin (tài liệu của đội) ⇒ mở thẻ của nó.
/// iPhone phủ cả màn hình (``filesCover``); cùng cách vẽ với `web/src/components/blocks/thread-files.tsx`.
struct ThreadFilesView: View {
    let ref: ThreadBlock.OpenRef
    let onJump: (Int) -> Void
    let onOpen: (OpenRequest) -> Void
    let onClose: () -> Void
    @Environment(Desk.self) private var desk: Desk?
    @Environment(\.openURL) private var openURL
    @State private var kind: String?
    @State private var page: CardPage?
    @State private var error: String?
    @State private var loading = true

    private var current: String { page?.tabs?.current ?? kind ?? "media" }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Tệp").font(.t16)
                    Text("Ảnh, tài liệu, link đã gửi — chỉ người đọc được trao đổi này thấy").font(.t12).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer(minLength: 8)
                if loading && page != nil { ProgressView().controlSize(.small) }
                Button("Xong", action: onClose).font(.t14m).keyboardShortcut(.cancelAction)
            }
            .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 10)
            if let tabs = page?.tabs {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(tabs.items) { t in
                            let on = t.key == current
                            Button {
                                guard !on else { return }
                                loading = true
                                kind = t.key
                            } label: {
                                Label(t.title, systemImage: Self.icon(t.key))
                                    .font(.t12m).lineLimit(1)
                                    .padding(.horizontal, 10).padding(.vertical, 7)
                                    .foregroundStyle(on ? Color.white : Color.primary)
                                    .background(Capsule().fill(on ? Color.accent : Color.primary.opacity(0.07)))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 16)
                }
                .padding(.bottom, 8)
            }
            Rectangle().fill(Color.hairline).frame(height: 1)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let error { Label(error, systemImage: "exclamationmark.triangle").font(.t12).foregroundStyle(Color.bad) }
                    if page == nil && loading { ProgressView().frame(maxWidth: .infinity).padding(.vertical, 40) }
                    ForEach(Array((page?.blocks ?? []).enumerated()), id: \.offset) { _, b in block(b) }
                }
                .padding(16)
            }
        }
        .background(Color.canvas)
        .task(id: kind) { await load() }
    }

    @ViewBuilder private func block(_ b: CardPage.Block) -> some View {
        switch b {
        case .callout(_, let title, let text, _, _, _):
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.t14m)
                if !text.isEmpty { Text(text).font(.t12).foregroundStyle(.secondary) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12).padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.surface))
        case .list(let title, let items):
            VStack(alignment: .leading, spacing: 8) {
                if !title.isEmpty { Text(title).font(.t12m).foregroundStyle(.secondary) }
                if current == "media" {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 4), count: 3), spacing: 4) {
                        ForEach(Array(items.enumerated()), id: \.offset) { _, it in mediaCell(it) }
                    }
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(items.enumerated()), id: \.offset) { i, it in
                            if i > 0 { Divider().padding(.leading, 44) }
                            row(it)
                        }
                    }
                    .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.surface))
                }
            }
        default:
            EmptyView()
        }
    }

    private func mediaCell(_ it: CardPage.ListItem) -> some View {
        Button { tap(it) } label: {
            Color.surface2
                .aspectRatio(1, contentMode: .fit)
                .overlay {
                    if let f = it.file, f.is_image {
                        TalkThumb(fileId: f.id)
                    } else {
                        VStack(spacing: 4) {
                            Image(systemName: "photo").font(.system(size: 18)).foregroundStyle(.secondary)
                            Text(it.title).font(.t12).foregroundStyle(.secondary).lineLimit(2).multilineTextAlignment(.center)
                        }
                        .padding(4)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .contextMenu {
            Button { tap(it) } label: { Label("Mở tin đã gửi", systemImage: "bubble.left") }
            if let f = it.file { Button { Task { await openFile(f) } } label: { Label("Xem / lưu ảnh", systemImage: "square.and.arrow.down") } }
        }
        .help([it.title, it.subtitle ?? ""].filter { !$0.isEmpty }.joined(separator: " — "))
    }

    private func row(_ it: CardPage.ListItem) -> some View {
        let link = Self.link(it.title)
        return HStack(spacing: 4) {
            Button { tap(it) } label: {
                HStack(spacing: 10) {
                    Image(systemName: link != nil ? "link" : it.file != nil ? "doc.text" : "books.vertical")
                        .font(.system(size: 15)).foregroundStyle(Color.accent).frame(width: 24)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(it.title).font(.t14m).lineLimit(1).truncationMode(.middle)
                        if let s = it.subtitle, !s.isEmpty { Text(s).font(.t12).foregroundStyle(.secondary).lineLimit(2) }
                    }
                    Spacer(minLength: 4)
                    if let b = it.badge, !b.isEmpty { Pill(text: b, tone: it.tone == "accent" ? .accent : .secondary) }
                }
                .padding(.leading, 10).padding(.vertical, 9)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if let link {
                Button { openURL(link) } label: { Image(systemName: "arrow.up.right.square").frame(width: 36, height: 36) }
                    .buttonStyle(.plain).foregroundStyle(.secondary).help("Mở link")
            } else if let f = it.file {
                Button { Task { await openFile(f) } } label: { Image(systemName: "arrow.down.circle").frame(width: 36, height: 36) }
                    .buttonStyle(.plain).foregroundStyle(.secondary).help("Xem / tải tệp")
            }
        }
        .padding(.trailing, 4)
    }

    /// Dòng có lối về đúng tin (``talk.thread`` + ``around``) ⇒ mở tin trong luồng đang xem; còn lại ⇒ mở thẻ của dòng.
    private func tap(_ it: CardPage.ListItem) {
        guard let o = it.open else { return }
        if o.query == "talk.thread", case .number(let n)? = o.input["around"], n > 0 { onJump(Int(n)); return }
        onOpen(OpenRequest(title: it.title, query: o.query, input: o.input))
    }

    /// Chỉ mở link web (http / https) — chữ của dòng là đường dẫn server đã rút từ tin.
    private static func link(_ text: String) -> URL? {
        guard text.hasPrefix("https://") || text.hasPrefix("http://"), let u = URL(string: text), u.host() != nil else { return nil }
        return u
    }

    private static func icon(_ key: String) -> String {
        switch key {
        case "media": "photo"
        case "link": "link"
        case "team": "books.vertical"
        default: "doc.text"
        }
    }

    private func openFile(_ f: ThreadBlock.File) async {
        guard let desk else { return }
        do {
            let data = try await desk.api.download("/v1/talk/files/\(f.id)")
            if let name = openDownloadedFile(data, name: Talk.safeName(f.filename, contentType: f.content_type)) { desk.show("Đã lưu \(name)") }
        } catch {
            desk.show(error.localizedDescription, isError: true)
        }
    }

    private func load() async {
        guard let desk else { return }
        var input = ref.input
        if let kind { input["kind"] = .string(kind) }
        do {
            guard case .card(let c) = try await desk.api.query("talk.files", input) else { throw APIError(message: "Không đọc được danh sách tệp") }
            page = c
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        loading = false
    }
}

/// Ảnh thu nhỏ của một tệp trong trao đổi: tải qua API (kèm danh tính — server kiểm quyền đọc luồng), giải mã ở cỡ nhỏ (ImageIO) cho lưới nhiều ảnh
/// không ngốn bộ nhớ điện thoại.
struct TalkThumb: View {
    let fileId: String
    var maxPixel = 360
    @Environment(Desk.self) private var desk: Desk?
    @State private var image: Image?
    @State private var failed = false

    var body: some View {
        Group {
            if let image {
                image.resizable().scaledToFill()
            } else if failed {
                Image(systemName: "photo").foregroundStyle(.tertiary)
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .clipped()
        .task(id: fileId) { await load() }
    }

    private func load() async {
        guard let desk, image == nil,
              let data = try? await desk.api.download("/v1/talk/files/\(fileId)"),
              let src = CGImageSourceCreateWithData(data as CFData, nil),
              let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                                                   kCGImageSourceCreateThumbnailWithTransform: true,
                                                                   kCGImageSourceThumbnailMaxPixelSize: maxPixel] as CFDictionary)
        else { failed = true; return }
        image = Image(decorative: cg, scale: 1)
    }
}
