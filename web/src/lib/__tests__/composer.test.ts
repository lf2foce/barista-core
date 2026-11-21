import { describe, expect, it } from "vitest";
import { applyComposeEvent, liveTurn, retryTarget, splitPlaceholders } from "../composer";
import { decodePage } from "../decode";

describe("composer", () => {
  it("tách chỗ giữ cần số liệu", () => {
    expect(splitPlaceholders("Có [cần số liệu: số căn] căn.")).toEqual([
      { text: "Có ", hole: false },
      { text: "[cần số liệu: số căn]", hole: true },
      { text: " căn.", hole: false },
    ]);
  });

  it("giải mã khối composer dung thứ, khối con có trang bảng", () => {
    const page = decodePage({
      kind: "card", title: "Bản", blocks: [
        { type: "composer", session_id: "s1", kind: "slides", brief: "x", brand: { color: "zz", logo: "javascript:alert(1)" },
          blocks: [{ id: "b1", type: "data", origin: "data", page: { kind: "table", columns: [{ key: "a", label: "A", format: "number" }], rows: [{ id: "0", a: 3 }] } },
                   { id: "b2", type: "paragraph", text: "Chào", flags: ["Số “9”…"] }] },
        { type: "text", markdown: "dự phòng" },
      ],
    });
    if (page.kind !== "card") throw new Error("card");
    const c = page.blocks[0];
    if (c.type !== "composer") throw new Error("composer");
    expect(c.brand.color).toBe("1F4E79");
    expect(c.brand.logo).toBe("");
    expect(c.blocks[0].page?.rows.length).toBe(1);
    expect(c.blocks[1].flags).toHaveLength(1);
    expect(c.stats.flagged).toBe(0);
  });

  it("giải mã bố cục tờ giới thiệu: phần lạ bị bỏ, khối có chỗ đứng + đoạn chữ, lần chia sẻ", () => {
    const page = decodePage({
      kind: "card", title: "Tờ", blocks: [{
        type: "composer", session_id: "s1", kind: "brochure",
        blocks: [{ id: "b1", type: "image", slot: "cover", document_id: "d1", caption: "Phối cảnh" },
                 { id: "b2", type: "data", slot: "tiles", page: { kind: "table", columns: [], rows: [] }, texts: [{ title: "Chính sách", text: "Chiết khấu 2%" }, { title: "x", text: "" }] }],
        layout: { template: "brochure", templates: [{ key: "brochure", label: "Tờ giới thiệu", about: "Bìa ảnh" }], slots: [{ key: "cover", label: "Bìa" }],
                  sections: [{ kind: "cover", style: "hero", hero: "b1", text: [] },
                             { kind: "tiles", cols: 9, items: [{ block: "b2", label: "Căn còn bán", value: "12" }], ids: ["b2"] },
                             { kind: "columns", cols: 2, columns: [["b3"], []] }, { kind: "sau-nay" },
                             { kind: "footer", ids: ["b4"], lines: { b4: ["Chuyên viên: An", 7] } }] },
        shares: [{ id: "h1", recipient: "Chị Lan", views: 3, active: true }, { id: "h2", views: "x" }],
      }],
    });
    if (page.kind !== "card" || page.blocks[0].type !== "composer") throw new Error("composer");
    const c = page.blocks[0];
    expect(c.blocks[0]).toMatchObject({ slot: "cover", document_id: "d1" });
    expect(c.blocks[1].texts).toEqual([{ title: "Chính sách", text: "Chiết khấu 2%" }]);
    expect(c.layout.template).toBe("brochure");
    expect(c.layout.sections.map((x) => x.kind)).toEqual(["cover", "tiles", "columns", "footer"]);   // loại lạ bỏ qua, không vỡ
    expect(c.layout.sections[3]).toMatchObject({ lines: { b4: ["Chuyên viên: An", "7"] } });
    const tiles = c.layout.sections[1];
    expect(tiles.kind === "tiles" && tiles.cols).toBe(4);                                   // chặn số cột ngoài khoảng
    expect(c.shares).toEqual([
      { id: "h1", recipient: "Chị Lan", version: 0, at: "", expires_at: "", views: 3, last_viewed_at: "", active: true },
      { id: "h2", recipient: "", version: 0, at: "", expires_at: "", views: 0, last_viewed_at: "", active: false },
    ]);
  });

  it("bản soạn cũ (không bố cục, không chia sẻ) ⇒ mặc định rỗng", () => {
    const page = decodePage({ kind: "card", title: "Cũ", blocks: [{ type: "composer", session_id: "s", blocks: [{ id: "b1", type: "paragraph", text: "x" }] }] });
    if (page.kind !== "card" || page.blocks[0].type !== "composer") throw new Error("composer");
    expect(page.blocks[0].layout).toEqual({ template: "", templates: [], slots: [], sections: [] });
    expect(page.blocks[0].shares).toEqual([]);
    expect(page.blocks[0].blocks[0].slot).toBe("");
  });

  it("ấn phẩm gắn hồ sơ (0249): hồ sơ / khách / công thức / mẫu + định dạng theo loại; bản cũ thiếu ⇒ rỗng", () => {
    const page = decodePage({
      kind: "card", title: "Ấn phẩm", blocks: [{
        type: "composer", session_id: "s", kind: "flyer", from_record: true,
        subject: { kind: "orderdesk.order", id: "o1", label: "DH-1 · Tư vấn" }, customer: { kind: "crm.lead", id: "l1", label: "Khách Một" },
        recipes: [{ key: "demo_order", label: "Tờ rơi một đơn", piece: "flyer", needs_subject: true, customer: true }, { label: "thiếu khoá" }],
        kinds: [{ key: "flyer", label: "Tờ rơi", templates: [{ key: "flyer", label: "Tờ rơi" }], formats: [{ key: "pdf", label: "PDF" }] }],
        formats: [{ key: "pdf", label: "PDF" }, { key: "html", label: "Trang web (.html)" }],
      }],
    });
    if (page.kind !== "card" || page.blocks[0].type !== "composer") throw new Error("composer");
    const c = page.blocks[0];
    expect(c.subject).toEqual({ kind: "orderdesk.order", id: "o1", label: "DH-1 · Tư vấn" });
    expect(c.customer.label).toBe("Khách Một");
    expect(c.from_record).toBe(true);
    expect(c.recipes).toEqual([{ key: "demo_order", label: "Tờ rơi một đơn", about: "", piece: "flyer", needs_subject: true, customer: true }]);
    expect(c.kinds[0].templates.map((t) => t.key)).toEqual(["flyer"]);
    expect(c.formats[0].key).toBe("pdf");
    const old = decodePage({ kind: "card", title: "Cũ", blocks: [{ type: "composer", session_id: "s" }] });
    if (old.kind !== "card" || old.blocks[0].type !== "composer") throw new Error("composer");
    expect(old.blocks[0].subject).toEqual({ kind: "", id: "", label: "" });
    expect(old.blocks[0].recipes).toEqual([]);
    expect(old.blocks[0].from_record).toBe(false);
  });

  it("giải mã lượt trò chuyện + lịch sử, thiếu trường ⇒ mặc định", () => {
    const page = decodePage({
      kind: "card", title: "Soạn", blocks: [{ type: "composer", session_id: "", turns: [{ role: "user", text: "Báo cáo" }, { role: "assistant", status: "failed", text: "Chưa cấu hình mô hình AI — báo quản trị", steps: [{ title: "Giao dịch" }] }, { role: "lạ" }],
                                              history: [{ id: "s1", title: "Bản 1", status: "failed" }] }],
    });
    if (page.kind !== "card" || page.blocks[0].type !== "composer") throw new Error("composer");
    const c = page.blocks[0];
    expect(c.turns.map((t) => t.role)).toEqual(["user", "assistant", "assistant"]);
    expect(c.turns[1].steps).toEqual([{ title: "Giao dịch", status: "ok" }]);
    expect(c.history[0]).toMatchObject({ id: "s1", status: "failed" });
    expect(c.kind).toBe("brochure");   // thiếu loại ⇒ tờ giới thiệu ("Văn bản" không còn là loại soạn mới — 0249)
    expect(retryTarget(c.turns.slice(0, 2))?.text).toBe("Báo cáo");
    expect(retryTarget(c.turns.slice(0, 1))).toBeNull();
  });

  it("ghép luồng soạn: bước chạy → xong, bảng vừa lấy, bản đầy đủ, lỗi", () => {
    let t = liveTurn("Báo cáo tháng");
    for (const ev of [
      { type: "session", session_id: "s1" },
      { type: "status", text: "Đang lập dàn ý" },
      { type: "step", title: "Giao dịch", status: "running" },
      { type: "step", title: "Giao dịch", status: "ok" },
      { type: "step", title: "Công nợ", status: "running" },
      { type: "step", title: "Công nợ", status: "error" },
      { type: "data", block: { id: "d1", type: "data", origin: "data", page: { kind: "table", columns: [], rows: [] } } },
      { type: "blocks", title: "Báo cáo", blocks: [{ id: "b1", type: "heading", text: "Tình hình" }] },
      { type: "done", status: "ready" },
    ]) t = applyComposeEvent(t, ev);
    expect(t.sessionId).toBe("s1");
    expect(t.status).toBe("Đang lập dàn ý");
    expect(t.steps).toEqual([{ title: "Giao dịch", status: "ok" }, { title: "Công nợ", status: "error" }]);
    expect(t.data[0].page?.kind).toBe("table");
    expect(t.blocks?.[0].text).toBe("Tình hình");
    expect(t.done && t.error === "").toBe(true);
    const failed = applyComposeEvent(liveTurn("x"), { type: "error", message: "Công ty đang soạn 2 bản khác" });
    expect(failed.sessionId).toBe("");
    expect(failed.error).toBe("Công ty đang soạn 2 bản khác");
    expect(applyComposeEvent(liveTurn("x"), { type: "done", status: "failed" }).error).toBe("Chưa soạn được — thử lại");
  });

  it("giải mã lý do của bước lỗi + lượt dở / đã dừng; thử lại cũng áp cho lượt đã dừng", () => {
    const page = decodePage({
      kind: "card", title: "Soạn", blocks: [{ type: "composer", session_id: "s1", turns: [
        { role: "user", text: "Báo cáo" },
        { role: "assistant", status: "stopped", text: "Đã dừng", partial: true, steps: [{ title: "Giao dịch", status: "error", reason: "AI gửi đầu vào chưa đúng với truy vấn này" }] },
      ] }],
    });
    if (page.kind !== "card" || page.blocks[0].type !== "composer") throw new Error("composer");
    const turns = page.blocks[0].turns;
    expect(turns[1]).toMatchObject({ status: "stopped", partial: true });
    expect(turns[1].steps[0].reason).toContain("đầu vào");
    expect(retryTarget(turns)?.text).toBe("Báo cáo");
    expect(retryTarget([{ ...turns[1], status: "ready" }])).toBeNull();
    // bước lỗi trong luồng mang lý do
    let t = liveTurn("x");
    for (const ev of [{ type: "step", title: "G", status: "running" }, { type: "step", title: "G", status: "error", reason: "không có quyền" }]) t = applyComposeEvent(t, ev);
    expect(t.steps).toEqual([{ title: "G", status: "error", reason: "không có quyền" }]);
    expect(applyComposeEvent(t, { type: "done", status: "stopped" }).error).toBe("");   // "đã dừng" không phải lỗi
  });

  it("giải mã ảnh trích từ PDF (bản thu nhỏ) và chỗ giữ ảnh chỉ ở màn soạn", () => {
    const page = decodePage({
      kind: "card", title: "Tờ", blocks: [{
        type: "composer", session_id: "s1", kind: "brochure",
        blocks: [{ id: "b1", type: "placeholder", slot: "cover", text: "Cần ảnh dự án", caption: "Tải ảnh vào Kho tài liệu" },
                 { id: "b2", type: "image", slot: "grid", document_id: "d9", derived: true, thumb: "data:image/jpeg;base64,AAAA" },
                 { id: "b3", type: "image", slot: "grid", document_id: "d1" }],
        layout: { template: "editorial", templates: [], slots: [], sections: [] },
      }],
    });
    if (page.kind !== "card" || page.blocks[0].type !== "composer") throw new Error("composer");
    const [ph, pdf, lib] = page.blocks[0].blocks;
    expect(ph).toMatchObject({ type: "placeholder", text: "Cần ảnh dự án", derived: false, thumb: "" });
    expect(pdf).toMatchObject({ derived: true, thumb: "data:image/jpeg;base64,AAAA" });
    expect(lib).toMatchObject({ derived: false, thumb: "" });
  });

  it("bước lấy số liệu lỗi rồi AI tự sửa đầu vào lấy được ⇒ gỡ dòng lỗi; lỗi chưa gỡ được vẫn giữ", () => {
    let t = liveTurn("Báo cáo tháng");
    for (const ev of [
      { type: "step", title: "Tổng hợp lead", status: "running" }, { type: "step", title: "Tổng hợp lead", status: "error", reason: "AI gửi đầu vào chưa đúng" },
      { type: "step", title: "Doanh số", status: "running" }, { type: "step", title: "Doanh số", status: "error", reason: "chưa có quyền" },
      { type: "step", title: "Tổng hợp lead", status: "running" }, { type: "step", title: "Tổng hợp lead", status: "ok" },
    ]) t = applyComposeEvent(t, ev as never);
    expect(t.steps).toEqual([{ title: "Doanh số", status: "error", reason: "chưa có quyền" }, { title: "Tổng hợp lead", status: "ok" }]);
  });
});
