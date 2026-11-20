import AVFoundation
import Observation

/// Ghi chú hiện trường (quyết định 0074 D): sale gọi / gặp khách xong bấm ghi, nói một câu, bấm lần nữa để gửi. Ghi WAV 16 kHz một kênh (model
/// nghe tốt, ~32 KB/giây); file tạm xoá ngay khi gửi — server chép lời rồi chạy một lượt trợ lý soạn đề xuất; âm thanh không lưu lâu dài.
@MainActor @Observable
final class FieldRecorder {
    private(set) var isRecording = false
    private(set) var startedAt: Date?
    @ObservationIgnored private var recorder: AVAudioRecorder?
    @ObservationIgnored private var file: URL?

    /// Bắt đầu ghi; lỗi (chưa cho phép micro, app không có mô tả quyền…) ⇒ trả câu nói cho người, không ghi.
    func start() async -> String? {
        #if os(macOS)
        guard Bundle.main.object(forInfoDictionaryKey: "NSMicrophoneUsageDescription") != nil else {
            return "Ghi âm trên Mac cần app có mô tả quyền micro (xem macos/README)."
        }
        #endif
        guard await AVCaptureDevice.requestAccess(for: .audio) else { return "Cần cho phép Barista dùng micro trong Cài đặt." }
        #if os(iOS)
        try? AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetooth])
        try? AVAudioSession.sharedInstance().setActive(true)
        #endif
        let url = FileManager.default.temporaryDirectory.appending(path: "ghi-chu-\(UUID().uuidString).wav")
        let settings: [String: Any] = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16_000, AVNumberOfChannelsKey: 1,
                                       AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false]
        guard let r = try? AVAudioRecorder(url: url, settings: settings), r.record() else { return "Không mở được micro để ghi âm." }
        recorder = r
        file = url
        startedAt = .now
        isRecording = true
        return nil
    }

    /// Dừng; trả đoạn ghi âm (dưới 1 giây coi như bấm nhầm ⇒ nil). File tạm luôn bị xoá.
    func stop() -> Data? {
        recorder?.stop()
        defer {
            if let file { try? FileManager.default.removeItem(at: file) }
            recorder = nil
            file = nil
            startedAt = nil
            isRecording = false
        }
        guard let file, let startedAt, Date.now.timeIntervalSince(startedAt) >= 1 else { return nil }
        return try? Data(contentsOf: file)
    }
}
