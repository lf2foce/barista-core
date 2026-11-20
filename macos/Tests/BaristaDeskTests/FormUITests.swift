import Foundation
import Testing
@testable import BaristaDesk

/// Lớp vẽ form (agent form D, 18/11/2026) — cùng hành vi với `web/src/lib/__tests__/form-ui.test.ts`.
struct FormUITests {
    private func field(_ key: String, kind: String = "text", required: Bool = false, help: String = "", extra: String = "") -> FieldSpec {
        let json = #"{"key":"\#(key)","label":"\#(key)","kind":"\#(kind)","required":\#(required),"help":"\#(help)","hidden":false\#(extra.isEmpty ? "" : "," + extra)}"#
        return try! JSONDecoder().decode(FieldSpec.self, from: Data(json.utf8))
    }

    @Test func markModeFollowsTheMajority() {
        #expect(FormUI.markMode([field("a", required: true), field("b", required: true), field("c")]) == .optional)
        #expect(FormUI.markMode([field("a", required: true), field("b"), field("c"), field("d")]) == .star)
        #expect(FormUI.markMode([field("a", required: true), field("b", required: true)]) == .none)
        #expect(FormUI.markMode([field("a"), field("b")]) == .none)
    }

    @Test func twoShortFieldsShareARow() {
        let rows = FormUI.pairs([field("ten"), field("ngay", kind: "date"), field("gio", kind: "time"), field("ghi", kind: "textarea"), field("tien", kind: "money")])
        #expect(rows.map { $0.map(\.key) } == [["ten"], ["ngay", "gio"], ["ghi"], ["tien"]])
        #expect(!FormUI.isShort(field("t", kind: "money", help: String(repeating: "x", count: 80))))
        #expect(!FormUI.isShort(field("khach", kind: "select", extra: #""options_query":"crm.leads""#)))
    }

    @Test func searchFoldsAccentsMatchesEveryWordAndRanks() {
        let o = [Option(value: "a@x.vn", label: "Nguyễn Thị Lan", hint: "Trưởng nhóm"), Option(value: "b@x.vn", label: "Hải Phòng Land", hint: "Dự án"),
                 Option(value: "c@x.vn", label: "Phạm Lan Anh", hint: "Kế toán")]
        #expect(FormUI.fold("Đặng Phạm") == "dang pham")
        #expect(FormUI.filter(o, "pham lan").map(\.value) == ["c@x.vn"])
        #expect(FormUI.filter(o, "ke toan").map(\.value) == ["c@x.vn"])
        #expect(FormUI.filter(o, "ng").map(\.value) == ["a@x.vn", "b@x.vn"])
        #expect(FormUI.filter(o, "an").map(\.value) == ["b@x.vn", "c@x.vn", "a@x.vn"])
        #expect(FormUI.filter(o, "zzz").isEmpty)
        #expect(FormUI.filter(o, "").count == 3)
        let w = FormUI.withRecents(o, [Option(value: "c@x.vn", label: "?"), Option(value: "gone", label: "?")])
        #expect(w.recent.map(\.value) == ["c@x.vn"] && w.rest.map(\.value) == ["a@x.vn", "b@x.vn"])
    }

    @Test func dependentOptionsRenameParametersAndWaitForVisibleSources() {
        let unit = field("unit", extra: #""options_query":"x.units","options_args":{"project_code":"project"},"options_with":["hint"]"#)
        #expect(FormUI.optionsArgs(unit) == ["hint": "hint", "project_code": "project"])
        #expect(FormUI.dependents([field("project"), unit], of: "project").map(\.key) == ["unit"])
        #expect(FormUI.optionsParams(unit, [:], visible: ["project", "unit"]).waiting)
        #expect(FormUI.optionsParams(unit, ["project": .string("VOP2")], visible: ["project", "unit"]).params == ["project_code": "VOP2"])
        #expect(!FormUI.optionsParams(unit, [:], visible: ["unit"]).waiting)   // ô nguồn ẩn trống ⇒ vẫn tải
    }

    @Test func moneyGroupsThousandsButKeepsUnitText() {
        #expect(FormUI.groupMoney("4500000") == "4.500.000")
        #expect(FormUI.groupMoney("4.500.0000") == "45.000.000")
        #expect(FormUI.groupMoney("4,5 tỷ") == "4,5 tỷ")
        #expect(FormUI.groupMoney("300tr") == "300tr")
        #expect(FormUI.groupMoney("007") == "7")
    }

    @Test func daysReadUnambiguously() throws {
        let d = try #require(FormUI.day("2026-12-01"))
        #expect(FormUI.long(d) == "1 tháng 12, 2026")
        #expect(FormUI.day("01/12/2026").map(FormUI.long) == "1 tháng 12, 2026")
    }
}
