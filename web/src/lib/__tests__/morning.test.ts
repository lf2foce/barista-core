import { describe, expect, it } from "vitest";
import { morningOutcome } from "../morning";

const r = (status: string, error = "") => ({ status, output: null, error, approval_id: null, replayed: false });

describe("đề xuất buổi sáng (0074 A)", () => {
  it("chạy được / gửi duyệt / bỏ ⇒ thẻ xong", () => {
    expect(morningOutcome(r("ok"), "confirm")).toMatchObject({ done: true, status: "ok" });
    expect(morningOutcome(r("pending_approval"), "confirm")).toMatchObject({ done: true, message: "Đã gửi duyệt" });
    expect(morningOutcome(r("dismissed"), "dismiss")).toMatchObject({ done: true, message: "Đã bỏ đề xuất" });
  });
  it("căn cứ đổi / sai dữ liệu ⇒ giữ form, nói lý do của server", () => {
    expect(morningOutcome(r("conflict", "Căn cứ đã đổi"), "confirm")).toEqual({ status: "conflict", done: false, message: "Căn cứ đã đổi" });
  });
});
