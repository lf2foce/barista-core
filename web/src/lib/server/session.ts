import type { TenantChoice } from "../types";

// Phiên của BFF: cookie httpOnly + SameSite=Lax, mã hoá AES-GCM. Mã phiên của core (Bearer) KHÔNG BAO GIỜ xuống JS trình duyệt.
//  - mode "token": đăng nhập Google thật — giữ mã phiên core cấp.
//  - mode "dev":   core bật BARISTA_DEV_AUTH — giữ {email, tenant}, BFF gửi X-Dev-User (core chỉ nhận từ máy local).

export const COOKIE = "barista_session";
export const SESSION_MAX_AGE_S = 7 * 24 * 3600;

export type Session = {
  v: 1;
  mode: "dev" | "token";
  email: string;
  token?: string;
  tenant: string | null;
  tenants: TenantChoice[];
  exp: number; // epoch giây
};

const enc = new TextEncoder();

function secret(): string {
  const s = process.env.BARISTA_SESSION_SECRET;
  if (s && s.length >= 16) return s;
  if (process.env.NODE_ENV === "production") throw new Error("Thiếu BARISTA_SESSION_SECRET (≥ 16 ký tự) cho môi trường chạy thật");
  return "barista-dev-only-secret-do-not-use-in-production";
}

function b64url(bytes: ArrayBuffer | Uint8Array): string {
  const u = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let s = "";
  for (const b of u) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(s: string): Uint8Array {
  const pad = s.length % 4 ? "=".repeat(4 - (s.length % 4)) : "";
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

// Khoá AES dẫn xuất MỘT lần mỗi tiến trình (theo đúng chuỗi bí mật): trước đây mỗi request /api/core/* băm + nhập khoá lại (12/11/2026).
let derived: { secret: string; key: Promise<CryptoKey> } | null = null;

async function aesKey(): Promise<CryptoKey> {
  const s = secret();
  if (derived?.secret !== s) {
    const key = crypto.subtle.digest("SHA-256", enc.encode(s)).then((d) => crypto.subtle.importKey("raw", d, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]));
    derived = { secret: s, key };
    key.catch(() => { if (derived?.key === key) derived = null; });   // lỗi (hiếm) ⇒ lần sau dẫn xuất lại, không giữ promise hỏng
  }
  return derived.key;
}

/** Mã hoá AES-GCM (vừa kín vừa chống sửa): ngay cả người dùng mở devtools cũng không đọc được mã phiên của core trong cookie. */
export async function seal(session: Session): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await aesKey(), enc.encode(JSON.stringify(session)));
  return `${b64url(iv)}.${b64url(ct)}`;
}

export async function unseal(cookie: string | undefined | null, now = Date.now() / 1000): Promise<Session | null> {
  if (!cookie) return null;
  const [iv, ct] = cookie.split(".");
  if (!iv || !ct) return null;
  try {
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64url(iv) as BufferSource }, await aesKey(), unb64url(ct) as BufferSource);
    const s = JSON.parse(new TextDecoder().decode(plain)) as Session;
    if (s.v !== 1 || typeof s.email !== "string" || (s.mode !== "dev" && s.mode !== "token") || !(s.exp > now)) return null;
    return { ...s, tenants: Array.isArray(s.tenants) ? s.tenants : [], tenant: s.tenant ?? null };
  } catch {
    return null;
  }
}

/** Cookie Secure khi đi qua HTTPS (hoặc sau proxy báo https); chạy http://localhost thì không (trình duyệt sẽ bỏ cookie Secure). */
export function isHttps(url: string, forwardedProto: string | null): boolean {
  return forwardedProto === "https" || url.startsWith("https:");
}

export function cookieOptions(secure: boolean, maxAge = SESSION_MAX_AGE_S) {
  return { httpOnly: true, sameSite: "lax" as const, secure, path: "/", maxAge };
}

/** Đăng nhập dev chỉ khi môi trường dev, hoặc người vận hành bật rõ ràng — không bao giờ ở bản cài production (BARISTA_ENV, ADR 0194). */
export function devLoginAllowed(): boolean {
  if (process.env.BARISTA_ENV === "production") return false;
  return process.env.NODE_ENV !== "production" || process.env.BARISTA_WEB_DEV_LOGIN === "1";
}
