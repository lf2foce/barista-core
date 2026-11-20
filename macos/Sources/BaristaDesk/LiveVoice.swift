import AVFoundation
import Foundation
import Observation
#if os(iOS)
import UIKit
#else
import AppKit
#endif

/// Chế độ giọng nói (quyết định 0042): nói ↔ nói qua Gemini Live CHẠY Ở BACKEND — app chỉ gửi micro (PCM16 16 kHz) và phát loa (PCM16 24 kHz),
/// khoá model không nằm trong app. Mọi câu hỏi công việc đi qua một lượt chat thật ở backend (cùng quyền người hỏi); bước / thẻ stream về
/// đúng như chat gõ chữ (``onChat``), Gemini chỉ đọc tóm tắt. Giọng nói không có quyền gì hơn gõ chữ.
@MainActor @Observable
final class LiveVoice {
    enum State: Equatable { case off, connecting, listening, thinking, speaking }

    var state: State = .off
    var userCaption = ""
    var botCaption = ""
    var level: Float = 0
    var problem: String?
    /// Tạm dừng nghe: giữ phiên, ngừng gửi micro.
    var paused = false

    /// Desk gắn vào: một câu hỏi công việc bắt đầu / sự kiện của lượt chat (như /assistant/chat/stream) / cuộc trò chuyện được tạo.
    @ObservationIgnored var onChatStart: ((String) -> Void)?
    @ObservationIgnored var onChat: (([String: Any]) -> Void)?
    @ObservationIgnored var onThread: ((String) -> Void)?

    @ObservationIgnored private var api: API?
    @ObservationIgnored private var threadId: String?
    @ObservationIgnored private var ws: URLSessionWebSocketTask?
    @ObservationIgnored private var audio: LiveAudio?
    @ObservationIgnored private var userTurnDone = false
    @ObservationIgnored private var retries = 0
    @ObservationIgnored private var pinger: Task<Void, Never>?
    @ObservationIgnored private var micOpenAt = Date.distantPast
    @ObservationIgnored private var pendingTools = 0
    @ObservationIgnored private var generation = UUID()
    @ObservationIgnored private var lastLevelAt = 0.0
    /// "Dạ, em xem ngay ạ" (đóng sẵn trong app): Gemini Live không tự nói trước khi gọi công cụ ⇒ vài giây im lặng lúc tra số liệu.
    @ObservationIgnored private lazy var ackPCM: Data? = Bundle.main.url(forResource: "ack", withExtension: "wav")
        .flatMap { try? Data(contentsOf: $0) }
        .flatMap { d in d.range(of: Data("data".utf8)).map { d.subdata(in: ($0.upperBound + 4)..<d.count) } }

    var isOn: Bool { state != .off }

    func start(api: API, threadId: String?) {
        guard state == .off else { return }
        #if os(macOS)
        // `swift run` không có Info.plist ⇒ hỏi quyền micro sẽ làm macOS đóng app; chỉ cho dùng khi đã nhúng mô tả quyền
        guard Bundle.main.object(forInfoDictionaryKey: "NSMicrophoneUsageDescription") != nil else {
            problem = "Giọng nói trên Mac cần app có mô tả quyền micro (xem macos/README)."
            return
        }
        #endif
        self.api = api
        self.threadId = threadId
        generation = UUID()
        let mine = generation
        problem = nil
        pendingTools = 0
        retries = 0
        state = .connecting
        userCaption = ""
        botCaption = ""
        #if os(iOS)
        UIApplication.shared.isIdleTimerDisabled = true  // màn hình không tự tắt giữa lúc nói chuyện
        #endif
        Task {
            let allowed = await AVCaptureDevice.requestAccess(for: .audio)
            guard generation == mine else { return }
            guard allowed else {
                #if os(macOS)
                // đã từ chối một lần thì macOS không hỏi lại — mở thẳng mục Micro để bật, khỏi tìm
                problem = "Chưa cho phép dùng micro — bật Barista Desk ở Cài đặt hệ thống › Quyền riêng tư & Bảo mật › Micro"
                if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone") {
                    NSWorkspace.shared.open(url)
                }
                #else
                problem = "Chưa cho phép dùng micro (Cài đặt › Barista › Micro)"
                #endif
                stop()
                return
            }
            connect()
        }
    }

    func stop() {
        generation = UUID()
        endPendingChat("Đã đóng giọng nói trước khi nhận đủ kết quả; anh/chị có thể hỏi lại.")
        pinger?.cancel()
        send(["type": "stop"])
        ws?.cancel(with: .normalClosure, reason: nil)
        ws = nil
        audio?.stop()
        audio = nil
        state = .off
        level = 0
        paused = false
        #if os(iOS)
        UIApplication.shared.isIdleTimerDisabled = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
    }

    func togglePause() {
        paused.toggle()
        if paused { level = 0 }
    }

    /// Chạm quả cầu khi máy đang nói ⇒ ngắt lời, nghe tiếp.
    func interrupt() {
        guard state == .speaking else { return }
        audio?.flush()
        state = .listening
    }

    var canSendText: Bool { ws != nil && pendingTools == 0 && (state == .listening || state == .speaking) }

    /// "Gõ cho trợ lý": gửi chữ vào cùng phiên — trả lời vẫn bằng giọng.
    @discardableResult
    func say(_ text: String) -> Bool {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, ws != nil, pendingTools == 0, state == .listening || state == .speaking else { return false }
        if state == .speaking { audio?.flush() }
        userCaption = t
        botCaption = ""
        userTurnDone = true
        state = .thinking
        send(["type": "text", "text": t])
        return true
    }

    // MARK: - Kết nối

    private func connect() {
        guard let api else { return }
        var comps = URLComponents(url: api.base.appending(path: "/v1/assistant/voice"), resolvingAgainstBaseURL: false)!
        comps.scheme = comps.scheme == "https" ? "wss" : "ws"
        if let threadId { comps.queryItems = [URLQueryItem(name: "thread_id", value: threadId)] }
        var req = URLRequest(url: comps.url!)
        api.identify(&req)
        let task = URLSession.shared.webSocketTask(with: req)
        task.maximumMessageSize = 16 * 1024 * 1024
        ws = task
        task.resume()
        receive()
        watchSetup()
    }

    /// Rớt kết nối giữa chừng (đổi wifi / 4G) ⇒ nối lại tối đa 3 lần, giữ micro đang chạy và tiếp tục đúng cuộc nói chuyện.
    private func reconnect(_ why: String) {
        guard state != .off else { return }
        endPendingChat("Kết nối giọng nói bị gián đoạn trong lúc tra dữ liệu; anh/chị hỏi lại sau khi nối lại nhé.")
        pinger?.cancel()
        ws?.cancel(with: .goingAway, reason: nil)
        ws = nil
        guard retries < 3 else {
            problem = "Mất kết nối giọng nói (\(why)) — chạm nút mic để nói tiếp."
            stop()
            return
        }
        retries += 1
        state = .connecting
        let mine = generation
        Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(400 * (self?.retries ?? 1)))
            guard let self, self.generation == mine, self.state == .connecting else { return }
            self.connect()
        }
    }

    private func endPendingChat(_ detail: String) {
        let interrupted = pendingTools > 0
        pendingTools = 0
        if interrupted { onChat?(["type": "error", "detail": detail]) }
    }

    private func watchSetup() {
        let socket = ws
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(15))
            guard let self, self.ws === socket, self.state == .connecting else { return }
            self.problem = "Chưa kết nối được giọng nói (mạng, khoá hoặc model ở máy chủ) — thử lại sau."
            self.stop()
        }
    }

    private func watchMic() {
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(6))
            guard let self, self.isOn, !self.paused, let a = self.audio else { return }
            let d = a.diagnostics
            if d.taps == 0 {
                self.problem = "Micro không có tín hiệu — kiểm tra Cài đặt › Barista › Micro, hoặc app khác đang giữ micro."
            } else if d.sent == 0 {
                self.problem = "Micro có tiếng nhưng chưa gửi được\(d.note.map { " (\($0))" } ?? "")."
            }
        }
    }

    private func send(_ obj: [String: Any]) {
        guard let ws, let data = try? JSONSerialization.data(withJSONObject: obj), let text = String(data: data, encoding: .utf8) else { return }
        ws.send(.string(text)) { _ in }
    }

    private func receive() {
        guard let socket = ws else { return }
        socket.receive { [weak self] result in
            Task { @MainActor in
                // callback của socket đã đóng không được sửa trạng thái / thẻ của phiên mới
                guard let self, self.ws === socket else { return }
                switch result {
                case .failure(let e):
                    self.reconnect(e.localizedDescription)
                case .success(let msg):
                    let data: Data? = switch msg {
                    case .data(let d): d
                    case .string(let s): s.data(using: .utf8)
                    @unknown default: nil
                    }
                    if let data, let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { self.handle(json) }
                    self.receive()
                }
            }
        }
    }

    private func handle(_ m: [String: Any]) {
        switch m["type"] as? String {
        case "ready":
            if let t = m["thread_id"] as? String, !t.isEmpty { threadId = t; onThread?(t) }
            if audio == nil { startAudio() } else { state = .listening }
            retries = 0
            problem = nil
            pinger?.cancel()
            pinger = Task { [weak self] in  // giữ đường WebSocket sống qua NAT di động
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(20))
                    self?.ws?.sendPing { _ in }
                }
            }
        case "reconnecting":
            state = .connecting
        case "audio":
            if let b64 = m["data"] as? String, let pcm = Data(base64Encoded: b64) {
                if state != .speaking { state = .speaking }
                userTurnDone = true
                audio?.play(pcm16k24: pcm)
            }
        case "user_caption":
            if let t = m["text"] as? String {
                if userTurnDone { userCaption = ""; botCaption = ""; userTurnDone = false }
                userCaption += t
            }
        case "bot_caption":
            if let t = m["text"] as? String { botCaption += t }
        case "interrupted":
            audio?.flush()
            botCaption = ""
            state = .listening
        case "turn_complete":
            if state == .thinking && pendingTools == 0 { state = .listening }
        case "chat_start":
            pendingTools += 1
            if pendingTools == 1 { acknowledge() }
            state = .thinking
            userTurnDone = true
            botCaption = ""
            onChatStart?(m["question"] as? String ?? userCaption)
        case "chat":
            if let ev = m["event"] as? [String: Any] {
                if let t = ev["thread_id"] as? String, !t.isEmpty, t != "<null>" { threadId = t; onThread?(t) }
                onChat?(ev)
                if ["reply", "error"].contains(ev["type"] as? String ?? "") { pendingTools = max(0, pendingTools - 1) }
            }
        case "error":
            problem = m["detail"] as? String ?? "Lỗi giọng nói"
            stop()
        default: break
        }
    }

    private func acknowledge() {
        // phát qua CHÍNH đường loa của trợ lý (cùng engine) + khoá micro lúc phát để khỏi nghe lại chính mình
        guard let pcm = ackPCM, let audio, state != .speaking else { return }
        micOpenAt = max(micOpenAt, Date().addingTimeInterval(Double(pcm.count) / 48000 + 0.3))
        audio.play(pcm16k24: pcm)
    }

    private func startAudio() {
        do {
            let a = LiveAudio()
            a.onChunk = { [weak self] data in
                // dựng khung JSON ngay trên luồng audio (không đụng MainActor)
                let frame = Self.audioFrame(data.base64EncodedString())
                Task { @MainActor in
                    guard let self, !self.paused, Date() >= self.micOpenAt else { return }
                    // micro KHOÁ lúc trợ lý đang nói — ngắt lời bằng chạm quả cầu (khử vọng chưa lọc sạch tiếng loa)
                    guard self.state == .listening || self.state == .thinking else { return }
                    self.ws?.send(.string(frame)) { _ in }
                }
            }
            a.onLevel = { [weak self] l in
                Task { @MainActor in
                    guard let self, !self.paused else { return }
                    let now = CACurrentMediaTime()
                    let quantized = (l * 25).rounded() / 25
                    guard (now - self.lastLevelAt >= 0.08 && abs(quantized - self.level) >= 0.03) || (quantized == 0 && self.level != 0) else { return }
                    self.lastLevelAt = now
                    self.level = quantized
                }
            }
            a.onDrained = { [weak self] in
                Task { @MainActor in
                    guard let self, self.state == .speaking else { return }
                    // đuôi tiếng loa còn vang: mở micro ngay thì trợ lý nghe lại chính câu vừa nói (iPhone không bật khử vọng)
                    #if os(iOS)
                    self.micOpenAt = Date().addingTimeInterval(0.35)
                    #endif
                    self.level = 0
                    self.state = .listening
                }
            }
            try a.start()
            audio = a
            state = .listening
            watchMic()
            // dev: tự hỏi một câu khi vừa nối (kiểm thử không cần nói vào micro)
            if let q = ProcessInfo.processInfo.environment["BARISTA_SAY"], !q.isEmpty { say(q) }
        } catch {
            problem = "Không mở được micro / loa: \(error.localizedDescription)"
            stop()
        }
    }

    nonisolated static func audioFrame(_ b64: String) -> String { #"{"type":"audio","data":""# + b64 + #""}"# }
}

/// Micro → PCM16 16 kHz mono (gửi lên) và PCM16 24 kHz (nhận về) → loa. Chạy trên luồng âm thanh, không đụng UI.
final class LiveAudio: @unchecked Sendable {
    var onChunk: ((Data) -> Void)?
    var onLevel: ((Float) -> Void)?
    var onDrained: (() -> Void)?

    private var engine = AVAudioEngine()
    private var player = AVAudioPlayerNode()
    private let playFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)!
    private let sendFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
    private let lock = NSLock()
    private var queued = 0
    private var generation = 0
    private var sent = 0
    private var taps = 0
    private var lastNote: String?

    /// Số lần micro gọi về, số gói tiếng đã gửi + lỗi gần nhất (chẩn đoán "nói mà không nghe").
    var diagnostics: (taps: Int, sent: Int, note: String?) { lock.withLock { (taps, sent, lastNote) } }
    private func note(_ s: String) { lock.withLock { lastNote = s } }

    /// Thử bật khử tiếng vọng (nói chen được khi máy đang nói); thiết bị không hỗ trợ thì chạy thường — micro đã được
    /// ngắt trong lúc máy nói nên vẫn không tự nghe lại mình.
    func start() throws {
        #if os(iOS)
        // iPhone: micro + loa ngoài (không phải loa nghe gọi), cho phép tai nghe Bluetooth
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try session.setActive(true)
        #endif
        do {
            // iPhone: KHÔNG bật khử vọng của AVAudioEngine — đã thấy micro không gọi về lần nào (Gemini không nghe gì,
            // app im lặng). Micro đã tự ngắt gửi trong lúc máy nói nên không tự nghe lại mình. Mac giữ khử vọng.
            #if os(iOS)
            try configure(voiceProcessing: false)
            #else
            try configure(voiceProcessing: ProcessInfo.processInfo.environment["BARISTA_NO_VP"] != "1")
            #endif
        } catch {
            engine.stop()
            engine = AVAudioEngine()
            player = AVAudioPlayerNode()
            try configure(voiceProcessing: false)
        }
    }

    private func configure(voiceProcessing: Bool) throws {
        let input = engine.inputNode
        if voiceProcessing { try input.setVoiceProcessingEnabled(true) }
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0, inFormat.channelCount > 0 else {
            throw APIError(message: "micro chưa sẵn sàng (\(inFormat))")
        }
        // Tự lấy kênh 0 rồi tự hạ về 16 kHz (trung bình từng cửa sổ — lọc chống răng cưa đủ cho giọng nói). Không dùng
        // AVAudioConverter: với khử vọng của iPhone (nhiều kênh / tần số lạ) nó trả 0 khung mà KHÔNG báo lỗi ⇒ Gemini
        // không nghe thấy gì, app thì im lặng.
        let rate = inFormat.sampleRate
        input.installTap(onBus: 0, bufferSize: 2048, format: inFormat) { [weak self] buf, _ in
            guard let self else { return }
            self.lock.withLock { self.taps += 1 }
            let n = Int(buf.frameLength)
            guard n > 0 else { return }
            let stride = buf.format.isInterleaved ? Int(buf.format.channelCount) : 1
            let sample: (Int) -> Float
            if let f = buf.floatChannelData {
                sample = { f[0][$0 * stride] }
            } else if let i = buf.int16ChannelData {
                sample = { Float(i[0][$0 * stride]) / 32768 }
            } else {
                self.note("định dạng micro lạ: \(buf.format)")
                return
            }
            let ratio = rate / 16000
            let count = Int(Double(n) / ratio)
            guard count > 0 else { return }
            var pcm = [Int16](repeating: 0, count: count)
            var energy: Float = 0
            for k in 0..<count {
                let lo = Int(Double(k) * ratio), hi = max(lo + 1, min(n, Int(Double(k + 1) * ratio)))
                var acc: Float = 0
                for j in lo..<hi { acc += sample(j) }
                let v = max(-1, min(1, acc / Float(hi - lo)))
                energy += v * v
                pcm[k] = Int16(v * 32767)
            }
            self.lock.withLock { self.sent += 1 }
            if self.isQuiet { self.onLevel?(min(1, sqrt(energy / Float(count)) * 10)) }
            self.onChunk?(pcm.withUnsafeBufferPointer { Data(buffer: $0) })
        }
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: playFormat)
        engine.prepare()
        try engine.start()
        player.play()
    }

    private var isQuiet: Bool { lock.withLock { queued == 0 } }

    func play(pcm16k24 data: Data) {
        let n = data.count / 2
        guard n > 0, let buf = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: AVAudioFrameCount(n)) else { return }
        buf.frameLength = AVAudioFrameCount(n)
        var sum: Float = 0
        data.withUnsafeBytes { raw in
            let src = raw.bindMemory(to: Int16.self)
            let dst = buf.floatChannelData![0]
            for i in 0..<n {
                let s = Float(Int16(littleEndian: src[i])) / 32768
                dst[i] = s
                sum += s * s
            }
        }
        onLevel?(min(1, sqrt(sum / Float(n)) * 6))
        let gen = lock.withLock { queued += 1; return generation }
        player.scheduleBuffer(buf, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            guard let self else { return }
            let drained = self.lock.withLock { () -> Bool in
                guard gen == self.generation else { return false }
                self.queued -= 1
                return self.queued == 0
            }
            if drained { self.onDrained?() }
        }
        if !player.isPlaying { player.play() }
    }

    /// Ngắt lời: bỏ hết âm thanh đang chờ phát.
    func flush() {
        lock.withLock { generation += 1; queued = 0 }
        player.stop()
        player.play()
    }

    func stop() {
        flush()
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }
}
