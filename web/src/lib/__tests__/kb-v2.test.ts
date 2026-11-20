import { describe, expect, it } from "vitest";
import { decodePage } from "../decode";
import { resultView, savedWarning } from "../form";
import { isInlinePdf, linkUrl } from "../links";
import { pipeTable } from "../markdown";

describe("bản gốc tài liệu (0052)", () => {
  it("giải mã links.page (số dương), bỏ page hỏng", () => {
    const d = decodePage({ kind: "detail", title: "QĐ", sections: [], links: [{ title: "Mở bản gốc (trang 3)", path: "/v1/kb/documents/X/file", filename: "QĐ 5276.pdf", page: 3 }, { title: "x", path: "/p", page: "3" }] });
    expect(d.kind === "detail" && d.links).toEqual([
      { title: "Mở bản gốc (trang 3)", path: "/v1/kb/documents/X/file", filename: "QĐ 5276.pdf", page: 3 },
      { title: "x", path: "/p", filename: undefined },
    ]);
  });
  it("PDF ⇒ xem ngay đúng trang qua BFF; file khác ⇒ tải về", () => {
    expect(isInlinePdf({ filename: "QĐ 5276.PDF" })).toBe(true);
    expect(isInlinePdf({ filename: "Ban tu van.docx" })).toBe(false);
    expect(linkUrl({ path: "/v1/kb/documents/X/file", page: 3 })).toBe("/api/core/v1/kb/documents/X/file#page=3");
    expect(linkUrl({ path: "/v1/papers/P/docx" })).toBe("/api/core/v1/papers/P/docx");
  });
});

describe("kết quả lệnh", () => {
  it("cảnh báo tài liệu gần giống sau khi lưu", () => {
    expect(savedWarning({ document_id: "n", warning: "Có tài liệu đang hiệu lực tên gần giống", similar: [{ id: "o", title: "QĐ cũ", ratio: 0.82 }, { title: "thiếu id" }] })).toEqual({
      warning: "Có tài liệu đang hiệu lực tên gần giống",
      similar: [{ id: "o", title: "QĐ cũ", ratio: 0.82 }],
    });
    expect(savedWarning({ document_id: "n" })).toBeNull();
    expect(savedWarning(null)).toBeNull();
  });
  it("thẻ kết quả: shows của form nếu có, không thì paper_id ⇒ papers.view", () => {
    expect(resultView(null, { paper_id: "P1", status: "draft" })).toEqual({ query: "papers.view", input: { paper_id: "P1" } });
    expect(resultView({ shows: { query: "work.view", key: "item_id" } }, { item_id: 7 })).toEqual({ query: "work.view", input: { item_id: "7" } });
    expect(resultView({ shows: ["papers.view", "paper_id"] }, { paper_id: "P2" })).toEqual({ query: "papers.view", input: { paper_id: "P2" } });
    expect(resultView(null, { document_id: "D" })).toBeNull();
    expect(resultView(null, [1])).toBeNull();
  });
});

describe("bảng trong đoạn chữ (lịch thanh toán của bản tư vấn căn)", () => {
  it("mỗi dòng 'a | b | c' ⇒ hàng; chữ thường ⇒ null", () => {
    const t = "Đợt | Thời điểm | Tỷ lệ | Số tiền (đ)\nĐặt cọc | khi đặt cọc | 5% | 178.500.000\nTổng cộng | 100% | 3.570.000.000";
    expect(pipeTable(t)).toEqual([
      ["Đợt", "Thời điểm", "Tỷ lệ", "Số tiền (đ)"],
      ["Đặt cọc", "khi đặt cọc", "5%", "178.500.000"],
      ["Tổng cộng", "", "100%", "3.570.000.000"],
    ]);
    expect(pipeTable("Cam kết thuê lại 2 năm.")).toBeNull();
    expect(pipeTable("a | b")).toBeNull();
    expect(pipeTable("a | b\nchữ thường")).toBeNull();
  });
});
