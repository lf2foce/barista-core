import SwiftUI

// KHỐI ẢNH + CHIA HAI CỘT (api 1.73 — ``ui.b_image`` / ``ui.b_split``): mảnh ghép bố cục chung, gói tự ghép kiểu nhìn riêng ("ảnh phiếu bên trái,
// dòng máy đọc bên phải") mà app không cần khối riêng cho từng nghiệp vụ. Cùng luật giải mã với web (web/src/lib/decode.ts).

/// Ảnh trong thẻ. ``src`` là link ký NGẮN HẠN của kho tệp (http/https, ≤ 60 giây) hoặc đường ``/v1/…`` của core; dạng khác ⇒ coi như chưa có ảnh
/// (hiện ``note``), không tải link lạ.
struct CardImage: Decodable, Hashable {
    let src: String
    let caption: String
    let alt: String
    let note: String

    private enum K: String, CodingKey { case src, caption, alt, note }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        let raw = (try? c.decodeIfPresent(String.self, forKey: .src)) ?? ""
        src = Self.safeSource(raw) ? raw : ""
        caption = (try? c.decodeIfPresent(String.self, forKey: .caption)) ?? ""
        alt = (try? c.decodeIfPresent(String.self, forKey: .alt)) ?? ""
        note = (try? c.decodeIfPresent(String.self, forKey: .note)) ?? ""
    }

    /// Link ký của kho tệp (http/https) hoặc đường của chính core (/v1/…). Đường core tải KÈM danh tính; link ngoài tải KHÔNG kèm (không lộ phiên).
    static func safeSource(_ s: String) -> Bool {
        s.range(of: #"^https?://[^\s"'<>]+$"#, options: .regularExpression) != nil
            || s.range(of: #"^/v1(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)+(\?[^\s"'<>]*)?$"#, options: .regularExpression) != nil
    }

    static func validRatio(_ r: String) -> Bool { r.range(of: #"^[1-9]:[1-9]$"#, options: .regularExpression) != nil }

    var isCorePath: Bool { src.hasPrefix("/v1/") }
}

/// Vẽ ``CardImage``: tải NGAY khi thẻ hiện (link ký có thể hết hạn nếu chờ cuộn tới), giữ ảnh đã tải để xem to không phải tải lại.
/// Hỏng ⇒ báo + gợi ý tải lại thẻ. Bấm ⇒ xem to (Esc / nút Đóng).
struct CardImageView: View {
    let block: CardImage
    @Environment(Desk.self) private var desk: Desk?
    @State private var image: Image?
    @State private var failed = false
    @State private var zoom = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if block.src.isEmpty || failed {
                Text(failed ? "Link ảnh đã hết hạn hoặc không tải được — tải lại thẻ" : (block.note.isEmpty ? "Chưa có ảnh" : block.note))
                    .font(.t14).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity, minHeight: 128).padding(16)
                    .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface2))
            } else if let image {
                Button { zoom = true } label: {
                    image.resizable().scaledToFit()
                        .frame(maxWidth: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.primary.opacity(0.08)))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Xem to: \(label)")
                .help("Bấm để xem to")
            } else {
                RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface2)
                    .frame(maxWidth: .infinity, minHeight: 180)
                    .overlay(ProgressView().controlSize(.small))
            }
            if !block.caption.isEmpty { Text(block.caption).font(.t12).foregroundStyle(.secondary) }
        }
        .task(id: block.src) { await load() }
        .sheet(isPresented: $zoom) { if let image { ImageZoom(image: image, label: label) } }
    }

    private var label: String { block.alt.isEmpty ? (block.caption.isEmpty ? "Ảnh" : block.caption) : block.alt }

    private func load() async {
        image = nil; failed = false
        guard !block.src.isEmpty else { return }
        do {
            let data: Data
            if block.isCorePath {
                guard let desk else { failed = true; return }
                data = try await desk.api.download(block.src)
            } else {
                guard let url = URL(string: block.src) else { failed = true; return }
                let (d, r) = try await URLSession.shared.data(from: url)   // link ký ngoài: không gắn mã phiên
                guard (r as? HTTPURLResponse)?.statusCode == 200 else { failed = true; return }
                data = d
            }
            if let img = platformImage(data) { image = img } else { failed = true }
        } catch {
            failed = true
        }
    }
}

/// Xem ảnh to: nền tối, ảnh vừa màn, nút Đóng (Esc trên Mac).
private struct ImageZoom: View {
    let image: Image
    let label: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Color.black.opacity(0.92).ignoresSafeArea()
            image.resizable().scaledToFit().padding(16)
                .accessibilityLabel(label)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            Button { dismiss() } label: {
                Image(systemName: "xmark").font(.system(size: 16, weight: .semibold))
                    .frame(width: 40, height: 40)
                    .background(Circle().fill(Color.white.opacity(0.9)))
                    .foregroundStyle(Color.black)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.cancelAction)
            .accessibilityLabel("Đóng ảnh")
            .padding(16)
        }
        #if os(macOS)
        .frame(minWidth: 720, minHeight: 560)
        #endif
    }
}

/// Bề rộng khung từ đó khối vẽ kiểu "màn rộng" (chia hai cột cạnh nhau, lịch lưới giờ); hẹp hơn ⇒ xếp dọc / danh sách. Theo khung chứa thẻ, không theo loại máy.
enum BlockLayout { static let wide: CGFloat = 560 }

/// Chia hai cột theo ``ratio`` (trái:phải). Khung đủ rộng (≥ 560 điểm) ⇒ hai cột cạnh nhau; hẹp (iPhone dọc, cột hẹp) ⇒ xếp dọc, cột trái trước.
/// Dựa trên bề rộng THẬT của khung chứa thẻ, không theo loại máy — cùng thẻ trên Mac, iPad, iPhone Duo mở / gập đều đúng.
struct SplitBlockView<L: View, R: View>: View {
    let ratio: String
    @ViewBuilder let left: () -> L
    @ViewBuilder let right: () -> R
    @State private var width: CGFloat = 0

    private let gap: CGFloat = 16

    var body: some View {
        let parts = ratio.split(separator: ":").compactMap { Double($0) }
        let a = parts.first ?? 1, b = parts.count > 1 ? parts[1] : 1
        Group {
            if width >= BlockLayout.wide {
                HStack(alignment: .top, spacing: gap) {
                    VStack(alignment: .leading, spacing: 12) { left() }
                        .frame(width: max(0, (width - gap) * a / (a + b)), alignment: .topLeading)
                    VStack(alignment: .leading, spacing: 12) { right() }
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                }
            } else {
                VStack(alignment: .leading, spacing: 12) {
                    left()
                    right()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
    }
}
