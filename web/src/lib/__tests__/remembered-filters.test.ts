import { describe, expect, it } from "vitest";
import { filterKey, recall, remember, type Store } from "@/lib/remembered-filters";
import type { FieldSpec } from "@/lib/types";

function mem(): Store & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v), removeItem: (k) => void data.delete(k) };
}
const f = (key: string, dflt: unknown = undefined) => ({ key, label: key, kind: "text", default: dflt }) as unknown as FieldSpec;

describe("nhớ bộ lọc của từng người (rà vai 18/11/2026)", () => {
  it("chỉ nhớ ô khác mặc định của server; về đúng mặc định thì quên", () => {
    const s = mem();
    const k = filterKey("t1", "u1", "deals.list");
    remember(k, [f("period", "year"), f("status", "")], { period: "all", status: "" }, s);
    expect(recall(k, [f("period", "year"), f("status", "")], s)).toEqual({ period: "all" });
    remember(k, [f("period", "year")], { period: "year" }, s);
    expect(s.data.has(k)).toBe(false);
  });

  it("ô màn đã bỏ không quay lại; kho hỏng / trống ⇒ như chưa nhớ", () => {
    const s = mem();
    const k = filterKey("t1", "u1", "q");
    s.setItem(k, JSON.stringify({ gone: "x", period: "all" }));
    expect(recall(k, [f("period", "year")], s)).toEqual({ period: "all" });
    s.setItem(k, "{không phải json");
    expect(recall(k, [f("period")], s)).toEqual({});
    expect(recall(k, [f("period")], null)).toEqual({});
  });

  it("khoá tách công ty × người × truy vấn", () => {
    expect(filterKey("t1", "u1", "q")).not.toBe(filterKey("t1", "u2", "q"));
    expect(filterKey("t1", "u1", "q")).not.toBe(filterKey("t2", "u1", "q"));
  });
});
