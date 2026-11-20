import CoreGraphics
import Testing
@testable import BaristaDesk

struct TailFollowTests {
    private func sentWith(viewport: CGFloat = 600) -> TailFollow {
        var f = TailFollow()
        _ = f.viewportChanged(viewport)
        _ = f.sent()
        return f
    }

    @Test func sendingPinsQuestionToTop() {
        var f = TailFollow()
        #expect(f.sent() == .scrollTop)
    }

    @Test func streamingNeverScrollsTheView() {
        var f = sentWith()
        for y in stride(from: 100.0, through: 2000.0, by: 100.0) {
            #expect(f.endMoved(to: y) == .none)   // dù chữ đã tràn khung từ lâu, khung vẫn đứng yên
        }
    }

    @Test func finishingScrollsToTheEndOnceWhenTheUserDidNotTouch() {
        var f = sentWith()
        _ = f.endMoved(to: 900)
        #expect(f.finished() == .scrollEnd)
    }

    @Test func finishingAShortAnswerHasNothingToScroll() {
        var f = sentWith()
        _ = f.endMoved(to: 380)               // đáy câu trả lời còn trong khung (600)
        #expect(f.finished() == .none)
    }

    @Test func finishingDoesNotYankAUserWhoScrolledDuringTheTurn() {
        var f = sentWith()
        _ = f.endMoved(to: 900)
        f.userBegan()
        f.userEnded(turnActive: true)
        #expect(f.finished() == .none)
    }

    @Test func newQuestionForgetsEarlierTouch() {
        var f = sentWith()
        f.userBegan()
        _ = f.sent()
        _ = f.endMoved(to: 900)
        #expect(f.finished() == .scrollEnd)
    }

    @Test func viewportChangeWhilePinnedReassertsTop() {
        var f = sentWith(viewport: 600)
        #expect(f.viewportChanged(650) == .scrollTop)   // thanh gợi ý vừa biến mất, khung cao lên
        #expect(f.viewportChanged(400) == .scrollTop)   // bàn phím bật lên
    }

    @Test func userScrollingWhilePinnedReleasesTheQuestion() {
        var f = sentWith()
        f.userBegan()
        #expect(f.viewportChanged(500) == .none)  // không kéo câu hỏi về đỉnh nữa
    }

    @Test func pinnedQuestionDriftingFromTopIsPulledBack() {
        var f = sentWith()
        #expect(f.tailTopMoved(to: 42) == .scrollTop)    // bố cục xê dịch sau lượt cuộn đầu
        #expect(f.tailTopMoved(to: 0.5) == .none)        // đã về đỉnh (dung sai 1pt)
    }

    @Test func noPullBackWhileOurOwnAnimationRuns() {
        var f = sentWith()
        f.animationBegan()
        #expect(f.tailTopMoved(to: 300) == .none)        // đang trượt có hoạt hoạ, đừng chen
        #expect(f.animationEnded() == .scrollTop)        // trượt xong mà vẫn lệch ⇒ kéo lại
    }

    @Test func noPullBackOnceUserTookOverOrTurnFinished() {
        var f = sentWith()
        f.userBegan()
        #expect(f.tailTopMoved(to: 80) == .none)
        var g = sentWith()
        _ = g.finished()
        #expect(g.tailTopMoved(to: -300) == .none)
    }
}
