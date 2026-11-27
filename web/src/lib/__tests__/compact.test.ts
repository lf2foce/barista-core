import { describe, expect, it } from "vitest";
import { compactCount } from "@/lib/compact";

describe("chữ đếm của thẻ gọn", () => {
  it("chỉ nói còn bao nhiêu mục chưa hiện — không '3/14' cộng lẫn loại", () => {
    expect(compactCount({ shown: 3, total: 14 })).toBe("còn 11 mục");
    expect(compactCount({ shown: 5, total: 5 })).toBe("");
    expect(compactCount({ shown: 8, total: null })).toBe("8 dòng đầu");
    expect(compactCount({ shown: 0, total: 0 })).toBe("");
    expect(compactCount(undefined)).toBe("");
  });
});
