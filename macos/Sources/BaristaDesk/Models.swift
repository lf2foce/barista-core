import Foundation

/// Giá trị JSON bất kỳ — trang / form do server mô tả nên app không biết trước kiểu từng cột.
enum JSON: Codable, Hashable, Sendable {
    case null, bool(Bool), number(Double), string(String), array([JSON]), object([String: JSON])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSON].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSON].self)) }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .number(let n):
            if n.rounded() == n, abs(n) < 9e15 { try c.encode(Int64(n)) } else { try c.encode(n) }
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }

    var isNull: Bool { if case .null = self { true } else { false } }
    var double: Double? {
        switch self {
        case .number(let n): n
        case .string(let s): Double(s)
        default: nil
        }
    }
    var bool: Bool? { if case .bool(let b) = self { b } else { nil } }
    /// Chữ thô (khoá, so sánh `when`, giá trị lựa chọn).
    var text: String {
        switch self {
        case .null: ""
        case .bool(let b): b ? "true" : "false"
        case .number(let n): n.rounded() == n && abs(n) < 9e15 ? String(Int64(n)) : String(n)
        case .string(let s): s
        case .array(let a): a.map(\.text).joined(separator: ", ")
        case .object: ""
        }
    }
}

typealias Input = [String: JSON]

// MARK: - Đăng nhập dev / manifest

struct Login: Decodable, Hashable, Identifiable {
    let tenant_id: String
    let tenant: String
    let email: String
    let name: String
    let role: String
    var role_key: String? = nil
    var group: String? = nil
    var hint: String? = nil
    var ask: String? = nil
    var id: String { tenant_id + email }
}

struct Manifest: Decodable {
    struct User: Decodable { let id, name, email, role, role_label: String; let team: String? }
    struct Tenant: Decodable { let id, name: String }
    let user: User
    let tenant: Tenant
    var workspaces: [Workspace]
    let inbox: ScreenSpec?
    struct Pin: Decodable { let workspace, key: String }
    var pins: [Pin]?  // màn ghim: của chính người đó trước, rồi theo vai (server quyết)
    /// Màn VIỆC chỉ ở bàn việc (0094) — vẫn mở được (lời chào trợ lý, thông báo, ⌘K), không lặp ở menu.
    var desk: [ScreenSpec]? = nil

    /// Bản cho iPhone: bỏ phân hệ server khai không hợp màn nhỏ (``phone: false`` — vd App của tôi, 0098) và màn ghim thuộc chúng.
    func forPhone() -> Manifest {
        var m = self
        let off = Set(workspaces.filter { $0.phone == false }.map(\.key))
        m.workspaces = workspaces.filter { !off.contains($0.key) }
        m.pins = pins?.filter { !off.contains($0.workspace) }
        return m
    }
}

/// Bàn việc (0094): một điểm vào "tôi phải làm gì" — server ráp + lọc quyền; app chỉ vẽ.
struct DeskPage: Decodable {
    struct Section: Decodable, Identifiable {
        let workspace: String
        let screen: ScreenSpec
        let lines: [Attention]
        let unchecked: Bool
        var id: String { workspace + ":" + screen.key }
    }
    struct Group: Decodable, Identifiable {
        let key: String
        let title: String
        let sections: [Section]
        var id: String { key }
    }
    let groups: [Group]
    var morning = 0   // thẻ đề xuất sáng đang mở (thẻ lấy ở /v1/assistant/morning)
    var count = 0     // số MỤC cần để ý trên nút "Việc" — server đếm
    var unchecked = 0 // số mục chưa kiểm được — nút hiện dấu xám, không để 0 trông như hết việc

    enum CodingKeys: String, CodingKey { case groups, morning, count, unchecked }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: CodingKeys.self)
        groups = try c.decode([Group].self, forKey: .groups)
        morning = try c.decodeIfPresent(Int.self, forKey: .morning) ?? 0
        count = try c.decodeIfPresent(Int.self, forKey: .count) ?? 0
        unchecked = try c.decodeIfPresent(Int.self, forKey: .unchecked) ?? 0
    }
}

struct Workspace: Decodable, Identifiable {
    let key, title, icon: String
    let screens: [ScreenSpec]
    /// false ⇒ không hiện trên iPhone (server quyết theo phân hệ — màn quá rộng cho màn nhỏ).
    var phone: Bool? = nil
    var id: String { key }
}

struct ScreenSpec: Decodable, Identifiable, Hashable {
    let key, title, icon, query, group: String
    let tint: String?   // màu theo ý nghĩa của màn do server khai (kernel/screens.py TINTS); nil ⇒ màu phân hệ
    let filters: [FieldSpec]
    let actions: [ActionSpec]
    let row_actions: [ActionSpec]
    let open: OpenSpec?
    /// Đầu vào CỐ ĐỊNH của truy vấn (0098 — màn app tự ghép mang mã app): gửi kèm mọi lần chạy, không phải bộ lọc.
    var input: Input? = nil
    /// Nút "nhờ trợ lý" trên thanh công cụ (vd "Tạo app"): đưa câu mở đầu vào ô chat.
    var ask: DetailPage.Ask? = nil
    /// Bấm MỘT lần vào dòng (core 12/11/2026): "open" ⇒ mở ``open``; tên lệnh ⇒ form của nút đó trong ``row_actions``. nil ⇒ bấm chỉ chọn dòng.
    /// Server chỉ gửi khi người xem chạy được đích.
    var row_click: String? = nil
    /// Màn kiểu báo cáo BI (0166): bộ lọc là CỘT bên trái, nội dung bên phải (hẹp ⇒ bộ lọc lên trên).
    var filters_side: Bool? = nil
    var id: String { key }
}

struct ActionSpec: Decodable, Hashable, Identifiable {
    let command, title, style: String
    let bind: [String: String]
    let preset: [String: JSON]
    let when: [String: [JSON]]
    let importable: Bool
    /// Trường ← cột của dòng, điền sẵn nhưng vẫn sửa được (khác ``bind``: cố định, ẩn). Form "Sửa" (vd sửa kệ tài liệu).
    var prefill: [String: String]? = nil
    /// Ô ĐIỀN SẴN do server soạn (vd tiêu đề việc lấy từ tin nhắn) — vẫn hiện + sửa được; khác ``preset`` (cố định, ẩn).
    var values: [String: JSON]? = nil
    var id: String { command + title }

    /// Nút dòng chỉ hiện khi dòng thoả `when` (trình bày thôi — server vẫn kiểm lại khi chạy).
    func applies(to row: Row) -> Bool {
        when.allSatisfy { key, allowed in allowed.contains { $0.text == (row.values[key]?.text ?? "") } }
    }
}

struct OpenSpec: Decodable, Hashable {
    let query: String
    let bind: [String: String]
    let title: String
}

// MARK: - Trang

enum Page: Decodable {
    case table(TablePage)
    case detail(DetailPage)
    case card(CardPage)

    private enum K: String, CodingKey { case kind }
    init(from decoder: Decoder) throws {
        switch try decoder.container(keyedBy: K.self).decode(String.self, forKey: .kind) {
        case "detail": self = .detail(try DetailPage(from: decoder))
        case "card": self = .card(try CardPage(from: decoder))
        default: self = .table(try TablePage(from: decoder))
        }
    }

    var title: String {
        switch self {
        case .table(let t): t.title
        case .detail(let d): d.title
        case .card(let c): c.title
        }
    }
}

/// Thẻ gọn (0094 đợt 2): bao nhiêu dòng đang hiện, tổng thật nếu server BIẾT (không biết ⇒ nil — app ghi "N dòng đầu", không đoán).
struct Compact: Decodable, Hashable {
    let shown: Int
    let total: Int?
}

extension Page {
    var compact: Compact? {
        switch self {
        case .table(let t): t.compact
        case .card(let c): c.compact
        case .detail: nil
        }
    }
}

struct KPI: Decodable, Hashable {
    let label: String
    let value: JSON
    let format: String
    let tone: String?
    let hint: String
    /// Nguồn của con số (0097): bấm ô số ⇒ mở đúng các dòng tạo nên nó (server đã lọc quyền; không có ⇒ ô số không bấm được).
    struct Drill: Decodable, Hashable { let query: String; let input: [String: JSON]; let sum: String? }
    let drill: Drill?
    /// "Sửa khối này" (app tự ghép — 0098): chuột phải ô số ⇒ form một ô "muốn đổi gì".
    var edit: ActionSpec? = nil
}

struct Column: Decodable, Hashable, Identifiable {
    let key, label, format: String
    let labels: [String: String]?
    let tones: [String: String]?
    let width: Int?
    var id: String { key }
}

struct Row: Identifiable, Hashable {
    let id: String
    let values: [String: JSON]
}

/// Góc nhìn khác của CÙNG các dòng bảng (0087) — server khai bảng nào hợp Kanban (theo cột trạng thái) / Lịch (theo cột ngày).
struct TableViews: Decodable, Hashable {
    struct BoardSpec: Decodable, Hashable {
        struct Col: Decodable, Hashable { let key, title: String; let tone: String? }
        let by, title: String
        let subtitle: String?
        let columns: [Col]
        let move: CardPage.Board.Move?
    }
    struct CalendarSpec: Decodable, Hashable { let date, title: String; let subtitle: String? }
    let board: BoardSpec?
    let calendar: CalendarSpec?
    let timeline: TimelineData?   // dòng thời gian (0222): server dựng sẵn cả khối, không tham chiếu cột
}

struct TablePage: Decodable {
    let title: String
    let kpis: [KPI]
    let columns: [Column]
    var rows: [Row]
    let empty: String
    let note: String
    let views: TableViews?
    let compact: Compact?   // thẻ gọn (0094): bản server đã cắt
    /// Bảng nằm trong THẺ (app tự ghép — 0098) mang nút của dòng + mở chi tiết của riêng nó (màn thì khai ở ScreenSpec).
    var rowActions: [ActionSpec] = []
    var openRow: OpenSpec? = nil
    /// Nút dưới câu ``empty`` khi bảng không có dòng (vd "Thêm người / phòng").
    var emptyActions: [ActionSpec] = []
    /// Trang phía server (18/11/2026): app xin ``?limit=&offset=`` ⇒ chỉ một trang + tổng của cả tập. Thiếu ⇒ bảng đủ dòng như cũ.
    struct Paging: Decodable, Hashable { let offset, limit, total: Int; var complete: Bool? = nil }
    var paging: Paging? = nil

    private enum K: String, CodingKey { case title, kpis, columns, rows, row_key, empty, note, views, compact, row_actions, open, empty_actions, paging }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        title = try c.decode(String.self, forKey: .title)
        kpis = try c.decode([KPI].self, forKey: .kpis)
        columns = try c.decode([Column].self, forKey: .columns)
        empty = try c.decode(String.self, forKey: .empty)
        note = (try? c.decode(String.self, forKey: .note)) ?? ""
        views = try? c.decodeIfPresent(TableViews.self, forKey: .views)
        compact = try? c.decodeIfPresent(Compact.self, forKey: .compact)
        rowActions = (try? c.decodeIfPresent([ActionSpec].self, forKey: .row_actions)) ?? []
        openRow = try? c.decodeIfPresent(OpenSpec.self, forKey: .open)
        emptyActions = (try? c.decodeIfPresent([ActionSpec].self, forKey: .empty_actions)) ?? []
        paging = try? c.decodeIfPresent(Paging.self, forKey: .paging)
        let key = (try? c.decode(String.self, forKey: .row_key)) ?? "id"
        let raw = try c.decode([[String: JSON]].self, forKey: .rows)
        rows = raw.enumerated().map { i, v in Row(id: v[key].map { $0.text.isEmpty ? "\(i)" : $0.text } ?? "\(i)", values: v) }
    }
}

struct DetailPage: Decodable {
    struct Field: Decodable, Hashable { let label: String; let value: JSON; let format: String }
    /// Bảng thật của mục (0102 — vd bảng phí công văn); ``total`` = hàng cộng.
    struct Table: Decodable, Hashable { let columns: [String]; let rows: [[String]]; let total: [String]?
    }
    struct Section: Decodable, Hashable { let title: String; let fields: [Field]; let text: String?; let table: Table? }
    /// Bản gốc (0102): PDF xem ngay trên trang (Word / Excel máy chủ đã đổi sang PDF); ``page`` = trang trích dẫn.
    struct Original: Decodable, Hashable { let title: String; let path: String; let filename: String?; let page: Int? }
    /// File tải về (vd giấy tờ .docx) — đường dẫn API, app gọi kèm danh tính rồi lưu.
    struct Link: Decodable, Hashable { let title: String; let path: String; let filename: String?; let page: Int? }
    let title, subtitle: String
    let sections: [Section]
    let links: [Link]?
    let actions: [ActionSpec]?
    /// "document": vẽ như văn bản liền mạch (tài liệu kho) thay vì hồ sơ một bản ghi.
    let style: String?
    let original: Original?
    /// "extracted": các mục là chữ bóc / OCR từ bản gốc (bản phụ cho tìm kiếm / trợ lý) ⇒ mặc định mở bản gốc.
    let text_role: String?
    /// Nút "hỏi trợ lý về cái này": đưa câu mở đầu vào ô chat để người gõ tiếp.
    /// ``mode == "builder"`` ⇒ mở TRÌNH TẠO APP (0098) thay vì đưa câu vào trợ lý cá nhân; ``app_id`` ⇒ trình tạo làm trên app đó.
    struct Ask: Decodable, Hashable { let title: String; let prompt: String; var mode: String? = nil; var app_id: String? = nil }
    let ask: Ask?
}

// MARK: - Lệnh / form / nhập

/// Dòng của ô chọn. ``hint`` = dòng phụ mờ dưới nhãn (SĐT, dự án, vai…) — thêm 1.61; thiếu ⇒ chỉ nhãn.
struct Option: Decodable, Hashable {
    let value, label: String
    var hint: String? = nil
}

struct FieldSpec: Decodable, Hashable, Identifiable {
    let key, label, kind: String
    let required: Bool
    let help: String
    let hidden: Bool
    let `default`: JSON?
    let options: [Option]?
    let options_query: String?
    /// Danh sách chọn tuỳ giá trị các trường này của chính form (vd mã tin nhắn) — gửi kèm khi tải ``options_query``.
    var options_with: [String]? = nil
    /// Như ``options_with`` nhưng đổi tên: ``{tham_số_truy_vấn: trường_của_form}`` (1.61). Trường đó đang hiện ⇒ danh sách tải lại + ô này bị xoá khi nó đổi.
    var options_args: [String: String]? = nil
    /// Nhóm gấp lại của bộ lọc (vd "Thêm điều kiện"): ô cùng nhóm nằm sau một nút bấm; không có ⇒ luôn hiện.
    var group: String? = nil
    /// Chữ mờ của ô trống (vd "Hôm nay") — thiếu ⇒ "Tất cả".
    var placeholder: String? = nil
    /// Ô lọc CHÍNH của màn (server khai; thiếu ⇒ 2 ô đầu không gấp — rà bố cục 18/11): nằm ngoài, ô còn lại vào nút "Bộ lọc".
    var primary: Bool? = nil
    let example: JSON?
    let fields: [FieldSpec]?   // kind "rows": ô con của mỗi dòng (vd các việc của kế hoạch: việc · người nhận · hạn)
    var id: String { key }
}

struct FormSpec: Decodable {
    let command, title, description: String
    let importable: Bool
    let fields: [FieldSpec]
    /// LUẬT XÁC NHẬN MỘT CHỖ (ADR 0253): "none" = đảo được; "consequence" = hộp nói hệ quả bằng `effect`. Server thêm — app cũ bỏ qua.
    let confirm: String?
    let effect: String?
}

struct CommandResult: Decodable {
    let status: String
    let output: JSON?
    let error: String
    let approval_id: String?
    let replayed: Bool
}

struct Envelope<T: Decodable>: Decodable {
    let status: String
    let output: T?
    let error: String
}

struct ImportReport: Decodable {
    struct Line: Decodable, Identifiable, Hashable {
        let line: Int
        let status: String
        let error: String?
        var id: Int { line }
    }
    let command, title: String
    let dry_run: Bool
    let total, ok, failed: Int
    let unknown_columns: [String]
    let rows: [Line]
}

// MARK: - Trợ lý

struct Step: Identifiable, Hashable, Decodable {
    let n: Int
    let tool: String
    let title: String
    var status: String
    var error: String?
    var summary: String?   // "Số PN 2 · Giá tối đa 4,5 tỷ → 84 dòng"
    var card_id: String?   // thẻ bước này tạo — bấm bước để mở
    var id: Int { n }
    /// nil = đang chạy; true = xong (kể cả thành đề xuất / chờ duyệt); false = bị chặn / lỗi.
    var done: Bool? { status == "running" ? nil : ["ok", "proposed", "pending_approval", "dismissed"].contains(status) }
}

/// Thẻ trợ lý tạo: "page" (kết quả một truy vấn) hoặc "proposal" (lệnh ghi agent soạn — người bấm Xác nhận mới chạy).
struct ChatCard: Decodable, Identifiable {
    let id: String
    let kind: String
    let title: String
    let subtitle: String?
    let query: String?
    let input: Input?
    let page: Page?
    let command: String?
    let fields: [FieldSpec]?
    /// Đề xuất thuộc một lượt đang DỪNG chờ người (0049): Xác nhận / Bỏ qua ``decide`` ⇒ trợ lý chạy tiếp. Thiếu ⇒ thẻ cũ: gọi lệnh trực tiếp.
    var resumable: Bool? = nil
    /// "morning": đề xuất trợ lý soạn sẵn buổi sáng (0074 A) — Xác nhận / Bỏ qua ``API.morningDecide``, không thuộc lượt chat nào.
    var source: String? = nil
    /// Thẻ gọn ngay trong câu trả lời (0094 đợt 2): server cắt từ chính kết quả vừa chạy; "Mở" xem bản đủ.
    var preview: Page? = nil
    /// Đề xuất BÊN LỀ (vd ghi nhớ): lượt KHÔNG dừng chờ — một dòng nhỏ dưới câu trả lời, quyết qua ``decide``, xong không chạy tiếp.
    var aside: Bool? = nil
    /// Bên lề: tên nút xác nhận (tên lệnh, vd "Ghi nhớ").
    var verb: String? = nil

    var op: String { query ?? command ?? "" }
    var isAside: Bool { kind == "proposal" && aside == true }
    var isProposal: Bool { kind == "proposal" }
}

struct ChatMessage: Identifiable {
    let id = UUID()
    let role: String  // user · assistant · system
    var text: String
    var cards: [ChatCard] = []
    var steps: [Step] = []
    var at: Date = .now
    var images: [Data] = []   // ảnh gửi kèm câu hỏi — chỉ trong phiên này (server không lưu ảnh)
    var imagesGone = 0        // mở lại cuộc trò chuyện cũ: chỉ biết đã gửi mấy ảnh
}

/// Ảnh đính kèm câu hỏi (ảnh chat với khách, phiếu cọc…): đã nén JPEG, cạnh dài ≤ 2000 px.
struct Attachment: Identifiable, Hashable {
    let id = UUID()
    let data: Data
    let mediaType: String
}

struct LiveTurn {
    var status = "Đang suy nghĩ…"
    var text = ""
    var steps: [Step] = []
    var cards: [ChatCard] = []
}

struct StreamEvent: Decodable {
    let type: String
    let text: String?
    let message: String?
    let thread_id: String?
    let card: ChatCard?
    let n: Int?
    let tool: String?
    let title: String?
    let status: String?
    let error: String?
    let summary: String?
    let card_id: String?
    let resuming: Bool?
    let replayed: Bool?
    let pending: Bool?
}

struct ThreadSummary: Decodable, Identifiable, Hashable {
    let id: String
    let title: String
    let updated_at: String
}

struct ThreadDTO: Decodable {
    struct Turn: Decodable {
        let role: String
        let text: String
        let cards: [ChatCard]?
        let steps: [Step]?
        let at: String?
        struct Attached: Decodable { let kind: String }
        let attachments: [Attached]?   // chỉ "có N ảnh" — server không lưu ảnh
        /// Đề xuất của lượt đã quyết (0049): mở lại cuộc trò chuyện thì thẻ hiện "Đã xong / Đã bỏ", không hiện như đang chờ.
        struct Decision: Decodable { let card_id: String; let status: String; let message: String? }
        struct Pending: Decodable { let calls: [String: Decision]? }
        let decided: [Decision]?
        let pending: Pending?
        let asides: [String: Decision]?   // đề xuất bên lề theo mã thẻ
    }
    let id: String
    let title: String
    let turns: [Turn]
}

/// Việc cần chú ý hôm nay do server tính (theo quyền người xem): bấm ⇒ mở màn tương ứng; `ask` ⇒ nhờ trợ lý.
struct Attention: Decodable, Identifiable, Hashable {
    let tone: String
    let title: String
    let detail: String
    let query: String
    var ask: String? = nil
    var id: String { query + title }
}

extension Notification.Name {
    /// Mở hộp "Cài đặt máy chủ" (iPhone) — phát từ màn đăng nhập hoặc khi lắc máy.
    static let baristaOpenServerSettings = Notification.Name("baristaOpenServerSettings")
}
