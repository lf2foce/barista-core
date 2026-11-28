import SwiftUI

/// Xin phép trước khi gửi dữ liệu cho dịch vụ AI bên thứ ba (App Store 5.1.1(i) / 5.1.2(i)): NÓI RÕ dữ liệu gì, gửi cho ai, để làm gì, và người dùng
/// bấm đồng ý NGAY TRONG APP — chính sách quyền riêng tư thôi là chưa đủ. Lưu trên máy; từ chối ⇒ không gửi gì cho trợ lý (câu hỏi giữ lại trong ô).
/// Chặn ở ``Desk/send(_:)`` (hỏi bằng chữ) và ``Desk/toggleVoice()`` (giọng nói); nhắn tin giữa đồng nghiệp (Talk) không đi qua AI nên không hỏi.
enum AIConsent {
    private static let key = "aiConsentAccepted"
    static var accepted: Bool {
        get { UserDefaults.standard.bool(forKey: key) || ProcessInfo.processInfo.environment["BARISTA_AI_CONSENT"] == "1" }   // dev / chụp màn
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }
    static let privacyURL = URL(string: "https://cloud.phenau.com/privacy")!
}

struct AIConsentSheet: View {
    let onDecide: (Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Sử dụng trợ lý AI").font(.t20)
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Để trợ lý trả lời và soạn sẵn việc cho bạn, nội dung bạn gửi được xử lý bởi dịch vụ AI bên thứ ba (Google Gemini) thông qua máy chủ Barista.")
                        .font(.t14)
                    section("Dữ liệu được gửi", ["Câu hỏi bạn nhập hoặc nói", "Ảnh bạn đính kèm",
                                                 "Dữ liệu công ty cần để trả lời, trong đúng quyền của bạn (khách hàng, việc, phiếu, số liệu…)"])
                    section("Gửi cho ai", ["Google Gemini, qua máy chủ Barista. Không bán hay chia sẻ cho bên khác."])
                    section("Mục đích", ["Trả lời câu hỏi, tóm tắt, soạn sẵn đề xuất để bạn duyệt."])
                    Text("Nếu bạn chọn \"Để sau\", trợ lý sẽ không nhận dữ liệu của bạn. Xem thêm tại [Chính sách quyền riêng tư](\(AIConsent.privacyURL.absoluteString)).")
                        .font(.t12).foregroundStyle(.secondary)
                }
            }
            HStack(spacing: 12) {
                Button("Để sau") { onDecide(false) }
                    .buttonStyle(.bordered).controlSize(.large).frame(maxWidth: .infinity)
                Button("Đồng ý") { onDecide(true) }
                    .buttonStyle(.borderedProminent).controlSize(.large).frame(maxWidth: .infinity)
            }
        }
        .padding(24)
        .frame(minWidth: 320, idealWidth: 420)
        #if os(iOS)
        .presentationDetents([.medium, .large])
        #endif
        .interactiveDismissDisabled()
    }

    private func section(_ title: String, _ items: [String]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.t14m)
            ForEach(items, id: \.self) { Text("• \($0)").font(.t14).foregroundStyle(.secondary) }
        }
    }
}
