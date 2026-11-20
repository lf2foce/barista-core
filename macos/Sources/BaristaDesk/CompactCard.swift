import SwiftUI

/// THẺ GỌN (0094 đợt 2, compact-card.tsx): CHÍNH trang của một truy vấn, server cắt bớt (≤ 3 dòng theo thứ tự module đã xếp, ≤ 3 cột, bỏ thanh công
/// cụ) — vẽ bằng bộ vẽ trang sẵn có nên nút trong khối (ghi đã gọi, kéo trạng thái…) chạy y như màn đầy đủ. Dùng chung cho câu trả lời trong chat
/// và mục bung trên bàn việc. "3/28" chỉ khi server biết tổng thật. Bấm một nút xong ⇒ tải lại đúng truy vấn đó; "Mở" ⇒ bản đầy đủ.
/// Trên iPhone (một ngăn) đây là chỗ xem / làm nhanh mà không rời cuộc trò chuyện.
struct CompactCard: View {
    @Environment(Desk.self) private var desk
    let title: String
    let query: String
    let input: Input
    var initial: Page? = nil
    let onOpen: () -> Void
    @State private var page: Page?
    @State private var loading = false
    @State private var failed = false

    private var count: String {
        guard let c = (page ?? initial)?.compact, c.shown > 0 else { return "" }
        if let total = c.total { return total > c.shown ? "\(c.shown)/\(total)" : "\(total)" }
        return "\(c.shown) dòng đầu"
    }

    var body: some View {
        let shown = page ?? initial
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text(title).font(.t14m).lineLimit(1)
                Spacer(minLength: 6)
                if !count.isEmpty {
                    Text(count).font(.t12).monospacedDigit().foregroundStyle(.secondary)
                        .help(shown?.compact?.total == nil ? "Chưa biết tổng — mở để xem đủ" : "")
                }
                if loading { ProgressView().controlSize(.mini) }
                Button { onOpen() } label: {
                    HStack(spacing: 3) { Text("Mở").font(.t12m); Image(systemName: "arrow.up.right").font(.system(size: 10, weight: .semibold)) }
                }
                .buttonStyle(.plain).foregroundStyle(Color.accent)
            }
            .padding(.horizontal, 12).padding(.vertical, 8)
            Rectangle().fill(Color.hairline).frame(height: 1)
            Group {
                if failed {
                    Text("Chưa tải được — có thể quyền hoặc dữ liệu vừa đổi. Bấm “Mở” để xem.").font(.t12).foregroundStyle(Color.warn)
                } else if let shown {
                    switch shown {
                    case .card(let c): CardView(page: c, onChanged: { Task { await reload() } })
                    case .table(let t):
                        if t.rows.isEmpty { Text(t.empty).font(.t12).foregroundStyle(.secondary) }
                        else { DataTable(page: t, selection: .constant([]), menu: { _ in EmptyView() }, open: { _ in onOpen() }) }
                    case .detail: Text("Bấm “Mở” để xem chi tiết.").font(.t12).foregroundStyle(.secondary)
                    }
                } else {
                    Text("Đang tải…").font(.t12).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 10)
            .opacity(loading && shown != nil ? 0.6 : 1)
        }
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.surface))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.primary.opacity(0.12)))
        .task { if initial == nil && page == nil { await load() } }   // mục bung trên bàn việc: tải một lần bằng quyền lúc này
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do { page = try await desk.api.queryCompact(query, input); failed = false } catch { failed = true }
    }

    /// Làm xong một việc ngay trên thẻ ⇒ tải lại đúng truy vấn này (dòng vừa làm biến mất) + bàn việc / số trên nút cập nhật.
    private func reload() async {
        await load()
        await desk.refreshInbox()
    }
}
