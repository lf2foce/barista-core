import SwiftUI

/// BÀN VIỆC (0094, desk-column.tsx): MỘT điểm vào "tôi phải làm gì" — cần tôi quyết · cần tôi làm · đang theo dõi. Mỗi mục là một màn đúng quyền
/// (bấm ⇒ mở trên khung làm việc, đủ nút như cũ), câu tín hiệu do server tính từ chính truy vấn của màn. App không tự đếm, không tự lọc quyền.
struct DeskColumn: View {
    @Environment(Desk.self) private var desk
    @State private var loading = false

    /// Thẻ đề xuất sáng thuộc "Cần tôi quyết" — có thẻ mà chưa có nhóm đó ⇒ vẫn dựng nhóm.
    private var groups: [DeskPage.Group] {
        let gs = desk.deskPage?.groups ?? []
        if !desk.morning.isEmpty && !gs.contains(where: { $0.key == "decide" }) {
            return [DeskPage.Group(key: "decide", title: "Cần tôi quyết", sections: [])] + gs
        }
        return gs
    }

    var body: some View {
        let waiting = desk.deskPage?.count ?? 0
        let unknown = desk.deskPage?.unchecked ?? 0
        VStack(spacing: 0) {
            DeskHeader(subtitle: [waiting > 0 ? "\(waiting) mục cần để ý" : (unknown > 0 ? "" : "Mọi việc của bạn ở một chỗ"),
                                  unknown > 0 ? "\(unknown) mục chưa kiểm được" : "", "đúng quyền của bạn"].filter { !$0.isEmpty }.joined(separator: " · ")) {
                Button { Task { loading = true; await desk.refreshDesk(); loading = false } } label: { Image(systemName: "arrow.clockwise") }
                    .buttonStyle(ToolChipStyle()).disabled(loading).help("Tải lại")
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2) {
                    if desk.deskFailed { Text("Chưa tải được bàn việc — bấm tải lại.").font(.t14).foregroundStyle(.secondary).padding(12) }
                    ForEach(groups) { g in
                        let hot = g.sections.filter { !$0.lines.isEmpty || $0.unchecked }.count + (g.key == "decide" ? desk.morning.count : 0)
                        HStack(spacing: 6) {
                            Text(g.title).font(.t12m).foregroundStyle(.secondary)
                            if hot > 0 {   // số MỤC có tín hiệu trong nhóm (server đã quyết) — không tự đếm việc
                                Text("\(hot)").font(.t12m).monospacedDigit().padding(.horizontal, 6).frame(minWidth: 18, minHeight: 18)
                                    .background(Capsule().fill(Color.primary.opacity(0.08)))
                            }
                        }
                        .padding(.horizontal, 12).padding(.top, 12).padding(.bottom, 4)
                        if g.key == "decide" { ForEach(desk.morning) { c in MorningRow(card: c).padding(.horizontal, 4) } }
                        ForEach(g.sections.filter { !$0.lines.isEmpty || $0.unchecked }) { s in DeskRow(section: s) }
                        let quiet = g.sections.filter { $0.lines.isEmpty && !$0.unchecked }
                        if !quiet.isEmpty { QuietRow(sections: quiet) }
                    }
                }
                .padding(.horizontal, 6).padding(.vertical, 8)
            }
        }
        .background(WA.wall)
        .task { if desk.deskPage == nil { await desk.refreshDesk() } }
    }
}

/// Mục không có gì mới: một dòng mờ "✓ N mục khác ổn" — bấm mới bung lối vào từng màn; không chiếm chỗ của việc đang cần làm.
private struct QuietRow: View {
    @Environment(Desk.self) private var desk
    let sections: [DeskPage.Section]
    @State private var open = false

    var body: some View {
        FlowRow(spacing: 6) {
            Button { withAnimation(.snappy) { open.toggle() } } label: {
                HStack(spacing: 4) {
                    Image(systemName: "checkmark").font(.system(size: 11, weight: .semibold)).foregroundStyle(Color.ok)
                    Text("\(sections.count) mục khác ổn").font(.t12).foregroundStyle(.secondary)
                    Image(systemName: "chevron.right").font(.system(size: 9, weight: .semibold)).foregroundStyle(.tertiary)
                        .rotationEffect(.degrees(open ? 90 : 0))
                }
            }
            .buttonStyle(.plain)
            if open {
                ForEach(sections) { s in
                    Button(s.screen.title) { withAnimation(.snappy) { desk.open(.screen(s.screen), show: true) } }
                        .buttonStyle(.bordered).controlSize(.small)
                }
            }
        }
        .padding(.horizontal, 10).padding(.top, 4)
    }
}

private struct DeskRow: View {
    @Environment(Desk.self) private var desk
    let section: DeskPage.Section
    @State private var hover = false
    @State private var expanded = false   // bấm mới bung — không biến bàn việc thành "40 thẻ"

    var body: some View {
        let s = section.screen
        let selected = desk.activeId == CanvasItem.screen(s).id
        HStack(alignment: .top, spacing: 10) {
            Button { withAnimation(.snappy) { desk.open(.screen(s), show: true) } } label: {
                HStack(alignment: .top, spacing: 10) {
                    IconTile(symbol: s.icon, tint: s.tintColor, size: 32)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(s.title).font(.t14m).lineLimit(1)
                        if section.unchecked {
                            Text("Chưa kiểm được — mở để xem").font(.t12).foregroundStyle(Color.warn)
                        }
                        // tín hiệu = NHÃN ngắn có màu theo mức; giải thích ở chú thích khi rê chuột — liếc là biết, không phải đọc đoạn văn
                        FlowRow(spacing: 4) {
                            ForEach(section.lines) { l in
                                HStack(spacing: 5) {
                                    Circle().fill(Color.card(l.tone)).frame(width: 6, height: 6)
                                    Text(l.title).font(.t12).lineLimit(1)
                                }
                                .padding(.horizontal, 8).padding(.vertical, 3)
                                .background(Capsule().fill(Color.card(l.tone).opacity(0.13)))
                                .help(l.detail)
                            }
                        }
                    }
                    .padding(.top, 4)
                    Spacer(minLength: 4)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if let ask = section.lines.first(where: { $0.ask != nil })?.ask {
                Button { desk.section = .assistant; desk.send(ask) } label: {
                    Image(systemName: "sparkles").font(.system(size: 12)).foregroundStyle(Color.accent)
                }
                .buttonStyle(.plain).padding(.top, 8).help("Nhờ trợ lý xử lý")
            }
            Button { withAnimation(.snappy) { expanded.toggle() } } label: {
                Image(systemName: "chevron.right").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                    .rotationEffect(.degrees(expanded ? 90 : 0)).frame(width: 22, height: 22)
            }
            .buttonStyle(.plain).padding(.top, 4).help(expanded ? "Thu gọn" : "Xem nhanh, làm ngay tại đây")
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous)
            .fill(selected ? Color.accent.opacity(0.12) : (hover ? Color.primary.opacity(0.05) : .clear)))
        .onHover { hover = $0 }
        if expanded {
            CompactCard(title: s.title, query: s.query, input: [:]) { withAnimation(.snappy) { desk.open(.screen(s), show: true) } }
                .padding(.horizontal, 10).padding(.bottom, 6)
        }
    }
}

/// Đầu cột bàn việc — cùng dáng đầu cột của thanh bên trái (Rail.swift, chỉ có ở Mac), dùng được cả trên điện thoại.
private struct DeskHeader<Trailing: View>: View {
    let subtitle: String
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Việc").font(.t16).lineLimit(1)
                Text(subtitle).font(.t12).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 8)
            trailing
        }
        .frame(height: 40)
        .padding(.horizontal, 14)
        .padding(.top, titlebarInset)
        .padding(.bottom, 10)
        // đầu ngăn NỔI trên nền của chính ngăn (cùng PANE_HEADER của web): không dải nền, không đường kẻ dưới — nút viên nổi như ChatGPT
    }
}
