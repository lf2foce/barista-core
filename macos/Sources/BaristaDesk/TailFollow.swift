import CoreGraphics

/// Luật cuộn của "đuôi" cuộc trò chuyện (câu hỏi vừa gửi + câu trả lời đang stream). Không dính SwiftUI để test được.
///
/// Kiểu ChatGPT, hai pha:
///  1. Vừa gửi ⇒ câu hỏi lên ĐỈNH khung và đứng yên; chữ chảy dần xuống — KHÔNG trôi theo suốt lúc stream (người dùng đang
///     đọc từ đầu câu trả lời; khung nhúc nhích là không đọc được).
///  2. Stream xong hẳn ⇒ kéo xuống cuối câu trả lời MỘT lần (trừ khi người dùng đã tự cuộn trong lượt này: không giật họ
///     khỏi chỗ đang đọc).
struct TailFollow: Equatable {
    enum Action: Equatable {
        case none
        /// Đưa ĐỈNH đuôi (câu hỏi) lên đỉnh khung.
        case scrollTop
        /// Đưa ĐÁY đuôi (cuối câu trả lời) xuống đáy khung.
        case scrollEnd
    }

    private(set) var pinned = false
    private(set) var userDriven = false
    /// Người dùng đã tự cuộn kể từ lúc gửi câu hỏi hiện tại.
    private(set) var touched = false
    private(set) var viewport: CGFloat = 0
    /// Đáy câu trả lời trong toạ độ khung nhìn (0 = mép trên, `viewport` = mép dưới).
    private(set) var endY: CGFloat = 0
    /// Đỉnh đuôi trong toạ độ khung nhìn (0 = chạm mép trên).
    private(set) var tailTop: CGFloat = 0
    /// Đang có một lần cuộn có hoạt hoạ do chính mình phát ra: đừng chen vào, xong rồi mới kiểm lại.
    private(set) var animating = false

    /// Người dùng vừa gửi câu hỏi.
    mutating func sent() -> Action {
        pinned = true
        userDriven = false
        touched = false
        return .scrollTop
    }

    /// Câu trả lời đã stream xong và nối vào hội thoại.
    mutating func finished() -> Action {
        pinned = false
        // Chữ chưa tràn khung (đáy câu trả lời còn trong khung) thì không có gì để kéo xuống.
        return touched || endY <= viewport + 1 ? .none : .scrollEnd
    }

    /// Khung nhìn đổi chiều cao (bàn phím, thanh gợi ý biến mất…). Đang giữ câu hỏi ở đỉnh thì giữ lại cho đúng chỗ.
    mutating func viewportChanged(_ height: CGFloat) -> Action {
        viewport = height
        // Khi bàn phím / gợi ý đổi kích thước, toạ độ đỉnh cũ có thể vẫn đang là 0;
        // chủ động giữ câu hỏi ở đỉnh trước khi geometry của bố cục mới cập nhật.
        return pinned && !userDriven && !animating ? .scrollTop : .none
    }

    /// Đỉnh đuôi đổi vị trí. Đang giữ câu hỏi ở đỉnh mà nó lệch (bố cục còn xê dịch: thanh gợi ý biến mất, bàn phím, đuôi mới
    /// dựng…) thì kéo lại — kiểm soát theo vị trí THẬT chứ không đoán thời điểm bố cục ổn định.
    mutating func tailTopMoved(to y: CGFloat) -> Action {
        tailTop = y
        return holdTop()
    }

    mutating func animationBegan() { animating = true }

    mutating func animationEnded() -> Action {
        animating = false
        return holdTop()
    }

    /// Đáy câu trả lời đổi vị trí: chỉ ghi nhận, KHÔNG cuộn (khung không bám theo chữ đang stream).
    mutating func endMoved(to y: CGFloat) -> Action {
        endY = y
        return .none
    }

    mutating func userBegan() {
        userDriven = true
        touched = true
        pinned = false
    }

    mutating func userEnded(turnActive: Bool) {
        userDriven = false
    }

    private func holdTop() -> Action {
        pinned && !userDriven && !animating && abs(tailTop) > 1 ? .scrollTop : .none
    }
}
