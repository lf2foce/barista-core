import { describe, expect, it, vi } from "vitest";
import { ApiError, failure, sessionGone } from "../api";
import { downstreamHeaders } from "../server/bff";
import { leaveSession } from "../session";

describe("phiên hỏng ⇒ đăng nhập lại (theo MÃ, không theo chữ)", () => {
  it("403 mang mã not_member ⇒ phiên hỏng; 403 khác (vd không có quyền) thì không", async () => {
    const gone = await failure(new Response(JSON.stringify({ detail: "Không phải thành viên đang hoạt động của công ty này" }),
      { status: 403, headers: { "x-barista-error": "not_member" } }), "lỗi");
    expect(gone.code).toBe("not_member");
    expect(gone.message).toContain("thành viên");
    expect(sessionGone(gone)).toBe(true);
    // chữ có "thành viên" nhưng KHÔNG có mã ⇒ không đăng xuất (trước đây so chữ nên nhầm)
    const denied = await failure(new Response(JSON.stringify({ detail: "Chỉ thành viên dự án mới xem được" }), { status: 403 }), "lỗi");
    expect(sessionGone(denied)).toBe(false);
    expect(sessionGone(new ApiError("hết hạn", 401))).toBe(true);
  });

  it("BFF chuyển mã lỗi của core xuống trình duyệt", () => {
    expect(downstreamHeaders(new Headers({ "x-barista-error": "not_member" })).get("x-barista-error")).toBe("not_member");
  });

  it("chỉ về trang đăng nhập khi xoá phiên THÀNH CÔNG — xoá lỗi thì ở lại (không vòng lặp)", async () => {
    const go = vi.fn();
    expect(await leaveSession(async () => ({ status: "ok" }), go)).toBe(true);
    expect(go).toHaveBeenCalledWith("/login");
    const stay = vi.fn();
    expect(await leaveSession(async () => { throw new Error("mạng"); }, stay)).toBe(false);
    expect(stay).not.toHaveBeenCalled();
  });
});
