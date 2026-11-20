import SwiftUI

// Ngôn ngữ thiết kế chép từ Barista Desk (barista_bds) — giữ cùng cảm giác giữa hai sản phẩm:
// màu hệ thống Apple sáng / tối, nền nhóm kiểu Cài đặt, thẻ trắng viền mảnh không đổ bóng, chat kiểu tin nhắn.

// Thang chữ tiếng Việt: 3 độ đậm, bốn bậc 20/16/14/12, không in hoa có dấu, không nghiêng. iPhone nhân theo cỡ chữ người dùng chọn (``typeSize``).
extension Font {
    static let t20 = Font.system(size: typeSize(20), weight: .semibold)
    static let t16 = Font.system(size: typeSize(16), weight: .semibold)
    static let t14 = Font.system(size: typeSize(14))
    static let t14m = Font.system(size: typeSize(14), weight: .medium)
    static let t12 = Font.system(size: typeSize(12))
    static let t12m = Font.system(size: typeSize(12), weight: .medium)
}

extension Color {
    static let ok = Color.green
    static let warn = Color.orange
    static let bad = Color.red
    static let cold = Color.blue
    static let accent = Color.blue
    // Nền nhóm kiểu Cài đặt hệ thống: sáng #F2F2F7 / thẻ trắng — tối đen / thẻ #1C1C1E / ô #2C2C2E
    static let canvas = Color(light: 0xF2F2F7, dark: 0x000000)
    static let surface = Color(light: 0xFFFFFF, dark: 0x1C1C1E)
    static let surface2 = Color(light: 0xF2F2F7, dark: 0x2C2C2E)
    static let hairline = Color.primary.opacity(0.08)

    /// Tông do server gửi (badge, KPI) → màu hệ thống.
    static func tone(_ name: String?) -> Color {
        switch name {
        case "green": .ok
        case "orange": .warn
        case "red": .bad
        case "blue": .cold
        case "purple": .purple
        case "gray": .secondary
        default: .primary
        }
    }
}

/// Bảng màu khung chat (kiểu ứng dụng tin nhắn) — như Barista Desk.
enum WA {
    static let wall = Color(light: 0xFFFFFF, dark: 0x000000)
    static let bar = Color(light: 0xF6F6F6, dark: 0x1C1C1E)
    static let outgoing = Color.blue
    static let incoming = Color(light: 0xE9E9EB, dark: 0x262629)
    static let text = Color.primary
    static let meta = Color.secondary
    static let field = Color(light: 0xFFFFFF, dark: 0x2C2C2E)
    static let chip = Color(light: 0xF2F2F7, dark: 0x1C1C1E)
}


/// Định dạng số / ngày kiểu Việt Nam: 4.500.000.000 · 4,5 tỷ · 30/10/2026 · 2,3%.
enum Fmt {
    private static let grouping: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.locale = Locale(identifier: "vi_VN")
        f.maximumFractionDigits = 2
        return f
    }()

    static func number(_ v: Double) -> String { grouping.string(from: NSNumber(value: v)) ?? "\(v)" }

    static func compactMoney(_ v: Double) -> String {
        let a = abs(v)
        if a >= 1e9 { return number((v / 1e9 * 100).rounded() / 100) + " tỷ" }
        if a >= 1e6 { return number((v / 1e6).rounded()) + " tr" }
        return number(v) + " đ"
    }

    private static let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f
    }()
    private static let shortDateTime: DateFormatter = {
        let f = DateFormatter()
        f.timeZone = TimeZone(identifier: "Asia/Ho_Chi_Minh")
        f.dateFormat = "dd/MM/yyyy HH:mm"
        return f
    }()
    private static let clock: DateFormatter = {
        let f = DateFormatter()
        f.timeZone = TimeZone(identifier: "Asia/Ho_Chi_Minh")
        f.dateFormat = "HH:mm"
        return f
    }()

    /// ISO (UTC "Z" / "+00:00", có / không phần lẻ giây) → Date.
    static func parse(_ s: String) -> Date? {
        guard s.count >= 19, let base = isoDay.date(from: String(s.prefix(19))) else { return nil }
        var offset = 0
        let tail = s.dropFirst(19).drop { $0 == "." || $0.isNumber }
        if let sign = tail.first, sign == "+" || sign == "-" {
            let parts = tail.dropFirst().split(separator: ":").compactMap { Int($0) }
            if parts.count >= 1 { offset = (parts[0] * 3600 + (parts.count > 1 ? parts[1] * 60 : 0)) * (sign == "-" ? -1 : 1) }
        }
        return base.addingTimeInterval(TimeInterval(-offset))
    }

    static func datetime(_ s: String) -> String { parse(s).map { shortDateTime.string(from: $0) } ?? s }

    private static let dayMonth: DateFormatter = {
        let f = DateFormatter()
        f.timeZone = TimeZone(identifier: "Asia/Ho_Chi_Minh")
        f.dateFormat = "dd/MM"
        return f
    }()

    /// Mốc giờ ở danh sách tin nhắn: hôm nay "14:05", hôm qua "Hôm qua", cũ hơn "28/10" (giờ Việt Nam).
    static func stamp(_ s: String) -> String {
        guard let d = parse(s) else { return "" }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "Asia/Ho_Chi_Minh") ?? .current
        if cal.isDateInToday(d) { return clock.string(from: d) }
        if cal.isDateInYesterday(d) { return "Hôm qua" }
        return dayMonth.string(from: d)
    }
    static func time(_ d: Date) -> String { clock.string(from: d) }

    static func date(_ s: String) -> String {
        let p = s.prefix(10).split(separator: "-")
        return p.count == 3 ? "\(p[2])/\(p[1])/\(p[0])" : s
    }

    /// SĐT lưu E.164 (ADR 0199, bổ sung 15/11/2026): số Việt Nam `+84…` hiện số nội địa liền (`0898654936`, không dấu cách); số nước khác giữ `+<mã nước><số>`.
    /// Cùng luật `phone_display` (backend) và `format.ts::phone` (web); chuỗi không phải E.164 (số đã che…) trả nguyên.
    static func phone(_ s: String) -> String {
        guard s.hasPrefix("+84"), s.dropFirst().allSatisfy({ $0.isNumber }) else { return s }
        return "0" + s.dropFirst(3)
    }

    /// Một ô theo `format` server gửi.
    static func cell(_ v: JSON?, _ format: String, labels: [String: String]? = nil) -> String {
        guard let v, !v.isNull else { return "" }
        switch format {
        case "money", "number": return v.double.map(number) ?? v.text
        case "percent": return v.double.map { number($0) + "%" } ?? v.text
        case "date": return date(v.text)
        case "datetime": return datetime(v.text)
        case "phone": return phone(v.text)
        case "badge": return labels?[v.text] ?? v.text
        default:
            if case .bool(let b) = v { return b ? "Có" : "Không" }
            return v.text
        }
    }

    static func kpi(_ k: KPI) -> String {
        guard !k.value.isNull else { return "—" }
        switch k.format {
        case "money": return k.value.double.map(compactMoney) ?? k.value.text
        default: return cell(k.value, k.format)
        }
    }
}

struct Pill: View {
    let text: String
    var tone: Color = .secondary
    var filled = false

    var body: some View {
        Text(text)
            .font(.t12m)
            .lineLimit(1)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .foregroundStyle(filled ? Color.white : tone)
            .background(Capsule().fill(filled ? tone : tone.opacity(0.13)))
    }
}

/// Nhãn trạng thái server gửi (badge) — vẽ bằng Pill.
struct Badge: View {
    let text: String
    let tone: String?
    var body: some View {
        if !text.isEmpty { Pill(text: text, tone: Color.tone(tone)) }
    }
}

struct Initials: View {
    let name: String
    var size: CGFloat = 28
    var colors: [Color] = [Color(red: 0.2, green: 0.55, blue: 1.0), Color(red: 0.36, green: 0.3, blue: 0.95)]

    var body: some View {
        let text = String(name.split(separator: " ").last?.prefix(1) ?? "?")
        Circle()
            .fill(LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing))
            .frame(width: size, height: size)
            .overlay(Text(text).font(.system(size: size * 0.42, weight: .semibold)).foregroundStyle(.white))
    }
}

/// Ô biểu tượng tô màu (đầu thẻ, dòng xem trước, nút tiện ích đầu cột) — như Barista Desk: gradient ĐẬM, không nhạt.
struct IconTile: View {
    let symbol: String
    let tint: Color
    var size: CGFloat = 30

    var body: some View {
        Image(systemName: SymbolMap.name(symbol))
            .font(.system(size: size * 0.46, weight: .semibold))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(RoundedRectangle(cornerRadius: size * 0.32, style: .continuous)
                .fill(LinearGradient(colors: [tint, tint.opacity(0.78)], startPoint: .topLeading, endPoint: .bottomTrailing)))
    }
}

extension Color {
    /// Tên màu hệ thống server gửi (``Screen.tint``) ⇒ màu SwiftUI; tên lạ ⇒ nil (dùng màu phân hệ).
    static func named(_ name: String?) -> Color? {
        switch name {
        case "blue": .blue
        case "teal": .teal
        case "green": .green
        case "orange": .orange
        case "red": .red
        case "pink": .pink
        case "purple": .purple
        case "indigo": .indigo
        case "mint": .mint
        case "brown": .brown
        case "gray": .gray
        case "yellow": .yellow
        case "cyan": .cyan
        default: nil
        }
    }
}

extension ScreenSpec {
    /// Màu của màn: server khai theo ý nghĩa; chưa khai ⇒ màu phân hệ của truy vấn.
    var tintColor: Color { Color.named(tint) ?? ModuleStyle.of(query).tint }
}

/// Biểu tượng + màu theo phân hệ của một thao tác (vd "crm.leads" → khách).
enum ModuleStyle {
    static func of(_ op: String) -> (symbol: String, tint: Color) {
        switch op.split(separator: ".").first.map(String.init) ?? "" {
        case "crm": ("person.2.fill", .blue)
        case "catalog": ("building.2.fill", .teal)
        case "deals": ("doc.text.fill", .indigo)
        case "commission": ("wallet.pass.fill", .green)
        case "acct": ("banknote.fill", .orange)
        case "mkt": ("megaphone.fill", .pink)
        case "admin": ("gearshape.2.fill", .gray)
        case "core": ("checkmark.seal.fill", .purple)
        case "kb": ("books.vertical.fill", .brown)
        case "papers": ("doc.richtext.fill", .indigo)
        case "talk": ("bubble.left.and.bubble.right.fill", .cyan)
        case "flow": ("checklist", .mint)
        case "leadflow": ("arrow.triangle.branch", .blue)
        case "work": ("clock.arrow.circlepath", .orange)
        case "hosting": ("house.fill", .teal)
        case "training": ("graduationcap.fill", .purple)
        case "books": ("book.closed.fill", .green)
        case "accounting_service": ("briefcase.fill", .indigo)
        default: ("square.grid.2x2.fill", .blue)
        }
    }
}

struct Toast: Equatable {
    let text: String
    let isError: Bool
}
