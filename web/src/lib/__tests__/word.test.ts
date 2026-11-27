import { describe, expect, it } from "vitest";
import { PAGE, buildOps, changedSpans, clipSame, decodeAssist, decodeWord, diffWords, splitParagraphs, indexParas, normalize, originalRuns, type EditedPara, type EditedRun, type WordPara, writtenSummary } from "@/lib/word";

const RAW = {
  type: "word", document_id: "d1", title: "Hợp đồng", version: 2, editable: true, page: { w: 612, ml: 90 },
  blocks: [
    { id: "b0", t: "p", style: { heading: 1, align: "center" }, runs: [{ src: 0, text: "Tiêu đề", b: true, sz: 14 }] },
    { id: "b1", t: "p", style: {}, runs: [{ src: 0, text: "Bên A: " }, { src: 1, text: "Công ty", b: true }] },
    { id: "b2", t: "p", style: {}, locked: "có liên kết", runs: [{ text: "khoá" }] },
    { id: "b3", t: "table", cols: [30, 70], w_pct: 100, borders: true, rows: [{ cells: [{ borders: false, blocks: [{ id: "b3.r0.c0.b0", t: "p", style: {}, runs: [{ src: 0, text: "Ô" }] }] }] }] },
    { id: "b4", t: "p", style: {}, runs: [] },
    { id: "<script>", t: "p", runs: [] }, { t: "lạ" },
  ],
  notes: ["một", 2], docx_url: "/v1/word/d1/docx", upload_url: "/v1/word/upload", ops: { save: "word.save", view: "javascript:alert(1)" }, documents: [{ id: "x", title: "A", version: 1, updated_at: "2026-11-15T00:00:00Z" }, { title: "thiếu mã" }],
};

const N = { sz: null, color: null, font: null };   // lượt không nêu cỡ / màu / phông
const run = (pid: string, src: number, text: string, f: Partial<EditedRun> = {}): EditedRun => ({ pid, src, text, b: false, i: false, u: false, ...f });
const asEdited = (p: WordPara): EditedPara => ({ id: p.id, runs: p.runs.map((r) => run(p.id, r.src, r.text, { b: r.b, i: r.i, u: r.u })) });

describe("khối word", () => {
  const doc = decodeWord(RAW);
  const paras = indexParas(doc.blocks);

  it("giải mã phòng thủ: trường thiếu có mặc định, khối lạ / mã lạ bị bỏ", () => {
    expect(doc.blocks.map((b) => b.id)).toEqual(["b0", "b1", "b2", "b3", "b4"]);
    expect(doc.page).toMatchObject({ w: 612, ml: 90, mr: 72, h: 841.9 });
    expect(doc.notes).toEqual(["một"]);
    expect(doc.documents.map((d) => d.id)).toEqual(["x"]);
    expect([...paras.keys()]).toEqual(["b0", "b1", "b2", "b3.r0.c0.b0", "b4"]);
    const table = doc.blocks[3];
    expect(table.t === "table" && [table.wPct, table.borders, table.rows[0].cells[0].borders]).toEqual([100, true, false]);
    expect([doc.ops.save, doc.ops.view, doc.uploadUrl]).toEqual(["word.save", "", "/v1/word/upload"]);   // tên thao tác do máy chủ gửi; giá trị lạ bị bỏ
    expect(decodeWord(null).blocks).toEqual([]);
  });

  it("không sửa gì ⇒ không có thay đổi (kể cả khi trình duyệt cắt vụn chữ)", () => {
    const b1 = paras.get("b1")!;
    const chopped: EditedPara = { id: "b1", runs: [run("b1", 0, "Bên "), run("b1", 0, "A: "), run("b1", 1, "Công", { b: true }), run("b1", 1, " ty", { b: true })] };
    expect(buildOps(paras, [{ ids: ["b0", "b1"], paras: [asEdited(paras.get("b0")!), chopped] }])).toEqual([]);
    expect(normalize(chopped.runs, "b1")).toEqual(originalRuns(b1));
  });

  it("sửa chữ, tách đoạn bằng Enter (mã lặp), thêm đoạn mới, xoá đoạn", () => {
    const ops = buildOps(paras, [{
      ids: ["b0", "b1"],
      paras: [
        { id: null, runs: [run("", -1, "Dòng trước tiêu đề")] },                       // đoạn mới trước đoạn gốc đầu ⇒ before
        { id: "b0", runs: [run("b0", 0, "Tiêu đề mới", { b: true })] },                 // đổi chữ ⇒ set
        { id: "b0", runs: [run("b0", 0, "phần tách ra", { b: true })] },                // Enter chép mã ⇒ đoạn mới sau b0, giữ nguồn định dạng
        { id: null, runs: [run("b1", 1, "dán từ đoạn khác", { b: true })] },            // nguồn thuộc đoạn khác ⇒ src -1
      ],
    }]);
    expect(ops).toEqual([
      { op: "insert", before: "b0", runs: [{ src: -1, text: "Dòng trước tiêu đề", b: false, i: false, u: false, ...N }] },
      { op: "set", id: "b0", runs: [{ src: 0, text: "Tiêu đề mới", b: true, i: false, u: false, ...N, sz: 14 }] },
      { op: "insert", after: "b0", runs: [{ src: 0, text: "phần tách ra", b: true, i: false, u: false, ...N, sz: 14 }] },
      { op: "insert", after: "b0", runs: [{ src: -1, text: "dán từ đoạn khác", b: true, i: false, u: false, ...N }] },
      { op: "delete", id: "b1" },
    ]);
  });

  it("đổi đậm / nghiêng không đổi chữ vẫn là thay đổi", () => {
    const ops = buildOps(paras, [{ ids: ["b1"], paras: [{ id: "b1", runs: [run("b1", 0, "Bên A: ", { i: true }), run("b1", 1, "Công ty", { b: true })] }] }]);
    expect(ops).toEqual([{ op: "set", id: "b1", runs: [{ src: 0, text: "Bên A: ", b: false, i: true, u: false, ...N }, { src: 1, text: "Công ty", b: true, i: false, u: false, ...N }] }]);
  });

  it("vùng bị xoá sạch hoặc chỉ còn đoạn mới: giữ lại đoạn gốc đầu, không xoá sạch rồi thêm", () => {
    expect(buildOps(paras, [{ ids: ["b0", "b1"], paras: [] }])).toEqual([{ op: "set", id: "b0", runs: [] }, { op: "delete", id: "b1" }]);
    expect(buildOps(paras, [{ ids: ["b3.r0.c0.b0"], paras: [{ id: null, runs: [run("", -1, "Ô mới")] }] }])).toEqual([
      { op: "set", id: "b3.r0.c0.b0", runs: [{ src: -1, text: "Ô mới", b: false, i: false, u: false, ...N }] }]);
    expect(buildOps(paras, [{ ids: ["b4"], paras: [{ id: "b4", runs: [] }] }])).toEqual([]);     // đoạn trống vẫn trống
    expect(buildOps(paras, [{ ids: ["không-có"], paras: [{ id: null, runs: [run("", -1, "x")] }] }])).toEqual([]);
  });

  it("định dạng đoạn: chỉ gửi khi khác bản gốc; đoạn mới mang thứ người chọn", () => {
    const b0 = asEdited(paras.get("b0")!), b1 = asEdited(paras.get("b1")!);
    expect(buildOps(paras, [{ ids: ["b0", "b1"], paras: [{ ...b0, align: "center", line: 1 }, { ...b1, align: "right", line: 1.5, indent: 36 },
      { id: null, align: "right", runs: [run("", -1, "Ký tên")] }] }])).toEqual([
      { op: "para", id: "b1", align: "right", line: 1.5, indent: 36 },   // b0 vốn căn giữa, giãn dòng 1 ⇒ không gửi
      { op: "insert", after: "b1", runs: [{ src: -1, text: "Ký tên", b: false, i: false, u: false, ...N }], align: "right" },
    ]);
  });

  it("cỡ / màu / phông: trang giấy không nêu ⇒ như lượt gốc; nêu khác ⇒ thay đổi", () => {
    const b0 = paras.get("b0")!;
    expect(buildOps(paras, [{ ids: ["b0"], paras: [{ id: "b0", runs: [run("b0", 0, "Tiêu đề", { b: true })] }] }])).toEqual([]);            // cỡ 14 lấy từ gốc
    expect(buildOps(paras, [{ ids: ["b0"], paras: [{ id: "b0", runs: [run("b0", 0, "Tiêu ", { b: true, sz: 14 }), run("b0", 0, "đề", { b: true, sz: 20, color: "c00000", font: "Arial" })] }] }])).toEqual([
      { op: "set", id: "b0", runs: [{ src: 0, text: "Tiêu ", b: true, i: false, u: false, sz: b0.runs[0].sz, color: null, font: null },
        { src: 0, text: "đề", b: true, i: false, u: false, sz: 20, color: "C00000", font: "Arial" }] }]);
  });

  it("màn vẽ đúng mặc định của trang ở lượt gốc không nêu ⇒ không phải thay đổi; trùng màu kế thừa vẫn đổi được", () => {
    const b1 = paras.get("b1")!;   // lượt gốc không nêu cỡ / màu / phông
    const seen = (f: Partial<EditedRun>) => buildOps(paras, [{ ids: ["b1"], paras: [{ id: "b1", runs: [run("b1", 0, "Bên A: ", f), run("b1", 1, "Công ty", { b: true, ...f })] }] }]);
    expect(seen({ sz: PAGE.sz, color: PAGE.color, font: PAGE.font })).toEqual([]);
    expect(seen({ color: "0070C0" })).toEqual([{ op: "set", id: "b1", runs: [
      { src: 0, text: "Bên A: ", b: false, i: false, u: false, ...N, color: "0070C0" }, { src: 1, text: "Công ty", b: true, i: false, u: false, ...N, color: "0070C0" }] }]);
    expect(b1.runs[0].color).toBe("");
  });

  it("gửi vào phòng: nơi gửi do máy chủ liệt kê; thiếu mục share ⇒ không có nút; tên thao tác lạ bị bỏ", () => {
    const withShare = decodeWord({ ...RAW, ops: { create: "word.create", send: "word.send" }, share: { targets: [{ value: "talk.room:r1", label: "Phòng · A" }, { value: "", label: "x" }] } });
    expect([withShare.ops.create, withShare.ops.send, withShare.share]).toEqual(["word.create", "word.send", { targets: [{ value: "talk.room:r1", label: "Phòng · A" }] }]);
    expect(decodeWord(RAW).share).toBeNull();
    expect(decodeWord({ ...RAW, share: { targets: [] } }).share).toEqual({ targets: [] });   // chưa ở phòng nào: vẫn có nút, nói rõ
  });

  it("chữ nhiều dòng của trợ lý ⇒ nhiều đoạn, bỏ dòng trống", () => {
    expect(splitParagraphs("Kính gửi Ban Giám đốc,\n\n  Công ty đề xuất…  \r\nTrân trọng.")).toEqual(["Kính gửi Ban Giám đốc,", "Công ty đề xuất…", "Trân trọng."]);
    expect(splitParagraphs("Một dòng")).toEqual(["Một dòng"]);
    expect(splitParagraphs("  ")).toEqual([""]);
  });
});

describe("rà soát: so chữ theo từ", () => {
  it("một chữ sai ⇒ một cụm bỏ + một cụm thêm, gạch đúng chữ sai trên trang", () => {
    const parts = diffWords("Đề nghị thanh tóan khoản phí", "Đề nghị thanh toán khoản phí");
    expect(parts).toEqual([
      { kind: "same", text: "Đề nghị thanh " }, { kind: "del", text: "tóan" }, { kind: "ins", text: "toán" }, { kind: "same", text: " khoản phí" },
    ]);
    expect(changedSpans(parts)).toEqual([[14, 18]]);
  });

  it("viết lại cả cụm: khoảng trắng kẹp giữa không làm vỡ vụn", () => {
    const parts = diffWords("Bên em đề nghị bên anh thanh toán", "Sàn mẫu kính đề nghị Quý Chủ đầu tư thanh toán");
    expect(parts.map((x) => x.kind)).toEqual(["del", "ins", "same", "del", "ins", "same"]);
    expect(parts[0]).toEqual({ kind: "del", text: "Bên em" });
    expect(parts[4]).toEqual({ kind: "ins", text: "Quý Chủ đầu tư" });
    // ghép lại đúng hai chữ
    expect(parts.filter((x) => x.kind !== "ins").map((x) => x.text).join("")).toBe("Bên em đề nghị bên anh thanh toán");
    expect(parts.filter((x) => x.kind !== "del").map((x) => x.text).join("")).toBe("Sàn mẫu kính đề nghị Quý Chủ đầu tư thanh toán");
  });

  it("chỉ thêm chữ ⇒ gạch từ đứng ngay trước (đầu đoạn ⇒ từ ngay sau)", () => {
    expect(changedSpans(diffWords("Hạn thanh toán 30 ngày", "Hạn thanh toán là 30 ngày"))).toEqual([[10, 14]]);
    expect(changedSpans(diffWords("thanh toán", "Đề nghị thanh toán"))).toEqual([[0, 5]]);
  });

  it("chữ giống nhau dài được rút gọn quanh chỗ đổi", () => {
    const long = "Căn cứ hợp đồng môi giới đã ký giữa hai bên và các phụ lục kèm theo, ";
    const parts = clipSame(diffWords(`${long}số tiền là 54.500.000 đ`, `${long}số tiền là 54.500.000 đồng`));
    expect(parts[0].text.startsWith("…")).toBe(true);
    expect(parts[0].text.length).toBeLessThan(45);
    expect(parts.slice(1)).toEqual([{ kind: "del", text: "đ" }, { kind: "ins", text: "đồng" }]);
  });

  it("giải mã giữ lý do của từng đề xuất", () => {
    expect(decodeAssist({ edits: [{ id: "b1", before: "a", text: "b", why: "Sai chính tả" }, { id: "b2", text: "c" }] }).edits)
      .toEqual([{ id: "b1", before: "a", text: "b", why: "Sai chính tả" }, { id: "b2", before: "", text: "c", why: "" }]);
    expect(decodeAssist({ checks: [{ id: "b3", why: "Số tiền bằng chữ lệch", quote: "Năm mươi bốn triệu" }, { id: "b4" },
                                   { id: "b5", why: "Văn bản ghi 3%, biểu phí ghi 2%", quote: "3%", source: "Biểu phí môi giới" }] }).checks)
      .toEqual([{ id: "b3", why: "Số tiền bằng chữ lệch", quote: "Năm mươi bốn triệu", source: "" },   // thiếu lý do ⇒ bỏ
                { id: "b5", why: "Văn bản ghi 3%, biểu phí ghi 2%", quote: "3%", source: "Biểu phí môi giới" }]);   // lệch tài liệu tham chiếu ⇒ tên tài liệu
    expect(decodeAssist({}).checks).toEqual([]);   // máy chủ cũ / trợ lý soạn thường: không có mục ⇒ rỗng
    expect(decodeAssist({ sources: [{ title: "Sổ tay", kind: "picked", document_id: "d1", complete: false, percent: 42.4 },
                                    { title: "Biểu phí", kind: "found", document_id: "d2", complete: true, percent: 100, used: true }, { kind: "found" }] }).sources)
      .toEqual([{ title: "Sổ tay", kind: "picked", documentId: "d1", complete: false, percent: 42, used: false },
                { title: "Biểu phí", kind: "found", documentId: "d2", complete: true, percent: 100, used: true }]);   // thiếu tên ⇒ bỏ
  });
});

describe("writtenSummary", () => {
  it("đếm theo đoạn trên trang, một mục thêm nhiều dòng = nhiều đoạn", () => {
    expect(writtenSummary({ edits: [{ id: "b1", before: "a", text: "b", why: "" }], additions: [{ after: "b1", text: "x\ny" }] })).toBe("Sửa 1 đoạn, thêm 2 đoạn");
    expect(writtenSummary({ edits: [], additions: [{ after: "b1", text: "x" }] })).toBe("Thêm 1 đoạn");
    expect(writtenSummary({ edits: [], additions: [] })).toBe("");
  });
});
