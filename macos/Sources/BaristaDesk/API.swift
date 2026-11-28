import Foundation

struct APIError: LocalizedError {
    let message: String
    var status: Int = 0   // mã HTTP khi server từ chối (401 / 403 / 5xx); 0 = lỗi đọc phản hồi / lỗi nghiệp vụ trong phong bì
    var errorDescription: String? { message }
}

/// Gọi core. Đăng nhập thật: phiên ``Authorization: Bearer`` (cấp sau khi đăng nhập Google, quyết định 0040) + công ty đang chọn.
/// Dev (chỉ máy chạy core): header X-Dev-User. Server quyết quyền mỗi request — app không giữ quyền gì.
struct API: Sendable {
    let base: URL
    let email: String
    let tenant: String
    var token: String? = nil

    /// Khoá bản demo trên VPS (core đặt BARISTA_DEMO_KEY): đăng nhập chọn vai từ máy khác cần header này. Thứ tự: biến BARISTA_DEMO_KEY → Cài đặt.
    static var demoKey: String {
        ProcessInfo.processInfo.environment["BARISTA_DEMO_KEY"] ?? UserDefaults.standard.string(forKey: "demoKey") ?? ""
    }

    /// Danh tính gắn vào mọi request.
    func identify(_ req: inout URLRequest) {
        if !Self.demoKey.isEmpty { req.setValue(Self.demoKey, forHTTPHeaderField: "X-Demo-Key") }
        if let token {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        } else {
            req.setValue(email, forHTTPHeaderField: "X-Dev-User")
        }
        req.setValue(tenant, forHTTPHeaderField: "X-Tenant-Id")
    }

    /// Máy chủ khi người dùng chưa nhập gì: bản phát hành iPhone (TestFlight / App Store) trỏ máy chủ CHUNG — nhân sự chỉ bấm Đăng nhập, core tự đưa vào
    /// đúng công ty theo email đã được mời; công ty có bản cài riêng đổi ở Cài đặt máy chủ (lắc máy ở màn đăng nhập). Bản dev / Mac: core trên máy.
    /// Bản dựng lại của bên khác: đổi dòng này thành máy chủ của mình.
    #if os(iOS) && !DEBUG
    static let shippedBase = "https://barista-api.phenau.com"
    #else
    static let shippedBase = "http://127.0.0.1:8300"
    #endif

    /// Máy chủ chạy trên chính máy này (dev) — màn đăng nhập ghi rõ "dev"; máy chủ khác thì không.
    static var isLocalServer: Bool {
        ["localhost", "127.0.0.1", "::1"].contains(defaultBase.host() ?? "")
    }

    /// Thứ tự: biến BARISTA_URL (dev) → địa chỉ người dùng nhập ở Cài đặt (iPhone) → ``shippedBase``.
    static var defaultBase: URL {
        let saved = UserDefaults.standard.string(forKey: "baseURL") ?? ""
        let raw = ProcessInfo.processInfo.environment["BARISTA_URL"] ?? (saved.isEmpty ? shippedBase : saved)
        return URL(string: raw) ?? URL(string: shippedBase)!
    }

    /// ``path`` có thể kèm chuỗi truy vấn ("?dry_run=false", "?compact=1"): ``appending(path:)`` mã hoá "?" thành "%3F" ⇒ tham số không tới server
    /// (nhập Excel trên Mac từng hỏng vì thế) — tách phần truy vấn ra, gắn đúng chỗ.
    static func url(_ base: URL, _ path: String) -> URL {
        guard let q = path.firstIndex(of: "?") else { return base.appending(path: path) }
        var parts = URLComponents(url: base.appending(path: String(path[..<q])), resolvingAgainstBaseURL: false)
        parts?.percentEncodedQuery = String(path[path.index(after: q)...])
        return parts?.url ?? base.appending(path: path)
    }

    private func request(_ path: String, method: String = "GET", json: Encodable? = nil, body: Data? = nil,
                         headers: [String: String] = [:]) async throws -> (Data, Int) {
        var req = URLRequest(url: Self.url(base, path))
        req.httpMethod = method
        req.timeoutInterval = 60
        identify(&req)
        for (k, v) in headers { req.setValue(v, forHTTPHeaderField: k) }
        if let json {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode(json)
        } else if let body {
            req.httpBody = body
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        return (data, (resp as? HTTPURLResponse)?.statusCode ?? 0)
    }

    private func decode<T: Decodable>(_ data: Data, _ code: Int) throws -> T {
        if code == 401 || code == 403 || code >= 500,
           let detail = try? JSONDecoder().decode([String: String].self, from: data)["detail"] {
            throw APIError(message: detail, status: code)
        }
        do { return try JSONDecoder().decode(T.self, from: data) } catch {
            throw APIError(message: "Không đọc được phản hồi (\(code)): \(error)")
        }
    }

    func logins() async throws -> [Login] {
        let (d, c) = try await request("/v1/dev/logins")
        return try decode(d, c)
    }

    func manifest() async throws -> Manifest {
        let (d, c) = try await request("/v1/manifest")
        return try decode(d, c)
    }

    /// Thẻ gọn (0094): cùng truy vấn, cùng quyền — server cắt bớt (≤ 3 dòng, ≤ 3 cột, tổng thật nếu biết).
    func queryCompact(_ name: String, _ input: Input = [:]) async throws -> Page {
        let (d, c) = try await request("/v1/queries/\(name)?compact=1", method: "POST", json: input)
        let env: Envelope<Page> = try decode(d, c)
        guard env.status == "ok", let page = env.output else { throw APIError(message: env.error) }
        return page
    }

    /// Trang của MÀN (như web ``api.page``): thẻ về ngay với phần nhanh — phần nặng là khối ``later`` app tự tải (``LaterLoader``); bảng xin một
    /// trang (``rows``) + tổng của cả tập. Server cũ không biết hai tham số này ⇒ trả trang đủ như ``query`` (app vẫn vẽ đúng).
    func page(_ name: String, _ input: Input = [:], rows: (offset: Int, limit: Int)? = nil) async throws -> Page {
        try await query(name + "?later=1" + (rows.map { "&limit=\($0.limit)&offset=\($0.offset)" } ?? ""), input)
    }

    /// Một phần tải sau của thẻ (``ui.part`` — khối ``later`` có ``part``): server chạy lại phần nhanh, dựng đúng phần này.
    func part(_ name: String, _ input: Input, part: String) async throws -> Page {
        try await query(name + "?part=" + (part.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&=?+"))) ?? part), input)
    }

    func query(_ name: String, _ input: Input = [:]) async throws -> Page {
        let (d, c) = try await request("/v1/queries/\(name)", method: "POST", json: input)
        let env: Envelope<Page> = try decode(d, c)
        // không thấy / không còn quyền xem ⇒ mã 404 để bên gọi phân biệt với lỗi mạng (vd trao đổi bị thu quyền — 0115)
        guard env.status == "ok", let page = env.output else { throw APIError(message: env.error, status: env.status == "not_found" ? 404 : 0) }
        return page
    }

    /// Truy vấn trả DỮ LIỆU (không phải trang để vẽ) — vd danh sách trao đổi cho cột "Tin nhắn".
    func data<T: Decodable>(_ name: String, _ input: Input = [:]) async throws -> T {
        let (d, c) = try await request("/v1/queries/\(name)", method: "POST", json: input)
        let env: Envelope<T> = try decode(d, c)
        guard env.status == "ok", let out = env.output else { throw APIError(message: env.error) }
        return out
    }

    /// ``input``: giá trị các trường form mà danh sách phụ thuộc (``options_with``) — gửi qua query string.
    func options(_ name: String, _ input: [String: String] = [:]) async throws -> [Option] {
        var parts = URLComponents()
        parts.queryItems = input.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        let qs = input.isEmpty ? "" : "?" + (parts.percentEncodedQuery ?? "")
        let (d, c) = try await request("/v1/options/\(name)\(qs)")
        let env: Envelope<[Option]> = try decode(d, c)
        guard env.status == "ok", let opts = env.output else { throw APIError(message: env.error) }
        return opts
    }

    func form(_ command: String) async throws -> FormSpec {
        let (d, c) = try await request("/v1/forms/\(command)")
        return try decode(d, c)
    }

    /// Lệnh trả kết quả nghiệp vụ (kể cả bị từ chối) — không ném lỗi trừ khi mạng / server hỏng.
    func command(_ name: String, _ input: Input, key: String) async throws -> CommandResult {
        let (d, c) = try await request("/v1/commands/\(name)", method: "POST", json: input, headers: ["Idempotency-Key": key])
        return try decode(d, c)
    }

    func xlsx(query name: String, _ input: Input) async throws -> Data {
        let (d, c) = try await request("/v1/queries/\(name)/xlsx", method: "POST", json: input)
        guard c == 200 else { throw APIError(message: "Xuất Excel lỗi (\(c))") }
        return d
    }

    func download(_ path: String) async throws -> Data {
        let (d, c) = try await request(path)
        guard c == 200 else { throw APIError(message: "Không tải được file (\(c))") }
        return d
    }

    func template(_ command: String) async throws -> Data {
        let (d, c) = try await request("/v1/imports/\(command)/template")
        guard c == 200 else { throw APIError(message: "Không tải được mẫu (\(c))") }
        return d
    }

    func importRows(_ command: String, rows: [Input], dryRun: Bool) async throws -> ImportReport {
        let (d, c) = try await request("/v1/imports/\(command)?dry_run=\(dryRun)", method: "POST", json: ["rows": rows])
        let env: Envelope<ImportReport> = try decode(d, c)
        guard env.status == "ok", let r = env.output else { throw APIError(message: env.error) }
        return r
    }

    /// Tải file lên kho tài liệu (0051): server đọc chữ (PDF scan ⇒ model đọc) rồi đi qua đúng lệnh kb.add_document.
    func uploadDocument(_ data: Data, filename: String, title: String, shelf: String, kind: String) async throws -> CommandResult {
        var comps = URLComponents(url: base.appending(path: "/v1/kb/upload"), resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "title", value: title), URLQueryItem(name: "shelf", value: shelf), URLQueryItem(name: "kind", value: kind)]
        var req = URLRequest(url: comps.url!)
        req.httpMethod = "POST"
        req.timeoutInterval = 180   // PDF scan: model đọc từng trang
        identify(&req)
        // header HTTP chỉ nhận ASCII ⇒ tên file tiếng Việt mã hoá phần trăm (server giải mã)
        req.setValue(filename.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(.init(charactersIn: "._-"))) ?? "file",
                     forHTTPHeaderField: "X-Filename")
        req.httpBody = data
        let (d, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 413 || code == 422, let detail = try? JSONDecoder().decode([String: String].self, from: d)["detail"] {
            throw APIError(message: detail)
        }
        return try decode(d, code)
    }

    /// Một tệp cho lệnh NHẬN TỆP (ADR 0262): thân = bytes, tên ở `X-Filename` (mã hoá phần trăm), ô khác của nút ở query; một khoá chống lặp mỗi tệp.
    /// Server kiểm đuôi / cỡ rồi chạy lệnh qua executor — đọc ảnh / PDF bằng model có thể mất vài chục giây.
    func uploadFile(_ command: String, data: Data, filename: String, params: [String: String] = [:]) async throws -> CommandResult {
        var comps = URLComponents(url: base.appending(path: "/v1/uploads/\(command)"), resolvingAgainstBaseURL: false)!
        if !params.isEmpty { comps.queryItems = params.map { URLQueryItem(name: $0.key, value: $0.value) } }
        var req = URLRequest(url: comps.url!)
        req.httpMethod = "POST"
        req.timeoutInterval = 180
        identify(&req)
        req.setValue(filename.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(.init(charactersIn: "._-"))) ?? "file",
                     forHTTPHeaderField: "X-Filename")
        req.setValue(UUID().uuidString, forHTTPHeaderField: "Idempotency-Key")
        req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        req.httpBody = data
        let (d, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if [404, 413, 415, 422].contains(code), let detail = try? JSONDecoder().decode([String: String].self, from: d)["detail"] {
            throw APIError(message: detail)
        }
        return try decode(d, code)
    }

    func importXLSX(_ command: String, data: Data, dryRun: Bool) async throws -> ImportReport {
        let (d, c) = try await request("/v1/imports/\(command)/xlsx?dry_run=\(dryRun)", method: "POST", body: data)
        if c == 422, let detail = try? JSONDecoder().decode([String: String].self, from: d)["detail"] {
            throw APIError(message: detail)
        }
        let env: Envelope<ImportReport> = try decode(d, c)
        guard env.status == "ok", let r = env.output else { throw APIError(message: env.error) }
        return r
    }

    // MARK: trợ lý

    func suggestions() async throws -> [String] {
        let (d, c) = try await request("/v1/assistant/suggestions")
        return try decode(d, c)
    }

    /// Đề xuất trợ lý soạn sẵn hôm nay (0074 A) — cùng hình thẻ đề xuất trong chat, ``source`` = "morning".
    func morning() async throws -> [ChatCard] {
        let (d, c) = try await request("/v1/assistant/morning")
        return try decode(d, c)
    }

    func morningDecide(id: String, confirm: Bool, input: Input, key: String) async throws -> CommandResult {
        try await morningDecide(id: id, decision: confirm ? "confirm" : "dismiss", input: input, key: key)
    }

    /// ``decision``: confirm · dismiss · hoặc giá trị nút ĐỐI SOÁT server gửi ở ``card.resolve`` (gửi lại nguyên văn).
    func morningDecide(id: String, decision: String, input: Input, key: String) async throws -> CommandResult {
        let body: [String: JSON] = ["decision": .string(decision), "input": .object(input), "key": .string(key)]
        let (d, c) = try await request("/v1/assistant/morning/\(id)/decide", method: "POST", json: body)
        return try decode(d, c)
    }

    func attention() async throws -> [Attention] {
        let (d, c) = try await request("/v1/assistant/attention")
        return try decode(d, c)
    }

    func desk() async throws -> DeskPage {
        let (d, c) = try await request("/v1/desk")
        return try decode(d, c)
    }

    func threads() async throws -> [ThreadSummary] {
        let (d, c) = try await request("/v1/assistant/threads")
        return try decode(d, c)
    }

    func thread(_ id: String) async throws -> ThreadDTO {
        let (d, c) = try await request("/v1/assistant/threads/\(id)")
        return try decode(d, c)
    }

    /// Một lượt hỏi: mỗi dòng NDJSON là một sự kiện (bước, thẻ, chữ, xong / lỗi).
    func chatStream(_ message: String, threadId: String?, images: [Attachment] = [], mode: String? = nil, appId: String? = nil,
                    turnKey: String? = nil, subject: ShareTarget? = nil) -> AsyncThrowingStream<StreamEvent, Error> {
        var body: [String: JSON] = ["message": .string(message)]
        if let subject {
            body["subject"] = .object(["kind": .string(subject.subjectKind), "id": .string(subject.subjectId), "title": .string(subject.title)])
        }
        if let turnKey { body["turn_key"] = .string(turnKey) }   // ⇒ bấm "Dừng" được (stopTurn)
        if let threadId { body["thread_id"] = .string(threadId) }
        if let mode { body["mode"] = .string(mode) }   // trình tạo app (0098): lời dặn + công cụ riêng, cuộc trò chuyện riêng
        if let appId { body["app_id"] = .string(appId) }
        if !images.isEmpty {
            body["images"] = .array(images.map { .object(["media_type": .string($0.mediaType), "data": .string($0.data.base64EncodedString())]) })
        }
        return events("/v1/assistant/chat/stream", body)
    }

    /// Dừng lượt chat đang chạy của chính mình — server huỷ, phần đã làm vẫn lưu, lượt kết thúc bằng "done" (stopped).
    func stopTurn(_ turnKey: String) async throws {
        _ = try await request("/v1/assistant/chat/stop", method: "POST", json: ["turn_key": turnKey])
    }

    /// Người Xác nhận / Bỏ một thẻ đề xuất (0049): dòng đầu là decided | rejected, sau đó (nếu đủ quyết định) trợ lý chạy tiếp như chat.
    func decide(cardId: String, confirm: Bool, input: Input, key: String) -> AsyncThrowingStream<StreamEvent, Error> {
        decide(cardId: cardId, decision: confirm ? "confirm" : "dismiss", input: input, key: key)
    }

    func decide(cardId: String, decision: String, input: Input, key: String) -> AsyncThrowingStream<StreamEvent, Error> {
        events("/v1/assistant/proposals/decide", ["card_id": .string(cardId), "decision": .string(decision), "input": .object(input), "key": .string(key)])
    }

    /// Đọc chính tả: gửi đoạn ghi âm WAV ⇒ CHỈ nhận chữ chép được (không chạy lượt trợ lý) — app chèn vào ô nhập.
    func transcribe(_ audio: Data) async throws -> String {
        struct Out: Decodable { let text: String }
        let (d, c) = try await request("/v1/assistant/transcribe", method: "POST", body: audio, headers: ["Content-Type": "audio/wav"])
        let out: Out = try decode(d, c)
        return out.text
    }

    private func events(_ path: String, _ body: [String: JSON]) -> AsyncThrowingStream<StreamEvent, Error> {
        events(path, data: (try? JSONEncoder().encode(body)) ?? Data(), contentType: "application/json")
    }

    private func events(_ path: String, data: Data, contentType: String, query: [URLQueryItem] = []) -> AsyncThrowingStream<StreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var url = base.appending(path: path)
                    if !query.isEmpty { url.append(queryItems: query) }
                    var req = URLRequest(url: url)
                    req.httpMethod = "POST"
                    req.timeoutInterval = 240
                    identify(&req)
                    req.setValue(contentType, forHTTPHeaderField: "Content-Type")
                    req.httpBody = data
                    let (bytes, resp) = try await URLSession.shared.bytes(for: req)
                    guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
                        throw APIError(message: "Trợ lý không trả lời (\((resp as? HTTPURLResponse)?.statusCode ?? 0))")
                    }
                    for try await line in bytes.lines where !line.isEmpty {
                        if let ev = try? JSONDecoder().decode(StreamEvent.self, from: Data(line.utf8)) { continuation.yield(ev) }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
