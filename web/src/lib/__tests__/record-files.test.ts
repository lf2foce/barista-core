import { describe, expect, it } from "vitest";
import { recordFileForm } from "@/lib/api";
import { decodeAttach, decodePage } from "@/lib/decode";
import { defaultDropChoice, dropChoices, toChatDocument, type OpenRecord } from "@/lib/documents";

// ADR 0249: tài liệu thả vào chat trợ lý KHÔNG tự vào kho tri thức; khối "Tệp" của hồ sơ là danh sách thường + nơi gắn tệp.

const record: OpenRecord = { subjectKind: "crm.lead", subjectId: "11111111-1111-1111-1111-111111111111", title: "Khách A", path: "/v1/record_files" };

describe("thả tài liệu vào chat trợ lý", () => {
  it("đang mở hồ sơ ⇒ chọn sẵn gắn vào hồ sơ; không thì chỉ dùng trong cuộc trò chuyện — kho tri thức không bao giờ là mặc định", () => {
    expect(defaultDropChoice(record)).toBe("record");
    expect(defaultDropChoice(null)).toBe("conversation");
    expect(dropChoices(null).map((c) => c.value)).toEqual(["conversation", "knowledge"]);
    expect(dropChoices(record).map((c) => c.value)).toEqual(["record", "conversation", "knowledge"]);
    expect(dropChoices(record)[0].label).toContain("Khách A");
  });

  it("tài liệu đi kèm câu hỏi là base64 thuần, quá 10 MB bị từ chối", async () => {
    const d = await toChatDocument(new File([new Uint8Array([37, 80, 68, 70])], "hd.pdf"));
    expect(d).toEqual({ filename: "hd.pdf", data: "JVBERg==", size: 4 });
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], "lon.pdf");
    await expect(toChatDocument(big)).rejects.toThrow("10 MB");
  });
});

describe("khối Tệp của hồ sơ", () => {
  it("giải mã attach trên khối danh sách; đường lạ (không phải /v1/) bị bỏ", () => {
    const attach = { subject_kind: "crm.lead", subject_id: "x1", subject_title: "Khách A", path: "/v1/record_files", file_max: 20971520, file_kinds: "PDF", labels: ["Hợp đồng", ""] };
    expect(decodeAttach(attach)).toEqual({ ...attach, labels: ["Hợp đồng"] });
    expect(decodeAttach({ ...attach, path: "https://evil.example/up" })).toBeNull();
    const page = decodePage({ kind: "card", title: "Khách A", blocks: [{ type: "list", title: "Tệp", items: [{ title: "hd.pdf" }], attach }] });
    const b = page.kind === "card" ? page.blocks[0] : null;
    expect(b && b.type === "list" ? b.attach?.subject_id : null).toBe("x1");
    const plain = decodePage({ kind: "card", title: "", blocks: [{ type: "list", title: "Lịch sử thay đổi", items: [] }] });
    expect(plain.kind === "card" && plain.blocks[0].type === "list" ? plain.blocks[0].attach : "x").toBeUndefined();
  });

  it("thân gắn tệp: trường chữ trước, tệp sau cùng; không gửi nhãn trống", () => {
    const f = recordFileForm(new File(["x"], "a.pdf"), { subject_kind: "crm.lead", subject_id: "x1", label: "" });
    expect([...f.keys()]).toEqual(["subject_kind", "subject_id", "file"]);
  });
});
