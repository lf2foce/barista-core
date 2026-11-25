import { describe, expect, it } from "vitest";
import { decodeAction } from "../decode";

describe("nút nhận tệp (ADR 0262)", () => {
  it("giữ đuôi tệp server khai, bỏ giá trị lạ", () => {
    expect(decodeAction({ command: "purchase_invoices.upload", title: "Tải hoá đơn", upload: "pdf,xml,jpg" }).upload).toBe("pdf,xml,jpg");
    expect(decodeAction({ command: "x.y", upload: "PDF; rm -rf" }).upload).toBeUndefined();
    expect(decodeAction({ command: "x.y", upload: 3 }).upload).toBeUndefined();
    expect("upload" in decodeAction({ command: "x.y" })).toBe(false);
  });
});
