import { describe, expect, it } from "vitest";
import { PRIMED_TTL_MS, primeDesk, takePrimedDesk } from "../session";

const manifest = { workspaces: [], tenant: { id: "t" } };
const session = { email: "a@x.demo", tenant_id: "t", mode: "dev", tenants: [] };

describe("manifest đăng nhập dev → trang chủ dùng một lần", () => {
  it("lấy được đúng một lần", () => {
    primeDesk(manifest, session, 1000);
    expect(takePrimedDesk(1500)).toEqual({ manifest, session });
    expect(takePrimedDesk(1600)).toBeNull();
  });
  it("quá hạn ⇒ không dùng (trang chủ hỏi core như thường)", () => {
    primeDesk(manifest, session, 1000);
    expect(takePrimedDesk(1000 + PRIMED_TTL_MS + 1)).toBeNull();
  });
  it("thiếu / sai dạng ⇒ không giữ", () => {
    primeDesk(null, session, 1000);
    expect(takePrimedDesk(1001)).toBeNull();
    primeDesk({ workspaces: "x" }, session, 1000);
    expect(takePrimedDesk(1001)).toBeNull();
    primeDesk(manifest, { tenant_id: "t" }, 1000);
    expect(takePrimedDesk(1001)).toBeNull();
  });
});
