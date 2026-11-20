import { describe, expect, it } from "vitest";
import { confirmAll, waitingProposals } from "../confirm-all";
import type { DecideOutcome } from "../decide";
import { addImages, base64Bytes, checkEncoded, fitWithin, imageFilesOf, IMAGE_BYTES, isImageType } from "../images";
import type { ChatCard } from "../types";

describe("ảnh: thu nhỏ + giới hạn", () => {
  it("cạnh dài ≤ 2000, giữ tỷ lệ, không phóng to", () => {
    expect(fitWithin(4000, 3000)).toEqual({ w: 2000, h: 1500, scaled: true });
    expect(fitWithin(1170, 2532)).toEqual({ w: 924, h: 2000, scaled: true });
    expect(fitWithin(800, 600)).toEqual({ w: 800, h: 600, scaled: false });
    expect(fitWithin(2000, 10)).toEqual({ w: 2000, h: 10, scaled: false });
    expect(fitWithin(30000, 1).h).toBe(1);
  });
  it("đếm byte thật của base64", () => {
    expect(base64Bytes(btoa("abc"))).toBe(3);
    expect(base64Bytes(btoa("abcd"))).toBe(4);
    expect(base64Bytes(btoa("abcde"))).toBe(5);
    expect(base64Bytes("")).toBe(0);
  });
  it("kiểm loại + 5 MB sau giải mã", () => {
    expect(checkEncoded({ media_type: "image/jpeg", data: btoa("x") })).toBeNull();
    expect(checkEncoded({ media_type: "image/gif", data: btoa("x") })).toBe("Chỉ nhận ảnh PNG, JPEG, WebP, HEIC");
    expect(checkEncoded({ media_type: "image/png", data: "" })).toBe("Ảnh không đọc được");
    const big = "A".repeat(Math.ceil(((IMAGE_BYTES + 3) * 4) / 3));
    expect(checkEncoded({ media_type: "image/png", data: big })).toBe("Mỗi ảnh tối đa 5 MB");
    expect(isImageType("IMAGE/HEIC")).toBe(true);
  });
  it("tối đa 3 ảnh, báo số bị bỏ", () => {
    expect(addImages([1, 2], [3, 4, 5])).toEqual({ images: [1, 2, 3], dropped: 2 });
    expect(addImages([1, 2, 3], [4])).toEqual({ images: [1, 2, 3], dropped: 1 });
    expect(addImages([], [1])).toEqual({ images: [1], dropped: 0 });
  });
  it("chỉ chặn dán khi clipboard có FILE ảnh (chữ / ô Excel dán như thường)", () => {
    const f = new File(["x"], "a.png", { type: "image/png" });
    const items = [
      { kind: "string", type: "text/plain", getAsFile: () => null },
      { kind: "file", type: "image/png", getAsFile: () => f },
      { kind: "file", type: "application/pdf", getAsFile: () => new File(["x"], "a.pdf") },
    ];
    expect(imageFilesOf(items)).toEqual([f]);
    expect(imageFilesOf([{ kind: "string", type: "text/html", getAsFile: () => null }])).toEqual([]);
  });
});

const card = (id: string, over: Partial<ChatCard> = {}): ChatCard => ({ id, kind: "proposal", title: id, command: "x", resumable: true, input: { v: id }, ...over });
const ok = (msg: string): DecideOutcome => ({ status: "ok", done: true, message: msg, replayed: false });

describe("xác nhận cả N đề xuất", () => {
  it("chỉ thẻ đề xuất của lượt đang dừng, chưa quyết", () => {
    const cards = [card("a"), card("b", { resumable: false }), card("c"), { ...card("d"), kind: "page" }, card("e")];
    expect(waitingProposals(cards, { c: "Đã xong" }).map((c) => c.id)).toEqual(["a", "e"]);
  });
  it("lần lượt từng thẻ, khoá mới mỗi thẻ, giá trị người đã sửa", async () => {
    const calls: string[] = [];
    let k = 0;
    const r = await confirmAll(
      [card("a"), card("b")],
      async (c, input, key) => {
        calls.push(`${c.id}:${String(input.v)}:${key}`);
        return ok(`Đã xong: ${c.id}`);
      },
      (c) => (c.id === "a" ? { v: "đã sửa" } : (c.input ?? {})),
      () => `k${++k}`,
    );
    expect(calls).toEqual(["a:đã sửa:k1", "b:b:k2"]);
    expect(r.failed).toBeNull();
    expect(r.confirmed.map((x) => x.outcome.message)).toEqual(["Đã xong: a", "Đã xong: b"]);
  });
  it("DỪNG ở lỗi đầu tiên — thẻ sau không bị gửi", async () => {
    const calls: string[] = [];
    const done: string[] = [];
    const r = await confirmAll(
      [card("a"), card("b"), card("c")],
      async (c) => {
        calls.push(c.id);
        return c.id === "b" ? { status: "conflict", done: false, message: "Trùng lịch", replayed: false } : ok("ok");
      },
      (c) => c.input ?? {},
      () => crypto.randomUUID(),
      (c) => done.push(c.id),
    );
    expect(calls).toEqual(["a", "b"]);
    expect(done).toEqual(["a"]);
    expect(r.failed?.card.id).toBe("b");
    expect(r.failed?.outcome.message).toBe("Trùng lịch");
  });
});
