import SwiftUI

// Phần dùng chung của danh sách "Tin nhắn" (0115): cột giữa trên Mac (Rail.swift) và tab "Tin nhắn" trên iPhone (ios/Sources/PhoneShell.swift).

/// Một dòng như danh sách chat của Lark: ảnh | tên + nhãn nhỏ + giờ | dòng tin cuối + số chưa đọc.
struct InboxRow<Icon: View>: View {
    let title: String
    var tag: (String, Color)? = nil
    let preview: String
    let stamp: String
    var unread = 0
    /// Tắt tiếng: số chưa đọc vẽ xám, không tính lên menu (server đếm).
    var muted = false
    var mentioned = false
    var selected = false
    @ViewBuilder let icon: Icon
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                icon.frame(width: 40, height: 40)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(title).font(unread > 0 ? .t14m : .t14).lineLimit(1)
                        if let tag, !tag.0.isEmpty {
                            Text(tag.0).font(.t12).foregroundStyle(tag.1)
                                .padding(.horizontal, 5).padding(.vertical, 1)
                                .background(RoundedRectangle(cornerRadius: 4, style: .continuous).fill(tag.1.opacity(0.13)))
                                .fixedSize()
                        }
                        if muted { Image(systemName: "bell.slash.fill").font(.system(size: 10)).foregroundStyle(.tertiary).help("Đã tắt thông báo") }
                        Spacer(minLength: 4)
                        Text(stamp).font(.t12).foregroundStyle(.tertiary).fixedSize()
                    }
                    HStack(spacing: 6) {
                        Text(preview.isEmpty ? " " : preview).font(.t12).foregroundStyle(.secondary).lineLimit(1)
                        Spacer(minLength: 4)
                        if mentioned {
                            Text("@").font(.t12m).foregroundStyle(.white).frame(width: 18, height: 18).background(Circle().fill(Color.bad))
                        }
                        if unread > 0 {
                            Text(unread > 99 ? "99+" : "\(unread)").font(.system(size: 11, weight: .bold)).monospacedDigit()
                                .foregroundStyle(.white).padding(.horizontal, 5).frame(minWidth: 18, minHeight: 18)
                                .background(Capsule().fill(mentioned ? Color.bad : muted ? Color.secondary.opacity(0.6) : Color.accent))
                        }
                    }
                }
            }
            .padding(.horizontal, 10).padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(selected ? Color.accent.opacity(0.12) : (hover ? Color.primary.opacity(0.05) : .clear)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

/// Ảnh nhóm: chữ cái đầu của tên phòng trên nền màu cố định theo tên (như nhóm chat của Lark).
struct RoomAvatar: View {
    let name: String
    var size: CGFloat = 40

    private static let palette: [Color] = [.blue, .teal, .indigo, .orange, .pink, .purple, .green]

    var body: some View {
        let words = name.split { !$0.isLetter }.prefix(2)
        // chữ cái đầu bỏ dấu ("Dự án" → "DA"): không in hoa chữ có dấu
        let text = String(words.compactMap(\.first)).folding(options: .diacriticInsensitive, locale: Locale(identifier: "vi_VN")).uppercased()
        let tint = Self.palette[name.unicodeScalars.reduce(0) { ($0 &+ Int($1.value)) % 997 } % Self.palette.count]
        Text(text.isEmpty ? "#" : text)
            .font(.system(size: size * 0.36, weight: .semibold)).foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(Circle().fill(LinearGradient(colors: [tint, tint.opacity(0.7)], startPoint: .topLeading, endPoint: .bottomTrailing)))
    }
}
