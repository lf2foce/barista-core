import { describe, expect, it } from "vitest";
import { decodePage } from "../decode";

// ADR 0251: trang giấy lập từ hồ sơ — ô bấm được (Thuộc ⇒ mở hồ sơ, ô thiếu ⇒ điền), tờ giấy chia mẩu theo lớp, nút mang căn cứ.
describe("tờ giấy (0251)", () => {
  const raw = {
    kind: "detail", title: "Phiếu", subtitle: "", links: [],
    actions: [{ command: "papers.issue", title: "Phát hành", style: "primary", preset: { paper_id: "p1" }, basis: "eyJhIjoiMSJ9" }],
    sections: [{ title: "", fields: [
      { label: "Thuộc", value: "Giao dịch C-1", format: "text", open: { query: "deals.case_file", input: { code: "C-1" }, title: "Giao dịch" } },
      { label: "Địa chỉ", value: "", format: "text", placeholder: "Chạm để điền", action: { command: "papers.set_field", title: "Điền", preset: { paper_id: "p1", key: "addr" } } },
      { label: "Lạ", value: "x", open: { input: {} }, action: { title: "thiếu lệnh" } },
    ] }],
    sheet: { title: "PHIẾU", number: "Số: (cấp khi phát hành)", draft_mark: "CHƯA PHÁT HÀNH", signers: ["BÊN A"], notes: [], legend: [{ layer: "record", label: "Số từ hồ sơ" }],
             actions: [{ command: "papers.set_field", title: "Điền", preset: { paper_id: "p1", key: "addr" } }],
             blocks: [{ type: "para", text: "Giá 5 tỷ, địa chỉ ……", runs: [{ t: "Giá " }, { t: "5 tỷ", k: "price", layer: "record" }, { t: ", địa chỉ " }, { t: "……", k: "addr", layer: "missing" }] },
                      { type: "table", text: "", columns: ["Đợt", "Tiền"], rows: [["1", "5"]], total: ["Tổng", "5"], align: ["l", "r"] }] },
  };

  it("giải mã ô bấm được, tờ giấy và căn cứ của nút", () => {
    const p = decodePage(raw);
    expect(p.kind).toBe("detail");
    if (p.kind !== "detail") return;
    const [belongs, gap, odd] = p.sections[0].fields;
    expect(belongs.open).toEqual({ query: "deals.case_file", input: { code: "C-1" }, title: "Giao dịch" });
    expect(gap.action?.preset).toEqual({ paper_id: "p1", key: "addr" });
    expect(gap.placeholder).toBe("Chạm để điền");
    expect(odd.open).toBeUndefined();
    expect(odd.action).toBeUndefined();
    expect(p.actions[0].basis).toBe("eyJhIjoiMSJ9");
    expect(p.sheet?.draftMark).toBe("CHƯA PHÁT HÀNH");
    expect(p.sheet?.blocks[0].runs?.map((r) => r.layer ?? "")).toEqual(["", "record", "", "missing"]);
    expect(p.sheet?.blocks[1].columns).toEqual(["Đợt", "Tiền"]);
    expect(p.sheet?.actions[0].preset.key).toBe("addr");
  });

  it("trang chi tiết cũ (không sheet) vẫn như trước", () => {
    const p = decodePage({ kind: "detail", title: "T", sections: [{ title: "A", fields: [{ label: "x", value: 1 }] }] });
    if (p.kind !== "detail") return;
    expect(p.sheet).toBeNull();
    expect(p.sections[0].fields[0]).toEqual({ label: "x", value: 1, format: "text" });
  });
});
