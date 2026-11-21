import { describe, expect, it } from "vitest";
import { decodeBlock } from "../decode";

describe("khối ảnh + chia cột (ui.b_image / ui.b_split, api 1.73)", () => {
  it("giải mã đệ quy hai cột, giữ ảnh link ký, bỏ khối lạ", () => {
    const b = decodeBlock({
      type: "split", ratio: "2:3",
      left: [{ type: "image", src: "https://api.example.vn/v1/files/signed?key=t%2Fa&signature=x", caption: "Ảnh phiếu" }],
      right: [{ type: "list", title: "Phiếu 1", items: [{ title: "Cát đen — 1 xe", badge: "93%", tone: "green" }] }, { type: "khong-co" }],
    });
    expect(b.type).toBe("split");
    if (b.type !== "split") return;
    expect(b.ratio).toBe("2:3");
    expect(b.left[0]).toMatchObject({ type: "image", caption: "Ảnh phiếu" });
    expect(b.right.map((x) => x.type)).toEqual(["list"]);
  });

  it("ảnh link lạ (javascript:, data:) ⇒ không vẽ, tỉ lệ sai ⇒ 1:1", () => {
    for (const src of ["javascript:alert(1)", "data:image/png;base64,AAAA"]) {
      const b = decodeBlock({ type: "image", src, note: "Chưa có ảnh" });
      expect(b).toMatchObject({ type: "image", src: "", note: "Chưa có ảnh" });
    }
    expect(decodeBlock({ type: "split", ratio: "10:1", left: [], right: [] })).toMatchObject({ type: "split", ratio: "1:1" });
  });
});
