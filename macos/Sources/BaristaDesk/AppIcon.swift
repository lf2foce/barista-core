import SwiftUI
#if os(macOS)
import AppKit
#endif

/// Icon app Barista: nền xanh đậm (màu hệ thống, gradient đậm — không nhạt), quả cầu trợ lý ở giữa, toà nhà trắng (môi giới BĐS).
/// Vẽ bằng mã ⇒ Mac đặt icon Dock lúc chạy (`swift run` không có .app), iPhone xuất PNG 1024 vào asset catalog (`--render-icon`).
struct AppIconArt: View {
    /// Mac: khung squircle 824/1024 có lề (Dock không tự bo); iPhone: tràn viền (hệ thống tự bo góc).
    var macFrame = true

    var body: some View {
        GeometryReader { g in
            let side = min(g.size.width, g.size.height)
            let tile = macFrame ? side * 824 / 1024 : side
            ZStack {
                RoundedRectangle(cornerRadius: macFrame ? tile * 0.225 : 0, style: .continuous)
                    .fill(LinearGradient(colors: [Color(red: 0.13, green: 0.36, blue: 0.98), Color(red: 0.07, green: 0.14, blue: 0.52)],
                                         startPoint: .topLeading, endPoint: .bottomTrailing))
                    .overlay(RoundedRectangle(cornerRadius: macFrame ? tile * 0.225 : 0, style: .continuous)
                        .fill(RadialGradient(colors: [.white.opacity(0.22), .clear], center: UnitPoint(x: 0.3, y: 0.18),
                                             startRadius: 0, endRadius: tile * 0.7)))
                    .frame(width: tile, height: tile)
                    .shadow(color: .black.opacity(macFrame ? 0.28 : 0), radius: side * 0.02, y: side * 0.012)
                // quả cầu trợ lý (đứng yên — icon không chạy hoạt cảnh)
                MeshGradient(width: 3, height: 3, points: Orb.points(0.6), colors: [
                    Color(red: 0.12, green: 0.24, blue: 0.62), Color(red: 0.16, green: 0.48, blue: 0.98), Color(red: 0.86, green: 0.93, blue: 1.0),
                    Color(red: 0.16, green: 0.48, blue: 0.98), Color(red: 0.86, green: 0.93, blue: 1.0), Color(red: 0.50, green: 0.36, blue: 0.95),
                    Color(red: 0.86, green: 0.93, blue: 1.0), Color(red: 0.12, green: 0.24, blue: 0.62), Color(red: 0.16, green: 0.48, blue: 0.98)])
                    .clipShape(Circle())
                    .overlay(Circle().fill(RadialGradient(colors: [.white.opacity(0.55), .clear], center: UnitPoint(x: 0.32, y: 0.26),
                                                          startRadius: 0, endRadius: tile * 0.3)))
                    .overlay(Circle().strokeBorder(.white.opacity(0.35), lineWidth: tile * 0.006))
                    .frame(width: tile * 0.62, height: tile * 0.62)
                Image(systemName: "building.2.fill")
                    .font(.system(size: tile * 0.25, weight: .semibold))
                    .foregroundStyle(.white)
                    .shadow(color: Color(red: 0.05, green: 0.1, blue: 0.4).opacity(0.45), radius: tile * 0.015, y: tile * 0.01)
            }
            .frame(width: g.size.width, height: g.size.height)
        }
    }

    #if os(macOS)
    /// Ảnh icon cho Dock / cửa sổ (ImageRenderer — dựng một lần lúc mở app).
    @MainActor static func image(side: CGFloat = 512) -> NSImage? {
        let r = ImageRenderer(content: AppIconArt().frame(width: side, height: side))
        r.scale = 2
        return r.nsImage
    }

    /// `--render-icon <thư mục>`: xuất PNG 1024 cho Mac (có khung) và iPhone (tràn viền).
    @MainActor static func render(into dir: URL) {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        for (name, mac) in [("icon-mac-1024.png", true), ("icon-ios-1024.png", false)] {
            let r = ImageRenderer(content: AppIconArt(macFrame: mac).frame(width: 1024, height: 1024))
            r.scale = 1
            r.isOpaque = !mac
            guard let cg = r.cgImage else { continue }
            let rep = NSBitmapImageRep(cgImage: cg)
            try? rep.representation(using: .png, properties: [:])?.write(to: dir.appending(path: name))
        }
        print("ok:", dir.path)
    }
    #endif
}
