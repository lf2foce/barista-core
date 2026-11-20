import { describe, expect, it } from "vitest";
import { acceptAll, longDate, nameClipboardFile, shelfInfo, withoutSiblingReplace, pendingProposals, pool, rejectReason, reviewOf, updateInput, type IntakeDoc } from "../kb-intake";

const doc = (over: Partial<IntakeDoc> = {}): IntakeDoc => ({
  status: "added",
  filename: "a.docx",
  document_id: "d1",
  title: "Quy chế A",
  kind: "policy",
  shelf: "common",
  context_code: "",
  guessed: ["title"],
  suggestion: { effective_from: "2027-01-01", evidence: "áp dụng từ 01/01/2027", replaces_id: "old1", replaces_title: "Quy chế A cũ" },
  ...over,
});

describe("rejectReason", () => {
  it("nhận các loại file của kho, báo sớm loại lạ / rỗng / quá 30 MB", () => {
    for (const n of ["a.pdf", "b.DOCX", "c.xlsx", "d.csv", "e.pptx", "f.HEIC", "g.zip", "h.md"]) expect(rejectReason({ name: n, size: 10 })).toBeNull();
    expect(rejectReason({ name: "x.mp4", size: 10 })).toContain(".mp4");
    expect(rejectReason({ name: "x.pdf", size: 0 })).toBe("File rỗng");
    expect(rejectReason({ name: "x.pdf", size: 31 * 1024 * 1024 })).toBe("File quá 30 MB");
  });
});

describe("updateInput — chỉ gửi phần người đã đổi hoặc chấp nhận", () => {
  it("chưa đổi gì ⇒ không gửi", () => {
    expect(updateInput(reviewOf(doc()))).toBeNull();
  });
  it("sửa tên / loại / phạm vi / kệ", () => {
    const r = { ...reviewOf(doc()), title: " Quy chế B ", kind: "legal", context_code: "HSR", shelf: "finance" };
    expect(updateInput(r)).toEqual({ document_id: "d1", title: "Quy chế B", kind: "legal", context_code: "HSR", shelf: "finance" });
  });
  it("bỏ phạm vi đã đoán ⇒ no_context", () => {
    expect(updateInput({ ...reviewOf(doc({ context_code: "HSR" })), context_code: "" })).toEqual({ document_id: "d1", no_context: true });
  });
  it("chấp nhận đề xuất ⇒ ngày + thay bản cũ; ngày người chọn thắng đề xuất", () => {
    const r = reviewOf(doc());
    expect(updateInput({ ...r, acceptFrom: true, acceptReplace: true })).toEqual({ document_id: "d1", effective_from: "2027-01-01", replaces_document_id: "old1" });
    expect(updateInput({ ...r, from: "2027-02-01", acceptFrom: true })).toMatchObject({ effective_from: "2027-02-01" });
  });
  it("tài liệu trùng không bao giờ gửi gì", () => {
    expect(updateInput({ ...reviewOf(doc({ status: "duplicate", document_id: undefined })), title: "x" })).toBeNull();
  });
});

describe("đề xuất chờ chấp nhận", () => {
  it("đếm đề xuất chưa chấp nhận; chấp nhận tất cả thì hết", () => {
    const rs = [reviewOf(doc()), reviewOf(doc({ suggestion: null })), reviewOf(doc({ status: "duplicate" }))];
    expect(rs.reduce((n, r) => n + pendingProposals(r), 0)).toBe(2);
    expect(acceptAll(rs).reduce((n, r) => n + pendingProposals(r), 0)).toBe(0);
  });
});

describe("nameClipboardFile", () => {
  it("ảnh dán không tên thật ⇒ đặt tên theo giờ; file có tên giữ nguyên", () => {
    const named = nameClipboardFile(new File(["x"], "image.png", { type: "image/png" }), new Date(2026, 10, 14, 9, 5, 7));
    expect(named.name).toBe("Ảnh dán 20261114-090507.png");
    const keep = new File(["x"], "bao-gia.pdf");
    expect(nameClipboardFile(keep)).toBe(keep);
  });
});

describe("pool", () => {
  it("không chạy quá giới hạn cùng lúc, chạy hết", async () => {
    let live = 0;
    let peak = 0;
    const done: number[] = [];
    await pool([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
      live++;
      peak = Math.max(peak, live);
      await new Promise((r) => setTimeout(r, 5));
      live--;
      done.push(n);
    });
    expect(peak).toBe(3);
    expect(done.sort()).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });
});

describe("longDate", () => {
  it("ngày hiện không nhập nhằng giữa ngày/tháng của các nước", () => {
    expect(longDate("2026-12-01")).toBe("1 tháng 12, 2026");
    expect(longDate("2027-01-12")).toBe("12 tháng 1, 2027");
    expect(longDate("")).toBe("");
  });
});

describe("withoutSiblingReplace", () => {
  it("bỏ đề xuất thay bản cũ khi 'bản cũ' là tài liệu cùng lần thả; giữ đề xuất thay bản cũ thật", () => {
    const a = reviewOf(doc({ document_id: "a", suggestion: { replaces_id: "real", replaces_title: "Bản thật" } }));
    const b = reviewOf(doc({ document_id: "b", suggestion: { replaces_id: "a", replaces_title: "A", effective_from: "2027-01-01" } }));
    const [ra, rb] = withoutSiblingReplace([a, b]);
    expect(ra.doc.suggestion?.replaces_id).toBe("real");
    expect(rb.doc.suggestion).toEqual({ effective_from: "2027-01-01" });
  });
});

describe("shelfInfo", () => {
  const opts = { shelves: [{ value: "common", label: "Chung", everyone: true, audience: "Cả công ty" }, { value: "management", label: "Ban giám đốc", everyone: false, audience: "Vai: Giám đốc" }], shelf: "common", kinds: [], contexts: [], can_manage: false };
  it("kệ không phải của cả công ty là kệ hạn chế, kèm ai đọc được", () => {
    expect(shelfInfo(opts, "common")).toEqual({ restricted: false, audience: "Cả công ty", label: "Chung" });
    expect(shelfInfo(opts, "management")).toEqual({ restricted: true, audience: "Vai: Giám đốc", label: "Ban giám đốc" });
  });
  it("chưa tải xong danh sách ⇒ dùng thông tin server gửi kèm tài liệu", () => {
    expect(shelfInfo(null, "management", { status: "added", filename: "a", shelf_everyone: false, shelf_audience: "Đội KD", shelf_name: "Kinh doanh" })).toEqual({ restricted: true, audience: "Đội KD", label: "Kinh doanh" });
  });
});
