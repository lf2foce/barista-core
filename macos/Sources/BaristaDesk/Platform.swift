import SwiftUI
import UniformTypeIdentifiers
#if os(macOS)
import AppKit
typealias PFont = NSFont
typealias PColor = NSColor
#else
import PhotosUI
import QuickLook
import SafariServices
import UIKit
typealias PFont = UIFont
typealias PColor = UIColor
#endif

/// Mã dùng chung Mac + iPhone (quyết định 0041): phần khác biệt của hệ điều hành gom về MỘT file này — phần còn lại của app
/// không viết `#if os(...)` rải rác.

extension PColor {
    #if os(macOS)
    static var rLabel: PColor { .labelColor }
    static var rSecondary: PColor { .secondaryLabelColor }
    static var rAccent: PColor { .controlAccentColor }
    #else
    static var rLabel: PColor { .label }
    static var rSecondary: PColor { .secondaryLabel }
    static var rAccent: PColor { .systemBlue }
    #endif
}

extension PFont {
    /// In nghiêng giữ nguyên họ chữ + cỡ chữ.
    func italicized() -> PFont {
        #if os(macOS)
        NSFontManager.shared.convert(self, toHaveTrait: .italicFontMask)
        #else
        fontDescriptor.withSymbolicTraits(.traitItalic).map { PFont(descriptor: $0, size: pointSize) } ?? self
        #endif
    }
}

extension Color {
    /// Màu theo chế độ sáng / tối của hệ thống (hex 0xRRGGBB).
    init(light: UInt32, dark: UInt32) {
        func comp(_ v: UInt32) -> (CGFloat, CGFloat, CGFloat) {
            (CGFloat((v >> 16) & 0xFF) / 255, CGFloat((v >> 8) & 0xFF) / 255, CGFloat(v & 0xFF) / 255)
        }
        let (lr, lg, lb) = comp(light), (dr, dg, db) = comp(dark)
        #if os(macOS)
        self.init(nsColor: NSColor(name: nil) {
            $0.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
                ? NSColor(srgbRed: dr, green: dg, blue: db, alpha: 1) : NSColor(srgbRed: lr, green: lg, blue: lb, alpha: 1)
        })
        #else
        self.init(uiColor: UIColor { $0.userInterfaceStyle == .dark
            ? UIColor(red: dr, green: dg, blue: db, alpha: 1) : UIColor(red: lr, green: lg, blue: lb, alpha: 1) })
        #endif
    }
}

#if os(macOS)
let titlebarInset: CGFloat = 30  // chừa chỗ nút đèn đỏ / vàng / xanh
#else
let titlebarInset: CGFloat = 0   // iPhone: vùng an toàn lo phần trên
#endif

func copyToPasteboard(_ text: String) {
    #if os(macOS)
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
    #else
    UIPasteboard.general.string = text
    #endif
}

/// Lưu một tệp người dùng vừa tải: Mac hỏi nơi lưu; iPhone mở bảng chia sẻ (Lưu vào Tệp, AirDrop, gửi Zalo…). Trả tên tệp hoặc nil nếu huỷ.
@MainActor
func saveFile(_ data: Data, suggestedName: String, type: UTType? = nil) -> String? {
    #if os(macOS)
    let panel = NSSavePanel()
    if let type { panel.allowedContentTypes = [type] }
    panel.nameFieldStringValue = suggestedName
    guard panel.runModal() == .OK, let url = panel.url else { return nil }
    do { try data.write(to: url) } catch { return nil }
    return url.lastPathComponent
    #else
    let url = FileManager.default.temporaryDirectory.appendingPathComponent(suggestedName)
    do { try data.write(to: url) } catch { return nil }
    guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first,
          var top = scene.keyWindow?.rootViewController else { return nil }
    while let next = top.presentedViewController { top = next }
    let share = UIActivityViewController(activityItems: [url], applicationActivities: nil)
    share.popoverPresentationController?.sourceView = top.view
    share.popoverPresentationController?.sourceRect = CGRect(x: top.view.bounds.midX, y: top.view.bounds.midY, width: 1, height: 1)
    top.present(share, animated: true)
    return suggestedName
    #endif
}

let xlsxType = UTType(filenameExtension: "xlsx") ?? .data

/// Ảnh từ dữ liệu tải về (ảnh trong trao đổi — 0115). Nil nếu không đọc được (tệp hỏng, định dạng máy không đọc).
func platformImage(_ data: Data) -> Image? {
    #if os(macOS)
    NSImage(data: data).map { Image(nsImage: $0) }
    #else
    UIImage(data: data).map { Image(uiImage: $0) }
    #endif
}

// MARK: - Chữ theo cỡ người dùng chọn (Dynamic Type)

/// Cỡ chữ của thang 20/16/14/12 (Theme.swift): iPhone nhân theo cỡ chữ người dùng chọn trong Cài đặt (đọc lúc mở app), trần ×1,6 để bố cục
/// còn đứng; Mac giữ nguyên cỡ.
func typeSize(_ base: CGFloat) -> CGFloat {
    #if os(macOS)
    base
    #else
    min(UIFontMetrics.default.scaledValue(for: base), base * 1.6).rounded()
    #endif
}

extension View {
    /// Chữ trong tin chọn / chép được bằng chuột trên Mac. iPhone: nhấn giữ mở menu của tin (có "Chép chữ") — chọn chữ sẽ tranh cử chỉ đó.
    @ViewBuilder func selectableOnMac() -> some View {
        #if os(macOS)
        textSelection(.enabled)
        #else
        self
        #endif
    }

    /// Cỡ tối thiểu của hộp thoại: Mac là cửa sổ nổi cần cỡ; iPhone là tấm trượt theo bề ngang màn hình — đặt cỡ cứng sẽ tràn màn.
    @ViewBuilder func sheetFrame(minWidth: CGFloat? = nil, idealWidth: CGFloat? = nil, minHeight: CGFloat? = nil, idealHeight: CGFloat? = nil) -> some View {
        #if os(macOS)
        frame(minWidth: minWidth, idealWidth: idealWidth, minHeight: minHeight, idealHeight: idealHeight)
        #else
        self
        #endif
    }

    /// Ô "Tệp" của một trao đổi (0128): iPhone phủ cả màn hình; Mac là hộp thoại.
    @ViewBuilder func filesCover<Item: Identifiable, C: View>(item: Binding<Item?>, @ViewBuilder content: @escaping (Item) -> C) -> some View {
        #if os(macOS)
        sheet(item: item) { content($0).frame(minWidth: 560, idealWidth: 640, minHeight: 520, idealHeight: 640) }
        #else
        fullScreenCover(item: item, content: content)
        #endif
    }
}

/// Mac mở tab "Tệp" như mọi trang ở ngăn bên cạnh cuộc chat; iPhone không có ngăn bên ⇒ phủ cả màn hình (``filesCover``).
#if os(macOS)
let threadFilesInCover = false
#else
let threadFilesInCover = true
#endif

/// Hàng nút trong menu nhấn giữ (vd thả cảm xúc): iPhone gom thành một hàng biểu tượng ở đầu menu; Mac để từng dòng như menu chuột phải.
struct MenuStrip<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        #if os(macOS)
        content
        #else
        ControlGroup { content }.controlGroupStyle(.compactMenu)
        #endif
    }
}

// MARK: - Mở trang gọi / xem tệp

/// Trang gọi trực tiếp của chính bản cài (``call_page`` — 0114): Mac mở trình duyệt mặc định; iPhone mở Safari ngay trong app (SFSafariViewController
/// — có micro / camera của Safari, không viết lại WebRTC bằng Swift), đóng là về đúng cuộc trò chuyện. Gọi lúc hộp thoại của lệnh đang đóng ⇒ chờ nó đóng
/// xong mới phủ (UIKit không cho trình bày chồng lên tấm đang rời đi).
@MainActor
func openCallPage(_ url: URL) {
    #if os(macOS)
    NSWorkspace.shared.open(url)
    #else
    guard url.scheme == "https" || url.scheme == "http" else { return }
    func attempt(_ left: Int) {
        guard let top = topController() else { UIApplication.shared.open(url); return }
        if left > 0 && (top.isBeingDismissed || top.isBeingPresented || top.transitionCoordinator != nil) {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { attempt(left - 1) }
            return
        }
        let safari = SFSafariViewController(url: url)
        safari.dismissButtonStyle = .close
        safari.modalPresentationStyle = .fullScreen
        top.present(safari, animated: true)
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { attempt(15) }
    #endif
}

/// Tệp vừa tải trong trao đổi: Mac hỏi nơi lưu, trả tên đã lưu; iPhone xem ngay bằng Xem nhanh (Quick Look — có nút chia sẻ / Lưu vào Tệp), trả nil.
@MainActor
func openDownloadedFile(_ data: Data, name: String) -> String? {
    #if os(macOS)
    return saveFile(data, suggestedName: name)
    #else
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("xem-\(UUID().uuidString)")
    let url = dir.appendingPathComponent(name)
    do {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try data.write(to: url)
    } catch { return nil }
    guard let top = topController() else { return nil }
    let preview = QLPreviewController()
    QuickPreview.shared.url = url
    preview.dataSource = QuickPreview.shared
    top.present(preview, animated: true)
    return nil
    #endif
}

#if os(iOS)
/// Màn đang trên cùng (kể cả tấm đang mở) — nơi trình bày Safari / Xem nhanh.
@MainActor
private func topController() -> UIViewController? {
    guard let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first(where: { $0.activationState == .foregroundActive })
            ?? UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first,
          var top = scene.keyWindow?.rootViewController else { return nil }
    while let next = top.presentedViewController, !next.isBeingDismissed { top = next }
    return top
}

/// Nguồn của Xem nhanh: một tệp tạm (QLPreviewController giữ nguồn yếu ⇒ giữ ở đây).
private final class QuickPreview: NSObject, QLPreviewControllerDataSource {
    static let shared = QuickPreview()
    var url: URL?
    func numberOfPreviewItems(in controller: QLPreviewController) -> Int { url == nil ? 0 : 1 }
    func previewController(_ controller: QLPreviewController, previewItemAt index: Int) -> QLPreviewItem { (url ?? URL(fileURLWithPath: "/")) as NSURL }
}
#endif

// MARK: - Đính kèm vào trao đổi

/// Nút đính kèm của ô nhắn (0115): Mac chọn tệp trên máy; iPhone mở menu Thư viện ảnh / Chụp ảnh / Chọn tệp. Ảnh từ thư viện / máy ảnh đổi sang JPEG
/// (HEIC xem trước được ở mọi máy), cạnh dài ≤ 2560 px. Server vẫn kiểm cỡ + loại theo NỘI DUNG; app chỉ chặn sớm cho người dùng đỡ chờ.
struct AttachButton<Label: View>: View {
    let types: [UTType]
    let onPick: (Data, String) -> Void
    let onError: (String) -> Void
    @ViewBuilder let label: () -> Label
    @State private var importing = false
    #if os(iOS)
    @State private var choosingPhoto = false
    @State private var photo: PhotosPickerItem?
    @State private var camera = false
    #endif

    var body: some View {
        #if os(macOS)
        Button { importing = true } label: { label() }
            .fileImporter(isPresented: $importing, allowedContentTypes: types) { pickFile($0) }
        #else
        Menu {
            Button { choosingPhoto = true } label: { SwiftUI.Label("Thư viện ảnh", systemImage: "photo.on.rectangle") }
            if UIImagePickerController.isSourceTypeAvailable(.camera) {
                Button { camera = true } label: { SwiftUI.Label("Chụp ảnh", systemImage: "camera") }
            }
            Button { importing = true } label: { SwiftUI.Label("Chọn tệp", systemImage: "folder") }
        } label: { label() }
        .fileImporter(isPresented: $importing, allowedContentTypes: types) { pickFile($0) }
        .photosPicker(isPresented: $choosingPhoto, selection: $photo, matching: .images)
        .onChange(of: photo) {
            guard let item = photo else { return }
            photo = nil
            Task {
                guard let data = try? await item.loadTransferable(type: Data.self) else { onError("Không đọc được ảnh này"); return }
                // ảnh động GIF giữ nguyên; còn lại (HEIC, PNG lớn…) đổi JPEG cho nhẹ + xem được ở mọi máy
                if item.supportedContentTypes.contains(where: { $0.conforms(to: .gif) }) { onPick(data, Self.stamp("gif")); return }
                guard let jpeg = UIImage(data: data).flatMap(Self.jpeg) else { onError("Không đọc được ảnh này"); return }
                onPick(jpeg, Self.stamp("jpg"))
            }
        }
        .fullScreenCover(isPresented: $camera) {
            CameraPicker { image in
                camera = false
                if let image, let jpeg = Self.jpeg(image) { onPick(jpeg, Self.stamp("jpg")) }
            }
            .ignoresSafeArea()
        }
        #endif
    }

    private func pickFile(_ result: Result<URL, Error>) {
        guard case .success(let url) = result else { return }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do { onPick(try Data(contentsOf: url), url.lastPathComponent) } catch { onError(error.localizedDescription) }
    }

    #if os(iOS)
    private static func stamp(_ ext: String) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        return "anh-\(f.string(from: Date())).\(ext)"
    }

    private static func jpeg(_ image: UIImage) -> Data? {
        let maxSide: CGFloat = 2560
        let w = image.size.width * image.scale, h = image.size.height * image.scale
        let k = min(1, maxSide / max(w, h, 1))
        let size = CGSize(width: (w * k).rounded(), height: (h * k).rounded())
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let drawn = UIGraphicsImageRenderer(size: size, format: format).image { ctx in
            UIColor.white.setFill()
            ctx.fill(CGRect(origin: .zero, size: size))
            image.draw(in: CGRect(origin: .zero, size: size))   // vẽ lại ⇒ xoay đúng chiều chụp
        }
        return drawn.jpegData(compressionQuality: 0.85)
    }
    #endif
}

#if os(iOS)
/// Máy ảnh hệ thống (UIImagePickerController) — chụp xong trả ảnh, huỷ trả nil.
private struct CameraPicker: UIViewControllerRepresentable {
    let done: (UIImage?) -> Void

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }
    func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(done: done) }

    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let done: (UIImage?) -> Void
        init(done: @escaping (UIImage?) -> Void) { self.done = done }
        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            done(info[.originalImage] as? UIImage)
        }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { done(nil) }
    }
}
#endif
