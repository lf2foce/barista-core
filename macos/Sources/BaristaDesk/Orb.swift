import SwiftUI

/// "Sự hiện diện" của trợ lý — chép quả cầu lưới màu của barista_bds, bảng màu xanh / tím.
struct Orb: View {
    enum Mood { case idle, thinking }
    var mood: Mood
    var size: CGFloat
    var level: Float = 0   // mức tiếng (giọng nói): quả cầu phồng theo
    var glow = false       // quầng sáng mờ quanh quả cầu — CHỈ khối giọng nói đang nghe / nói (báo trợ lý đang hoạt động); chỗ khác trơn

    var body: some View {
        TimelineView(.animation(minimumInterval: mood == .idle ? 1.0 / 12 : 1.0 / 24)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate * (mood == .idle ? 0.35 : 1.5)
            MeshGradient(width: 3, height: 3, points: Self.points(t), colors: colors)
                .clipShape(Circle())
                .overlay(Circle().fill(RadialGradient(colors: [.white.opacity(0.45), .clear], center: UnitPoint(x: 0.32, y: 0.28),
                                                      startRadius: 0, endRadius: size * 0.55)))
                .overlay(Circle().strokeBorder(.white.opacity(0.22), lineWidth: max(0.5, size / 80)))
                .scaleEffect(breathe(t))
        }
        .shadow(color: glow ? (mood == .idle ? Color.blue : Color.purple).opacity(mood == .idle ? 0.30 : 0.6) : .clear, radius: glow ? size * 0.2 : 0)
        .frame(width: size, height: size)
        .animation(.easeInOut(duration: 0.6), value: mood)
    }

    /// Nhịp "thở" của quả cầu + phồng theo mức tiếng.
    private func breathe(_ t: Double) -> Double {
        let base: Double = mood == .idle ? 0.012 * sin(t * 2) : 0.035 * sin(t * 2.2)
        return 1 + base + Double(level) * 0.12
    }

    private var colors: [Color] {
        let ice = Color(red: 0.86, green: 0.93, blue: 1.0)
        let blue = Color(red: 0.16, green: 0.48, blue: 0.98)
        let deep = Color(red: 0.12, green: 0.24, blue: 0.62)
        switch mood {
        case .idle: return [deep, blue, ice, blue, ice, blue, ice, deep, blue]
        case .thinking:
            let violet = Color(red: 0.50, green: 0.36, blue: 0.95)
            let rose = Color(red: 0.95, green: 0.45, blue: 0.62)
            return [violet, blue, rose, blue, ice, violet, rose, violet, blue]
        }
    }

    nonisolated static func points(_ t: Double) -> [SIMD2<Float>] {
        func w(_ a: Double, _ amp: Double = 0.2) -> Float { Float(0.5 + sin(a) * amp) }
        return [
            [0, 0], [w(t * 0.9), 0], [1, 0],
            [0, w(t * 1.1)], [w(t * 1.3 + 1, 0.25), w(t * 0.7 + 2, 0.25)], [1, w(t * 0.8 + 3)],
            [0, 1], [w(t * 1.2 + 4), 1], [1, 1],
        ]
    }
}

/// Hoạ tiết nền mờ của cột chat (như Barista Desk) — biểu tượng nghề môi giới.
struct Doodles: View {
    private let symbols = ["house", "building.2", "key", "doc.text", "chart.bar", "banknote", "person.2", "calendar", "phone",
                           "map", "megaphone", "sparkles"]

    var body: some View {
        Canvas { ctx, size in
            let step: CGFloat = 64
            var i = 0
            for y in stride(from: CGFloat(10), to: size.height, by: step) {
                for x in stride(from: (Int(y / step) % 2 == 0 ? 10 : 42), to: Int(size.width), by: Int(step)) {
                    let img = ctx.resolve(Image(systemName: symbols[(i * 7 + Int(y)) % symbols.count]))
                    var c = ctx
                    c.translateBy(x: CGFloat(x), y: y)
                    c.rotate(by: .degrees(Double((i * 37) % 50) - 25))
                    c.opacity = 0.035
                    c.draw(img, at: .zero)
                    i += 1
                }
            }
        }
        .foregroundStyle(WA.text)
        .allowsHitTesting(false)
    }
}

/// Nút công cụ phẳng của Barista Desk: nền trắng, viền xám nhẹ, bo viên thuốc; rê chuột đậm lên một nhịp.
struct ToolChipStyle: ButtonStyle {
    var fill: Color?

    func makeBody(configuration: Configuration) -> some View { Chip(configuration: configuration, fill: fill) }

    private struct Chip: View {
        let configuration: ButtonStyleConfiguration
        let fill: Color?
        @State private var hover = false

        var body: some View {
            configuration.label
                .foregroundStyle(fill == nil ? Color.primary : Color.white)
                .padding(.horizontal, 11)
                .frame(minWidth: 34, minHeight: 32)
                .background(Capsule().fill(fill ?? (hover ? Color.surface2 : Color.surface)))
                .overlay(Capsule().strokeBorder(Color.primary.opacity(fill == nil ? 0.12 : 0), lineWidth: 1))
                .opacity(configuration.isPressed ? 0.7 : 1)
                .contentShape(Capsule())
                .onHover { hover = $0 }
        }
    }
}

struct ToolLabel: View {
    let text: String
    let icon: String
    var tint: Color
    var labels = true

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 14, weight: .semibold)).foregroundStyle(tint)
            if labels { Text(text).font(.t12m).lineLimit(1) }
        }
    }
}
