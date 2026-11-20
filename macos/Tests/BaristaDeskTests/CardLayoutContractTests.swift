import XCTest
@testable import BaristaDesk

/// Hợp đồng thẻ 0166 / 0173: khối có `w` (bề rộng 1–12), biểu đồ "combo" (as / axis / unit_right), scatter (`x`), treemap, lọc chéo (`pick`).
final class CardLayoutContractTests: XCTestCase {
    private func card(_ blocks: String) throws -> CardPage {
        let json = """
        {"kind":"card","title":"t","subtitle":"","icon":"","actions":[],"open":[],"blocks":\(blocks)}
        """
        return try JSONDecoder().decode(CardPage.self, from: Data(json.utf8))
    }

    func testBlocksWithWidthStillDecodeAndKeepTheirOrder() throws {
        let page = try card("""
        [{"type":"kpis","w":12,"items":[]},{"type":"chart","w":6,"title":"A","kind":"bar","unit":"count","labels":["x"],"series":[{"name":"s","values":[1]}]},
         {"type":"chart","w":6,"title":"B","kind":"bar","unit":"count","labels":["x"],"series":[{"name":"s","values":[2]}]}]
        """)
        XCTAssertEqual(page.blocks.count, 3)
        guard case .kpis = page.blocks[0], case .chart(let a) = page.blocks[1], case .chart(let b) = page.blocks[2] else { return XCTFail("sai kiểu khối") }
        XCTAssertEqual([a.title, b.title], ["A", "B"])
    }

    func testComboKeepsBothAxesAndTheRightUnit() throws {
        let page = try card("""
        [{"type":"chart","w":6,"title":"GMV và tỉ lệ","kind":"combo","unit":"vnd","unit_right":"pct","labels":["T1","T2"],
          "series":[{"name":"GMV","values":[10,20],"as":"bar"},{"name":"Tỉ lệ","values":[5,null],"as":"line","axis":"right"}]}]
        """)
        guard case .chart(let c) = page.blocks[0] else { return XCTFail("không phải chart") }
        XCTAssertEqual(c.kind, "combo")
        XCTAssertEqual(c.series.map(\.mark), ["bar", "line"])
        XCTAssertEqual(c.series.map(\.right), [false, true])
        XCTAssertEqual(c.unit(of: c.series[0]), "vnd")
        XCTAssertEqual(c.unit(of: c.series[1]), "pct")   // ô số khi rê chuột: chuỗi trục phải đo bằng đơn vị của nó
        XCTAssertEqual(page.widths, [6])
    }

    /// 0173: lọc chéo (`pick`), scatter (`x`) và treemap — app native đọc được thẻ, bỏ qua trường chưa vẽ (scatter / treemap rơi về cột của `values`).
    func testPickScatterAndTreemapDecodeAndAreIgnoredGracefully() throws {
        let page = try card("""
        [{"type":"chart","title":"S","kind":"scatter","unit":"count","labels":["A","B"],"pick":{"param":"project","values":["a","b"]},
          "series":[{"name":"y","values":[1,2],"x":[10,20]}]},
         {"type":"chart","title":"T","kind":"treemap","unit":"vnd","labels":["A"],"series":[{"name":"v","values":[5]}]}]
        """)
        guard case .chart(let s) = page.blocks[0], case .chart(let t) = page.blocks[1] else { return XCTFail("không phải chart") }
        XCTAssertEqual([s.kind, t.kind], ["scatter", "treemap"])
        XCTAssertEqual(s.series[0].x, [10, 20])
        XCTAssertEqual(s.pick, ChartBlock.Pick(param: "project", values: ["a", "b"]))
    }

    /// Khối tải sau (``ui.b_later`` / ``ui.part``) đọc được; thay khối bằng trang con dời bề rộng / nút sửa của khối sau theo.
    func testLaterBlockDecodesAndIsReplacedInPlace() throws {
        var page = try card("""
        [{"type":"kpis","w":3,"items":[]},{"type":"later","query":"x.y","input":{"a":1},"part":"trend","title":"Xu hướng"},
         {"type":"text","markdown":"cuối","w":6}]
        """)
        guard case .later(let l) = page.blocks[1] else { return XCTFail("không phải later") }
        XCTAssertEqual([l.query, l.part ?? "", l.title], ["x.y", "trend", "Xu hướng"])
        page.edits = [2: ActionSpec(command: "apps.revise_block", title: "Sửa", style: "", bind: [:], preset: [:], when: [:], importable: false)]
        let sub = try card(#"[{"type":"text","markdown":"a","w":4},{"type":"text","markdown":"b","w":8}]"#)
        let out = page.replacing(1, with: sub)
        XCTAssertEqual(out.blocks.count, 4)
        XCTAssertEqual(out.widths, [3, 4, 8, 6])
        XCTAssertEqual(Array(out.edits.keys), [3])
        XCTAssertEqual(page.replacing(1, with: nil).widths, [3, 6])   // phần trống ⇒ chỗ chờ biến mất
    }

    func testTablePagingDecodes() throws {
        let json = #"{"kind":"table","title":"t","kpis":[],"columns":[],"rows":[{"id":"1"}],"empty":"","paging":{"offset":0,"limit":300,"total":1234,"complete":true}}"#
        guard case .table(let t) = try JSONDecoder().decode(Page.self, from: Data(json.utf8)) else { return XCTFail("không phải bảng") }
        XCTAssertEqual(t.paging?.total, 1234)
        XCTAssertEqual(t.paging?.limit, 300)
    }
}

/// Bố cục thẻ — cùng luật web (layout.ts, KpiRow, filter-layout.ts).
final class CardGridTests: XCTestCase {
    func testRowsJoinNeighboursWithWidthUpToTwelve() {
        XCTAssertEqual(CardGrid.rows([3, 3, 3, 3, 4, 4, 4, 8, nil, 6, 6]), [[0, 1, 2, 3], [4, 5, 6], [7], [8], [9, 10]])
        XCTAssertEqual(CardGrid.rows([nil, nil]), [[0], [1]])
    }

    func testSpansFallBackToTwoUpThenOneUp() {
        XCTAssertEqual(CardGrid.effectiveSpans([4, 4, 4], width: 1200), [4, 4, 4])
        XCTAssertEqual(CardGrid.effectiveSpans([4, 4, 4], width: 800), [6, 6, 12])   // lẻ ⇒ ô cuối cả hàng
        XCTAssertEqual(CardGrid.effectiveSpans([4, 4, 4], width: 600), [12, 12, 12])
        XCTAssertEqual(CardGrid.lines([6, 6, 12]), [[0, 1], [2]])
    }

    func testKpisShareOneRowAndBalanceLines() {
        XCTAssertEqual(CardGrid.kpiPerLine(count: 6, width: 1100, spacing: 10), 6)    // ≤ 6 ô cùng một hàng khi đủ rộng
        XCTAssertEqual(CardGrid.kpiPerLine(count: 6, width: 640, spacing: 10), 3)     // vừa 4 ⇒ 3 + 3, không 4 + 2
        XCTAssertEqual(CardGrid.kpiPerLine(count: 5, width: 300, spacing: 10), 2)     // hẹp vẫn hai cột
        XCTAssertEqual(CardGrid.kpiPerLine(count: 4, width: .infinity, spacing: 10), 4)
    }

    func testXLabelsNeverOverlap() {
        let branches = ["Không thuộc chi nhánh", "Chi nhánh Đà Nẵng", "Chi Nhánh Nha Trang", "Trụ Sở Hà Nội", "Chi nhánh Hồ Chí Minh"]
        let narrow = XLabels.fit(branches, plotWidth: 300)
        XCTAssertTrue(narrow.vertical)
        XCTAssertGreaterThan(narrow.extra, 0)
        XCTAssertFalse(XLabels.fit(["T1", "T2", "T3"], plotWidth: 300).vertical)
        XCTAssertEqual(ChartFmt.cut("Không thuộc chi nhánh nào cả hết", 24).count, 24)
    }

    func testNiceAxisAlignsTwoScales() {
        let s = NiceAxis.span([0, 1730])
        XCTAssertEqual(s.lo, 0)
        XCTAssertGreaterThanOrEqual(s.hi, 1730)
        XCTAssertEqual(s.hi, s.lo + Double(s.n) * s.step)
        let neg = NiceAxis.span([-30, 80])
        XCTAssertLessThanOrEqual(neg.lo, -30)
    }

    func testSquarifyCoversTheAreaWithoutOverlap() {
        let rects = Squarify.layout([6, 6, 4, 3, 2, 2, 1], in: CGRect(x: 0, y: 0, width: 600, height: 400))
        let area = rects.reduce(0) { $0 + $1.width * $1.height }
        XCTAssertEqual(area, 600 * 400, accuracy: 1)
        for i in rects.indices { for j in rects.indices where j > i {
            XCTAssertLessThan(rects[i].intersection(rects[j]).width * rects[i].intersection(rects[j]).height, 0.5)
        } }
    }

    private func field(_ key: String, group: String? = nil, primary: Bool? = nil) -> FieldSpec {
        let json = #"{"key":"\#(key)","label":"\#(key)","kind":"text","required":false,"help":"","hidden":false,"example":null,"fields":null"#
            + (group.map { #","group":"\#($0)""# } ?? "") + (primary.map { #","primary":\#($0)"# } ?? "") + "}"
        return try! JSONDecoder().decode(FieldSpec.self, from: Data(json.utf8))
    }

    func testFiltersSplitPrimaryFromMore() {
        // ≤ 3 ô: bày hết ra ngoài
        XCTAssertEqual(FilterSplit.split([field("a"), field("b"), field("c")]).inline.map(\.key), ["a", "b", "c"])
        // server cũ (không cờ): 2 ô đầu không gấp là chính
        let old = FilterSplit.split([field("a"), field("g", group: "Thêm"), field("b"), field("c"), field("d")])
        XCTAssertEqual(old.inline.map(\.key), ["a", "b"])
        XCTAssertEqual(old.more.map(\.key), ["g", "c", "d"])
        // server khai cờ
        let flagged = FilterSplit.split([field("a"), field("b", primary: true), field("c"), field("d", primary: true)])
        XCTAssertEqual(flagged.inline.map(\.key), ["b", "d"])
    }

    func testIconsFollowTheSharedMap() {
        XCTAssertEqual(SymbolMap.name("chart.bar.doc.horizontal.fill"), "chart.bar.xaxis")
        XCTAssertEqual(SymbolMap.name("person.2.fill"), "person.2.fill")   // không lệch ⇒ giữ nguyên tên server
    }
}
