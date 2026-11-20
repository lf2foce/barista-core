import { describe, expect, it } from "vitest";
import { parseStandalone, standaloneHref } from "@/lib/standalone";

describe("trang riêng ở tab mới", () => {
  it("địa chỉ ⇄ truy vấn + tham số", () => {
    const href = standaloneHref("mod.view", { document_id: "a-1", q: "bỏ" });
    expect(href).toBe("/open?q=mod.view&document_id=a-1");
    expect(parseStandalone(Object.fromEntries(new URLSearchParams(href.split("?")[1])))).toEqual({ query: "mod.view", input: { document_id: "a-1" } });
  });

  it("tên truy vấn lạ ⇒ không mở; khoá lạ / giá trị lặp / quá dài bị bỏ", () => {
    expect(parseStandalone({})).toBeNull();
    expect(parseStandalone({ q: "../v1/x" })).toBeNull();
    expect(parseStandalone({ q: ["a.b", "c.d"] })).toBeNull();
    expect(parseStandalone({ q: "mod.view", "Bad-Key": "x", id: ["1", "2"], long: "x".repeat(201), ok: "1" })).toEqual({ query: "mod.view", input: { ok: "1" } });
  });
});
