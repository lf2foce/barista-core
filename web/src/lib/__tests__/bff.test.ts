import { describe, expect, it } from "vitest";
import { clientIp, downstreamHeaders, publicHeaders, publicLinkPath, sameOrigin, serverTiming, targetPath, upstreamHeaders } from "../server/bff";
import { seal, unseal, type Session } from "../server/session";
import { groupLogins, safeNext } from "../login";
import { parseInline, parseMarkdown } from "../markdown";
import { foldVietnamese } from "../search";

const base: Session = { v: 1, mode: "token", email: "a@b.vn", token: "SECRET-TOKEN", tenant: "T1", tenants: [], exp: Date.now() / 1000 + 3600 };

describe("BFF: header gửi core", () => {
  const incoming = new Headers({ "content-type": "application/json", "idempotency-key": "k-1", cookie: "barista_session=xyz", authorization: "Bearer fake", "x-dev-user": "hacker@x", "x-tenant-id": "tenant-fake", "x-forwarded-for": "1.2.3.4" });
  it("phiên thật: Bearer của core + công ty từ cookie, giữ Idempotency-Key", () => {
    const h = upstreamHeaders(base, incoming);
    expect(h.get("authorization")).toBe("Bearer SECRET-TOKEN");
    expect(h.get("x-tenant-id")).toBe("T1");
    expect(h.get("idempotency-key")).toBe("k-1");
    expect(h.get("content-type")).toBe("application/json");
    expect(h.get("x-dev-user")).toBeNull();
  });
  it("trình duyệt không thể tự gắn danh tính: cookie / authorization / x-dev-user / x-tenant-id đầu vào bị bỏ", () => {
    const h = upstreamHeaders({ ...base, mode: "dev", token: undefined }, incoming);
    expect(h.get("x-dev-user")).toBe("a@b.vn");
    expect(h.get("authorization")).toBeNull();
    expect(h.get("cookie")).toBeNull();
    expect(h.get("x-tenant-id")).toBe("T1");
    expect(h.get("x-forwarded-for")).toBeNull();
  });
  it("địa chỉ trình duyệt: chỉ đường chấm công, chỉ khi proxy biên gắn đúng khoá, lấy hop CUỐI, gắn khoá khi gọi core", () => {
    const old = process.env.BARISTA_EDGE_SECRET;
    process.env.BARISTA_EDGE_SECRET = "khoa-bien-thu-0123456789";
    try {
      const edge = (xff: string, key = "khoa-bien-thu-0123456789") => new Headers({ "x-forwarded-for": xff, "x-barista-edge": key });
      const h = upstreamHeaders(base, edge("203.0.113.9, 198.51.100.7"), "/v1/timekeeping/punch");
      expect(h.get("x-forwarded-for")).toBe("198.51.100.7");                     // trình duyệt tự gửi IP văn phòng phía trước — bỏ
      expect(h.get("x-barista-edge")).toBe("khoa-bien-thu-0123456789");
      expect(upstreamHeaders(base, edge("198.51.100.7"), "/v1/commands/timekeeping.punch").get("x-forwarded-for")).toBeNull();
      expect(upstreamHeaders(base, edge("198.51.100.7", "sai-khoa-0123456789012"), "/v1/timekeeping/punch").get("x-forwarded-for")).toBeNull();
      expect(upstreamHeaders(base, new Headers({ "x-forwarded-for": "198.51.100.7" }), "/v1/timekeeping/punch").get("x-forwarded-for")).toBeNull();
      expect(upstreamHeaders(base, edge("1.2.3.4, evil"), "/v1/timekeeping/punch").get("x-forwarded-for")).toBeNull();
      expect(clientIp(edge("2001:db8::1"))).toBe("2001:db8::1");
    } finally {
      if (old === undefined) delete process.env.BARISTA_EDGE_SECRET;
      else process.env.BARISTA_EDGE_SECRET = old;
    }
    expect(clientIp(new Headers({ "x-forwarded-for": "198.51.100.7", "x-barista-edge": "" }))).toBeNull();   // máy dev: không khoá ⇒ không tin
  });
  it("phản hồi: chỉ loại nội dung + tên file, không đệm, không cache", () => {
    const h = downstreamHeaders(new Headers({ "content-type": "application/x-ndjson", "content-disposition": "attachment", "set-cookie": "x=1", server: "uvicorn" }));
    expect(h.get("content-type")).toBe("application/x-ndjson");
    expect(h.get("set-cookie")).toBeNull();
    expect(h.get("x-accel-buffering")).toBe("no");
    expect(h.get("cache-control")).toContain("no-store");
  });
  it("Server-Timing: chặng của core đi tiếp, chặng của BFF nối sau; không có số đo nào ⇒ không có tiêu đề", () => {
    const h = downstreamHeaders(new Headers({ "server-timing": "principal;dur=2.0, executor;dur=4.1, app;dur=7.0" }), { "bff-session": 0.21, bff: 8.04 });
    expect(h.get("server-timing")).toBe("principal;dur=2.0, executor;dur=4.1, app;dur=7.0, bff-session;dur=0.2, bff;dur=8.0");
    expect(downstreamHeaders(new Headers()).get("server-timing")).toBeNull();
    expect(serverTiming(null, { bff: 1, "x;y": 2, bad: Number.NaN })).toBe("bff;dur=1.0");   // tên lạ / số hỏng bị bỏ
  });
  it("tệp trong trao đổi: giữ nosniff để trình duyệt không đoán lại kiểu", () => {
    const h = downstreamHeaders(new Headers({ "content-type": "image/png", "x-content-type-options": "nosniff" }));
    expect(h.get("x-content-type-options")).toBe("nosniff");
  });
});

describe("BFF: đường dẫn + nguồn gốc", () => {
  it("chỉ /v1/…, không auth / dev / '..'", () => {
    expect(targetPath(["v1", "queries", "crm.leads"])).toBe("/v1/queries/crm.leads");
    expect(targetPath(["v1", "queries", "crm.leads", "xlsx"])).toBe("/v1/queries/crm.leads/xlsx");
    expect(targetPath(["health"])).toBeNull();
    expect(targetPath(["v1", "auth", "google"])).toBeNull();
    expect(targetPath(["v1", "dev", "logins"])).toBeNull();
    expect(targetPath(["v1", "..", "health"])).toBeNull();
    expect(targetPath(["v1", "a/b"])).toBeNull();
    expect(targetPath(["v1", "queries", ""])).toBeNull();
  });
  it("yêu cầu ghi từ trang khác bị chặn", () => {
    expect(sameOrigin("POST", "http://localhost:3100", "localhost:3100")).toBe(true);
    expect(sameOrigin("POST", "https://evil.example", "localhost:3100")).toBe(false);
    expect(sameOrigin("POST", null, "localhost:3100")).toBe(true);
    expect(sameOrigin("GET", "https://evil.example", "localhost:3100")).toBe(true);
  });
});

describe("phiên cookie mã hoá", () => {
  it("mở lại được; không lộ mã phiên; sửa / giả mạo / hết hạn ⇒ từ chối", async () => {
    const c = await seal(base);
    expect((await unseal(c))?.token).toBe("SECRET-TOKEN");
    expect(c).not.toContain("SECRET");
    expect(atob(c.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")).includes("SECRET")).toBe(false);
    expect(await unseal(c.replace(/.$/, (x) => (x === "A" ? "B" : "A")))).toBeNull();
    const forged = btoa(JSON.stringify({ ...base, email: "admin@b.vn" })).replace(/=+$/, "");
    expect(await unseal(`${forged}.${forged}`)).toBeNull();
    expect(await unseal(await seal({ ...base, exp: 1 }))).toBeNull();
    expect(await unseal(undefined)).toBeNull();
    expect(await seal(base)).not.toBe(c); // iv ngẫu nhiên mỗi lần
  });
});

describe("đăng nhập + markdown + tìm", () => {
  it("nhóm vai giữ thứ tự xuất hiện", () => {
    const g = groupLogins([{ group: "A", email: "1" }, { group: "B", email: "2" }, { group: "A", email: "3" }, { email: "4" }] as never);
    expect(g.map(([n, r]) => [n, r.length])).toEqual([["A", 2], ["B", 1], ["Khác", 1]]);
  });
  it("next chỉ nội bộ", () => {
    expect(safeNext("/x?y=1")).toBe("/x?y=1");
    expect(safeNext("//evil.com")).toBe("/");
    expect(safeNext("https://evil.com")).toBe("/");
    expect(safeNext(null)).toBe("/");
  });
  it("markdown: tiêu đề, gạch lồng nhau, bảng; nhấn mạnh không nghiêng", () => {
    const b = parseMarkdown("## T\n- a\n  - b\n1. c\n| x | y |\n|---|---|\n| 1 | 2 |");
    expect(b.map((x) => x.kind)).toEqual(["heading", "bullet", "bullet", "bullet", "table"]);
    expect(b[2]).toMatchObject({ kind: "bullet", level: 1, marker: "◦" });
    expect(b[3]).toMatchObject({ marker: "1." });
    expect(parseInline("**đậm** và *nhấn* `mã` [l](https://x.vn)").map((p) => p.kind)).toEqual(["bold", "text", "text", "text", "code", "text", "link"]);
    expect(parseInline("<script>alert(1)</script>")).toEqual([{ kind: "text", text: "<script>alert(1)</script>" }]);
  });
  it("bỏ dấu tiếng Việt khi tìm màn", () => {
    expect(foldVietnamese("Việc hôm nay Đội")).toBe("viec hom nay doi");
  });
});

describe("BFF: link gửi khách (không phiên)", () => {
  const tok = "A".repeat(43);
  it("chỉ cho đúng 3 dạng, mã đúng định dạng", () => {
    expect(publicLinkPath([tok], "GET")).toBe(`/v1/public/links/${tok}`);
    expect(publicLinkPath([tok, "answer"], "POST")).toBe(`/v1/public/links/${tok}/answer`);
    expect(publicLinkPath([tok, "upload"], "POST")).toBe(`/v1/public/links/${tok}/upload`);
    expect(publicLinkPath([tok], "POST")).toBeNull();
    expect(publicLinkPath([tok, "answer"], "GET")).toBeNull();
    expect(publicLinkPath([tok, "delete"], "POST")).toBeNull();
    expect(publicLinkPath([tok, "answer", "x"], "POST")).toBeNull();
    expect(publicLinkPath(["../v1/commands"], "GET")).toBeNull();
    expect(publicLinkPath(["ngan"], "GET")).toBeNull();
  });
  it("không chuyển cookie / danh tính xuống core", () => {
    const h = publicHeaders(new Headers({ cookie: "barista_session=x", authorization: "Bearer y", "x-dev-user": "a@b", "content-type": "application/json",
                                          "x-filename": "sao%20ke.pdf" }));
    expect([...h.keys()].sort()).toEqual(["content-type", "x-filename"]);
  });
});
