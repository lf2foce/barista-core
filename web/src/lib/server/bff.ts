import { timingSafeEqual } from "node:crypto";
import type { Session } from "./session";

// Phần thuần của BFF (kiểm thử được): chọn địa chỉ core, dựng header gửi đi, lọc header trả về.

export function coreBase(): string {
  return (process.env.BARISTA_CORE_URL || "http://127.0.0.1:8300").replace(/\/+$/, "");
}

/** Bản demo trên VPS: core chỉ nhận đăng nhập dev từ máy khác khi có X-Demo-Key (BARISTA_DEMO_KEY, chỉ phía server — không lộ ra trình duyệt). */
export function demoHeaders(): Record<string, string> {
  const key = process.env.BARISTA_DEMO_KEY;
  return key ? { "x-demo-key": key } : {};
}

/** Đường dẫn cho phép đi qua BFF chung: chỉ /v1/…; đăng nhập và đăng nhập dev có route riêng; không '..' hay đoạn rỗng. */
export function targetPath(segments: string[]): string | null {
  if (segments.length < 2 || segments[0] !== "v1") return null;
  if (segments.some((s) => s === "" || s === "." || s === ".." || s.includes("/") || s.includes("\\"))) return null;
  if (segments[1] === "auth" || segments[1] === "dev") return null;
  return "/" + segments.map((s) => encodeURIComponent(s)).join("/");
}

/** Đường cần ĐỊA CHỈ MẠNG của trình duyệt (chấm công kiểm theo IP văn phòng — core 0150 lát 4): BFF chuyển tiếp địa chỉ do proxy biên của bản cài
 *  ghi, CHỈ cho các đường này; đường khác không mang địa chỉ. */
export const CLIENT_IP_PATHS = new Set(["/v1/timekeeping/punch"]);

/** Khoá proxy biên (BARISTA_EDGE_SECRET, chỉ phía server): Traefik của stack gắn `X-Barista-Edge` vào mọi yêu cầu tới web (ghi đè giá trị trình duyệt
 *  tự gửi); BFF chỉ tin X-Forwarded-For khi khoá đúng, và tự gắn khoá khi gọi core (lõi chỉ tin X-Forwarded-For khi khoá đúng — barista/edge.py).
 *  Không có khoá (máy dev) ⇒ không chuyển địa chỉ nào; core thấy địa chỉ kết nối (máy local). */
export function edgeSecret(): string {
  return process.env.BARISTA_EDGE_SECRET ?? "";
}

function sameSecret(given: string | null, secret: string): boolean {
  if (!given || secret.length < 16) return false;
  const a = Buffer.from(given), b = Buffer.from(secret);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** Địa chỉ trình duyệt theo proxy biên: phần tử CUỐI của X-Forwarded-For — hop do proxy biên nối thêm (phần đầu trình duyệt tự gửi được) — CHỈ khi
 *  yêu cầu mang đúng khoá proxy biên; không phải địa chỉ IP ⇒ không gửi. */
export function clientIp(incoming: Headers, secret = edgeSecret()): string | null {
  if (!sameSecret(incoming.get("x-barista-edge"), secret)) return null;
  const hops = (incoming.get("x-forwarded-for") ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  const raw = hops[hops.length - 1] ?? "";
  return /^[0-9a-fA-F:.]{2,45}$/.test(raw) && (raw.includes(".") || raw.includes(":")) ? raw : null;
}

/** Header gửi core: danh tính (Bearer hoặc X-Dev-User) + công ty; chỉ chuyển các header nghiệp vụ cần thiết. */
export function upstreamHeaders(session: Session, incoming: Headers, target = ""): Headers {
  const h = new Headers();
  for (const name of ["content-type", "accept", "idempotency-key", "x-filename"]) {
    const v = incoming.get(name);
    if (v) h.set(name, v);
  }
  if (session.mode === "token" && session.token) h.set("authorization", `Bearer ${session.token}`);
  else {
    h.set("x-dev-user", session.email);
    for (const [k, v] of Object.entries(demoHeaders())) h.set(k, v);
  }
  if (session.tenant) h.set("x-tenant-id", session.tenant);
  const secret = edgeSecret();
  const ip = CLIENT_IP_PATHS.has(target) ? clientIp(incoming, secret) : null;
  if (ip) {
    h.set("x-forwarded-for", ip);
    h.set("x-barista-edge", secret);
  }
  return h;
}

/** Chặng đo của BFF, mili giây (vd `bff-session`, `bff-upstream`, `bff`) — nối sau `Server-Timing` của core. */
export type BffTiming = Record<string, number>;

/** Một tiêu đề `Server-Timing` cho trình duyệt (DevTools › Network › Timing): chặng của core (owner-wait, session, principal, app-wait, claims, handler, executor, json, app — backend
 *  `kernel/timing.py`) rồi chặng của BFF. Chỉ thời lượng; tên chặng lạ / giá trị không phải số bị bỏ. */
export function serverTiming(upstream: string | null, bff: BffTiming = {}): string {
  const own = Object.entries(bff)
    .filter(([k, v]) => /^[\w-]+$/.test(k) && Number.isFinite(v))
    .map(([k, v]) => `${k};dur=${v.toFixed(1)}`);
  return [upstream?.trim() ?? "", ...own].filter(Boolean).join(", ");
}

/** Header trả về trình duyệt: chỉ loại nội dung / tên file + số đo thời gian; không đệm, không cache (stream NDJSON / SSE chạy thẳng). */
export function downstreamHeaders(upstream: Headers, timing?: BffTiming): Headers {
  const h = new Headers();
  // nosniff: tệp người dùng gửi (trao đổi — 0115) giữ đúng kiểu server đã nhận diện, trình duyệt không đoán lại thành HTML
  // x-barista-error: mã lỗi máy đọc của core (vd not_member) — app quyết theo mã, không theo chữ
  for (const name of ["content-type", "content-disposition", "x-content-type-options", "x-barista-error"]) {
    const v = upstream.get(name);
    if (v) h.set(name, v);
  }
  const st = serverTiming(upstream.get("server-timing"), timing);
  if (st) h.set("server-timing", st);
  h.set("cache-control", "no-store, no-transform");
  h.set("x-accel-buffering", "no");
  return h;
}

/** Chặn gửi chéo trang: yêu cầu ghi mà có Origin khác máy chủ ⇒ từ chối (cookie SameSite=Lax đã chặn phần lớn). */
export function sameOrigin(method: string, origin: string | null, host: string | null): boolean {
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return true;
  if (!origin) return true;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

/** Link gửi khách (core 0064 — /v1/public/links/…): KHÔNG phiên, KHÔNG danh tính — quyền là chính mã link. Chỉ đúng 3 dạng; mã sai dạng ⇒ null
 *  (không gửi rác xuống core). */
const LINK_TOKEN = /^[A-Za-z0-9_-]{20,100}$/;
export function publicLinkPath(segments: string[], method: string): string | null {
  const [token, action, ...rest] = segments;
  if (!token || !LINK_TOKEN.test(token) || rest.length) return null;
  if (action === undefined) return method === "GET" ? `/v1/public/links/${token}` : null;
  if ((action === "answer" || action === "upload") && method === "POST") return `/v1/public/links/${token}/${action}`;
  // gọi trực tiếp qua link mời (core 0114 bổ sung 07/11): gửi tín hiệu / báo có mặt (POST), nghe tín hiệu SSE (GET)
  if ((action === "signal" || action === "presence") && method === "POST") return `/v1/public/links/${token}/${action}`;
  if (action === "stream" && method === "GET") return `/v1/public/links/${token}/stream`;
  return null;
}

/** Header gửi core cho link công khai: chỉ loại nội dung + tên file (không cookie, không danh tính, không khoá demo). */
export function publicHeaders(incoming: Headers): Headers {
  const h = new Headers();
  for (const name of ["content-type", "x-filename"]) {
    const v = incoming.get(name);
    if (v) h.set(name, v);
  }
  return h;
}
