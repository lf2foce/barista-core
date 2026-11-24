import Foundation

/// Thanh bên theo vai (ADR 0255) — phần thuần, dùng chung Mac + iPhone; cùng luật với `web/src/lib/rail.ts`.
/// Server quyết phân hệ nào ở nhóm nào (đã lọc quyền, không lặp); đây chỉ ráp manifest thành bố cục để vẽ và giữ lời hứa "một mục hiện một lần"
/// ngay cả khi manifest lệch (khoá lạ bị bỏ, khoá trùng chỉ giữ lần đầu). Server không gửi ``rail`` (vai không có mặc định) ⇒ vẽ hết như trước.
struct RailLayout {
    enum Place: Equatable { case role, pinned, none }

    struct Item: Identifiable {
        let ws: Workspace
        /// đang hiện ở nhóm nào trên thanh bên (``none``: chỉ ở "Tất cả mục")
        let place: Place
        /// người xem tự ghim phân hệ này
        let pinned: Bool
        var id: String { ws.key }
    }

    /// false ⇒ vẽ hết phân hệ, không nhóm, không "Tất cả mục".
    let grouped: Bool
    let role: [Workspace]
    let pinned: [Workspace]
    /// Mọi phân hệ người xem thấy, theo thứ tự menu — nội dung "Tất cả mục".
    let all: [Item]

    init(_ m: Manifest?) {
        let spaces = m?.workspaces ?? []
        let flat = spaces.map { Item(ws: $0, place: .none, pinned: $0.pinned == true) }
        guard let rail = m?.rail else {
            grouped = false; role = []; pinned = []; all = flat
            return
        }
        var taken = Set<String>()
        func pick(_ keys: [String]) -> [Workspace] {
            keys.compactMap { k in
                guard let w = spaces.first(where: { $0.key == k }), !taken.contains(k) else { return nil }
                taken.insert(k)
                return w
            }
        }
        let r = pick(rail.role)
        let p = pick(rail.pinned)
        // mặc định của vai lọc hết (không còn phân hệ nào thấy được) ⇒ như chưa khai: vẽ hết, đừng để thanh bên trống
        guard !r.isEmpty else {
            grouped = false; role = []; pinned = []; all = flat
            return
        }
        grouped = true
        role = r
        pinned = p
        let roleKeys = Set(r.map(\.key)), pinKeys = Set(p.map(\.key))
        all = spaces.map { w in
            Item(ws: w, place: roleKeys.contains(w.key) ? .role : pinKeys.contains(w.key) ? .pinned : .none, pinned: w.pinned == true)
        }
    }

    /// Phân hệ theo thứ tự hiện trên thanh bên (phím tắt): vẽ hết ⇒ mọi phân hệ; có nhóm ⇒ "Theo vai" rồi "Đã ghim".
    var onRail: [Workspace] { grouped ? role + pinned : all.map(\.ws) }

    /// Phân hệ đang mở mà thanh bên không hiện (mở từ "Tất cả mục") ⇒ vẫn có một mục sáng để biết mình đang ở đâu. Vẽ hết / không ở phân hệ ⇒ nil.
    func current(_ key: String?) -> Workspace? {
        guard grouped, let key, let hit = all.first(where: { $0.ws.key == key }), hit.place == .none else { return nil }
        return hit.ws
    }

    /// Dòng phụ của một mục trong "Tất cả mục" (một mục chỉ hiện một lần — mặc định theo vai thắng, ghim vẫn ghi nhận).
    static func subtitle(_ i: Item) -> String {
        switch i.place {
        case .role: i.pinned ? "Hiện sẵn theo vai · đã ghim" : "Hiện sẵn theo vai"
        case .pinned: "Đã ghim"
        case .none: "\(i.ws.screens.count) màn"
        }
    }
}
