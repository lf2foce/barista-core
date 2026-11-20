import SwiftUI

// DÒNG THỜI GIAN (0222) — khối "timeline" của thẻ + góc nhìn "Dòng thời gian" của bảng (kernel/ui.py ``b_timeline``). Server dựng nhóm / dòng / mốc / chữ ngày.
// Mac + iPhone chưa vẽ thanh Gantt (web vẽ): ở đây là DANH SÁCH THEO NHÓM có ngày — đủ đọc, bấm mở việc; không tự tính gì. Chỉ-thêm: trường lạ bỏ qua, thiếu ⇒ mặc định.

struct TimelineData: Decodable, Hashable {
    struct Item: Decodable, Hashable, Identifiable {
        let id, title: String
        let start: String
        let end: String?
        let kind, tone, status, who, dates, flag: String
        let open: CardPage.Open?
        let after: [String]

        private enum K: String, CodingKey { case id, title, start, end, kind, tone, status, who, dates, flag, open, after }
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: K.self)
            id = try c.decode(String.self, forKey: .id)
            title = try c.decode(String.self, forKey: .title)
            start = (try? c.decode(String.self, forKey: .start)) ?? ""
            end = try? c.decodeIfPresent(String.self, forKey: .end)
            kind = (try? c.decode(String.self, forKey: .kind)) ?? "bar"
            tone = (try? c.decode(String.self, forKey: .tone)) ?? "muted"
            status = (try? c.decode(String.self, forKey: .status)) ?? ""
            who = (try? c.decode(String.self, forKey: .who)) ?? ""
            dates = (try? c.decode(String.self, forKey: .dates)) ?? ""
            flag = (try? c.decode(String.self, forKey: .flag)) ?? ""
            open = try? c.decodeIfPresent(CardPage.Open.self, forKey: .open)
            after = (try? c.decode([String].self, forKey: .after)) ?? []
        }
    }
    struct Milestone: Decodable, Hashable { let on, title: String; let tone: String? }
    struct Group: Decodable, Hashable, Identifiable {
        let key, title, subtitle: String
        let start, end: String?
        let milestones: [Milestone]
        let items: [Item]
        let open: CardPage.Open?
        var id: String { key }

        private enum K: String, CodingKey { case key, title, subtitle, start, end, milestones, items, open }
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: K.self)
            key = try c.decode(String.self, forKey: .key)
            title = try c.decode(String.self, forKey: .title)
            subtitle = (try? c.decode(String.self, forKey: .subtitle)) ?? ""
            start = try? c.decodeIfPresent(String.self, forKey: .start)
            end = try? c.decodeIfPresent(String.self, forKey: .end)
            milestones = (try? c.decode([Milestone].self, forKey: .milestones)) ?? []
            items = (try? c.decode([Item].self, forKey: .items)) ?? []
            open = try? c.decodeIfPresent(CardPage.Open.self, forKey: .open)
        }
    }
    let title, today, start, end, note: String
    let groups: [Group]
    let milestones: [Milestone]

    private enum K: String, CodingKey { case title, today, start, end, note, groups, milestones }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        title = (try? c.decode(String.self, forKey: .title)) ?? ""
        today = (try? c.decode(String.self, forKey: .today)) ?? ""
        start = (try? c.decode(String.self, forKey: .start)) ?? ""
        end = (try? c.decode(String.self, forKey: .end)) ?? ""
        note = (try? c.decode(String.self, forKey: .note)) ?? ""
        groups = try c.decode([Group].self, forKey: .groups)
        milestones = (try? c.decode([Milestone].self, forKey: .milestones)) ?? []
    }

    /// "2026-12-04" ⇒ "04/12" (chỉ cắt chữ — không tính ngày).
    static func short(_ iso: String) -> String {
        let p = iso.split(separator: "-")
        return p.count == 3 ? "\(p[2])/\(p[1])" : iso
    }
}

/// Danh sách theo nhóm: tiêu đề nhóm (tên · phụ · số việc · mốc), mỗi dòng "ngày · tên · người · trạng thái". Bấm dòng ⇒ mở việc / kế hoạch.
struct TimelineList: View {
    let data: TimelineData
    var open: (CardPage.Open) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !data.milestones.isEmpty {
                ForEach(data.milestones, id: \.self) { m in
                    Label("\(m.title) \(TimelineData.short(m.on))", systemImage: "diamond.fill").font(.t12m).foregroundStyle(Color.card(m.tone))
                }
            }
            ForEach(data.groups) { g in
                VStack(alignment: .leading, spacing: 0) {
                    header(g).padding(.vertical, 6)
                    VStack(spacing: 0) {
                        ForEach(Array(g.items.enumerated()), id: \.element.id) { i, it in
                            if i > 0 { Divider() }
                            row(it)
                        }
                    }
                    .padding(.horizontal, 12)
                    .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.surface2))
                }
            }
            if !data.note.isEmpty { Text(data.note).font(.t12).foregroundStyle(.secondary) }
        }
    }

    @ViewBuilder private func header(_ g: TimelineData.Group) -> some View {
        let line = HStack(spacing: 6) {
            Text(g.title).font(.t14m)
            if !g.subtitle.isEmpty { Text(g.subtitle).font(.t12).foregroundStyle(.secondary) }
            if let s = g.start, let e = g.end { Text("\(TimelineData.short(s)) – \(TimelineData.short(e))").font(.t12).foregroundStyle(.secondary) }
            ForEach(g.milestones, id: \.self) { m in
                Label(TimelineData.short(m.on), systemImage: "diamond.fill").labelStyle(.titleAndIcon).font(.t12).foregroundStyle(Color.card(m.tone))
            }
        }
        if let o = g.open { Button { open(o) } label: { line }.buttonStyle(.plain) } else { line }
    }

    private func row(_ it: TimelineData.Item) -> some View {
        let content = HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(it.dates).font(.t12).monospacedDigit().foregroundStyle(.secondary).frame(width: 96, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text(it.title).font(.t14).frame(maxWidth: .infinity, alignment: .leading)
                if !it.who.isEmpty { Text(it.who).font(.t12).foregroundStyle(.secondary) }
                if !it.flag.isEmpty { Label(it.flag, systemImage: "exclamationmark.triangle.fill").font(.t12).foregroundStyle(Color.warn) }
            }
            if !it.status.isEmpty { Text(it.status).font(.t12m).foregroundStyle(Color.card(it.tone)) }
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
        return Group {
            if let o = it.open { Button { open(o) } label: { content }.buttonStyle(.plain) } else { content }
        }
    }
}
