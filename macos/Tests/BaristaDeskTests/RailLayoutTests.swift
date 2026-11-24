import Foundation
import Testing
@testable import BaristaDesk

/// Thanh bên theo vai (ADR 0255) — cùng luật với `web/src/lib/rail.ts` (test ở `web/src/lib/__tests__/rail.test.ts`).
struct RailLayoutTests {
    private func manifest(rail: String?, pinned: [String] = [], phoneOff: [String] = []) throws -> Manifest {
        let spaces = ["sales", "accounting", "kpi", "schedule", "hr"].map { k in
            #"{"key":"\#(k)","title":"\#(k)","icon":"folder","screens":[]"#
                + (pinned.contains(k) ? #","pinned":true"# : "") + (phoneOff.contains(k) ? #","phone":false"# : "") + "}"
        }
        let json = #"{"user":{"id":"u","name":"A B","email":"a@x.demo","role":"sale","role_label":"Sale"},"tenant":{"id":"t","name":"T"},"inbox":null,"workspaces":["#
            + spaces.joined(separator: ",") + "]" + (rail.map { #","rail":\#($0)"# } ?? "") + "}"
        return try JSONDecoder().decode(Manifest.self, from: Data(json.utf8))
    }

    @Test func noRailDrawsEveryWorkspaceUngrouped() throws {
        let l = RailLayout(try manifest(rail: nil))
        #expect(!l.grouped)
        #expect(l.onRail.map(\.key) == ["sales", "accounting", "kpi", "schedule", "hr"])
        #expect(l.current("hr") == nil)
    }

    @Test func roleThenPinnedEachOnceAndRestOnlyInAllItems() throws {
        let l = RailLayout(try manifest(rail: #"{"role":["sales","kpi","ghost","sales"],"pinned":["kpi","hr"]}"#, pinned: ["kpi", "hr"]))
        #expect(l.grouped)
        #expect(l.role.map(\.key) == ["sales", "kpi"])            // khoá lạ bỏ, trùng giữ lần đầu
        #expect(l.pinned.map(\.key) == ["hr"])                    // đã ở "Theo vai" ⇒ không lặp ở "Đã ghim"
        #expect(l.onRail.map(\.key) == ["sales", "kpi", "hr"])
        let kpi = try #require(l.all.first { $0.ws.key == "kpi" })
        #expect(kpi.place == .role && kpi.pinned)
        #expect(RailLayout.subtitle(kpi) == "Hiện sẵn theo vai · đã ghim")
        #expect(l.all.first { $0.ws.key == "schedule" }?.place == RailLayout.Place.none)
    }

    @Test func openWorkspaceOffTheRailStillShowsAsCurrent() throws {
        let l = RailLayout(try manifest(rail: #"{"role":["sales"],"pinned":[]}"#))
        #expect(l.current("schedule")?.key == "schedule")
        #expect(l.current("sales") == nil)   // đã ở thanh bên ⇒ không cần mục "Đang mở"
    }

    @Test func roleDefaultsAllHiddenFallsBackToEverything() throws {
        let l = RailLayout(try manifest(rail: #"{"role":["ghost"],"pinned":["hr"]}"#))
        #expect(!l.grouped)
        #expect(l.onRail.count == 5)
    }

    @Test func phoneDropsRailKeysOfWorkspacesHiddenOnPhone() throws {
        let m = try manifest(rail: #"{"role":["sales","hr"],"pinned":["kpi"]}"#, phoneOff: ["hr", "kpi"]).forPhone()
        #expect(m.rail?.role == ["sales"])
        #expect(m.rail?.pinned == [])
        #expect(RailLayout(m).onRail.map(\.key) == ["sales"])
    }
}
