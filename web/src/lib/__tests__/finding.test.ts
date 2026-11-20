import { describe, expect, it } from "vitest";
import { decodeBlock } from "../decode";
import { decodeShelves, documentTitle, isDocumentFile, splitDrop, uploadOutcome, uploadedPrompt, decodeTypeHint, withoutDocumentIds } from "../documents";
import { documentFileUrl, searchTerm } from "../links";

describe("khối finding (0072)", () => {
  it("giải mã đủ trường, bằng chứng thiếu / sai kiểu có mặc định", () => {
    const b = decodeBlock({
      type: "finding", tone: "bad", title: "R17 · Số tiền bảo hiểm", badge: "Bảo hiểm", text: "Thiếu USD 9,900.00", facts: ["Căn cứ: 28(f)(ii)"],
      evidence: [
        { label: "Bảo hiểm · Số tiền", quote: "Amount insured USD 495,000.00", value: "USD 495000", page: 1, document_id: "d1", scan: true, filename: "bh.pdf", note: "" },
        { label: "Phiếu đóng gói · Số L/C", quote: "", page: "1", document_id: null },
      ],
      actions: [{ command: "review.decide_finding", title: "Giữ" }],
    });
    expect(b.type).toBe("finding");
    if (b.type !== "finding") return;
    expect(b.evidence[0]).toEqual({ label: "Bảo hiểm · Số tiền", quote: "Amount insured USD 495,000.00", value: "USD 495000", page: 1, document_id: "d1", scan: true, filename: "bh.pdf", note: "" });
    expect(b.evidence[1]).toEqual({ label: "Phiếu đóng gói · Số L/C", quote: "", value: null, page: null, document_id: null, scan: false, filename: "", note: "" });
    expect(b.actions[0].command).toBe("review.decide_finding");
  });
  it("khối lạ vẫn là unknown (hợp đồng chỉ thêm)", () => {
    expect(decodeBlock({ type: "finding-v2" }).type).toBe("unknown");
  });
});

describe("mở bằng chứng trong trình xem PDF", () => {
  it("tìm cụm có chữ số dài nhất, không có thì 4 chữ đầu", () => {
    expect(searchTerm("TOTAL: USD 459,000.00")).toBe("459,000.00");
    expect(searchTerm(":20:  CREDIT NUMBER             MKB-LC-26-0417")).toBe("MKB-LC-26-0417");
    expect(searchTerm("CLEAN ON BOARD")).toBe("CLEAN ON BOARD");
  });
  it("URL file gốc qua BFF: trang + câu tìm, mã tài liệu được mã hoá", () => {
    expect(documentFileUrl("d 1", 2, "TOTAL: USD 459,000.00")).toBe("/api/core/v1/kb/documents/d%201/file#page=2&search=459%2C000.00");
    expect(documentFileUrl("d1", null)).toBe("/api/core/v1/kb/documents/d1/file");
  });
});

describe("thả chứng từ vào ô chat", () => {
  it("PDF / Word vào kho, còn lại đi đường ảnh; câu mời liệt kê tên", () => {
    expect(isDocumentFile("Hoa Don.PDF") && isDocumentFile("x.docx") && !isDocumentFile("anh.png")).toBe(true);
    const { documents, others } = splitDrop([{ name: "a.pdf" }, { name: "b.png" }, { name: "c.docx" }]);
    expect(documents.map((f) => f.name)).toEqual(["a.pdf", "c.docx"]);
    expect(others.map((f) => f.name)).toEqual(["b.png"]);
    expect(documentTitle("XT1 — Hoá đơn.pdf")).toBe("XT1 — Hoá đơn");
    expect(uploadedPrompt([{ title: "A", documentId: "d1" }, { title: "B", documentId: "d2" }]))
      .toBe("Tôi vừa tải lên 2 chứng từ: “A” (mã tài liệu d1), “B” (mã tài liệu d2). ");
    expect(uploadedPrompt([])).toBe("");
    // gợi ý loại: chữ do server soạn; không áp dụng ⇒ không chèn gì
    const hints = {
      d1: decodeTypeHint({ applicable: true, chip: "Hoá đơn thương mại", tone: "accent", prompt: "có vẻ là Hoá đơn thương mại" }),
      d2: decodeTypeHint({ applicable: false, chip: "", tone: "", prompt: "" }),
    };
    expect(uploadedPrompt([{ title: "A", documentId: "d1" }], hints)).toContain("· có vẻ là Hoá đơn thương mại");
    expect(uploadedPrompt([{ title: "B", documentId: "d2" }], hints)).toBe("Tôi vừa tải lên 1 chứng từ: “B” (mã tài liệu d2). ");
    expect(decodeTypeHint(null)).toEqual({ applicable: false, chip: "", tone: "", prompt: "" });
  });
  it("bong bóng chat ẩn mã tài liệu", () => {
    expect(withoutDocumentIds("“A” (mã tài liệu 9bb5b5e8-6e1f-442c-807e-516584c36ef5) · có vẻ là Hoá đơn")).toBe("“A” · có vẻ là Hoá đơn");
  });
  it("chờ duyệt / lỗi KHÔNG tính là đã lưu (review 04/11)", () => {
    expect(uploadOutcome({ status: "ok", output: { document_id: "d1" } })).toEqual({ kind: "saved", documentId: "d1" });
    expect(uploadOutcome({ status: "pending_approval", output: { approval_id: "a1" } })).toEqual({ kind: "pending" });
    expect(uploadOutcome({ status: "ok", output: {} })).toEqual({ kind: "failed" });
    expect(uploadOutcome({ status: "denied", output: null })).toEqual({ kind: "failed" });
  });
  it("kệ tải lên: giữ thứ tự server, bỏ dòng hỏng, không có kệ mặc định", () => {
    expect(decodeShelves([{ value: "kt", label: "Kế toán", audience: "Vai kế toán", everyone: false }, { label: "x" }, null]))
      .toEqual([{ value: "kt", label: "Kế toán", audience: "Vai kế toán", everyone: false }]);
    expect(decodeShelves("x")).toEqual([]);
  });
});
