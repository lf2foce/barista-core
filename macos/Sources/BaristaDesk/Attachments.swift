import SwiftUI
import UniformTypeIdentifiers
#if os(macOS)
import AppKit
#else
import UIKit
#endif

/// Ảnh đính kèm câu hỏi (ảnh đoạn chat với khách, phiếu cọc, giấy tờ…): trợ lý đọc trong lượt đó rồi soạn đề xuất; server KHÔNG lưu ảnh.
/// Nén JPEG, cạnh dài ≤ 2000 px (chữ trong ảnh chụp màn hình vẫn đọc rõ, gửi nhanh, dưới giới hạn 5 MB của server).
enum ImageIntake {
    static let maxSide: CGFloat = 2000

    #if os(macOS)
    static func attachment(from image: NSImage) -> Attachment? {
        guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        let w = CGFloat(cg.width), h = CGFloat(cg.height)
        let scale = min(1, maxSide / max(w, h))
        let size = NSSize(width: (w * scale).rounded(), height: (h * scale).rounded())
        guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width), pixelsHigh: Int(size.height), bitsPerSample: 8,
                                         samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0,
                                         bitsPerPixel: 0) else { return nil }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        NSColor.white.setFill()
        NSRect(origin: .zero, size: size).fill()   // ảnh trong suốt ⇒ nền trắng (JPEG không có kênh trong suốt)
        NSImage(cgImage: cg, size: NSSize(width: w, height: h)).draw(in: NSRect(origin: .zero, size: size))
        NSGraphicsContext.restoreGraphicsState()
        guard let jpeg = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.85]) else { return nil }
        return Attachment(data: jpeg, mediaType: "image/jpeg")
    }

    static func attachment(from data: Data) -> Attachment? { NSImage(data: data).flatMap(attachment(from:)) }

    /// Bảng nháp có ảnh (chụp màn hình ⌘⇧⌃4, ảnh copy từ Zalo / trình duyệt) ⇒ ảnh; chỉ có chữ ⇒ nil (để ô chat dán chữ như thường).
    static func fromPasteboard(_ pb: NSPasteboard = .general) -> Attachment? {
        if let urls = pb.readObjects(forClasses: [NSURL.self], options: [.urlReadingContentsConformToTypes: [UTType.image.identifier]]) as? [URL],
           let url = urls.first, let data = try? Data(contentsOf: url) { return attachment(from: data) }
        guard pb.canReadObject(forClasses: [NSImage.self], options: nil),
              let image = pb.readObjects(forClasses: [NSImage.self], options: nil)?.first as? NSImage else { return nil }
        return attachment(from: image)
    }

    /// Chọn ảnh từ máy (nút ảnh trên ô chat).
    @MainActor static func pick() -> [Attachment] {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = true
        panel.message = "Chọn ảnh (đoạn chat với khách, phiếu cọc, giấy tờ…) — trợ lý đọc rồi soạn đề xuất, ảnh không được lưu lại"
        guard panel.runModal() == .OK else { return [] }
        return panel.urls.compactMap { (try? Data(contentsOf: $0)).flatMap(attachment(from:)) }
    }
    #endif
}

/// Ảnh nhỏ (trên ô nhập, trong bong bóng câu hỏi).
struct Thumb: View {
    let data: Data
    var size: CGFloat = 56

    var body: some View {
        Group {
            #if os(macOS)
            if let img = NSImage(data: data) { Image(nsImage: img).resizable().scaledToFill() } else { Color.secondary.opacity(0.2) }
            #else
            if let img = UIImage(data: data) { Image(uiImage: img).resizable().scaledToFill() } else { Color.secondary.opacity(0.2) }
            #endif
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Color.primary.opacity(0.12)))
    }
}

/// Dải ảnh đang chờ gửi trên ô chat, mỗi ảnh có nút bỏ.
struct DraftImages: View {
    @Environment(Desk.self) private var desk

    var body: some View {
        if !desk.draftImages.isEmpty {
            HStack(spacing: 8) {
                ForEach(desk.draftImages) { a in
                    Thumb(data: a.data)
                        .overlay(alignment: .topTrailing) {
                            Button { desk.draftImages.removeAll { $0.id == a.id } } label: {
                                Image(systemName: "xmark.circle.fill").font(.system(size: 16)).foregroundStyle(.white, .black.opacity(0.6))
                            }
                            .buttonStyle(.plain).offset(x: 6, y: -6).help("Bỏ ảnh này")
                        }
                }
                Text("Trợ lý đọc ảnh rồi soạn đề xuất — ảnh không được lưu lại").font(.t12).foregroundStyle(.secondary)
                Spacer(minLength: 0)
            }
            .padding(.top, 6)
        }
    }
}
