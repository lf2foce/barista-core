import { clientIp, coreBase, edgeSecret } from "@/lib/server/bff";
import { detail, json, writeSession } from "@/lib/server/handler";
import { SESSION_MAX_AGE_S } from "@/lib/server/session";
import type { TenantChoice } from "@/lib/types";

/** Gửi một yêu cầu đăng nhập sang core (Google / mã email) ⇒ core trả phiên ⇒ BFF giữ mã phiên trong cookie httpOnly. Lỗi ⇒ chữ của core. */
export async function loginThroughCore(path: string, payload: unknown, request: Request): Promise<Response> {
  const headers = new Headers({ "content-type": "application/json", "user-agent": request.headers.get("user-agent") ?? "barista-web" });
  const secret = edgeSecret();
  const ip = clientIp(request.headers, secret);   // giới hạn lượt sai theo TỪNG trình duyệt, không gộp mọi người dùng web vào địa chỉ của BFF
  if (ip) {
    headers.set("x-forwarded-for", ip);
    headers.set("x-barista-edge", secret);
  }
  const r = await fetch(`${coreBase()}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    cache: "no-store",
  }).catch(() => null);
  if (!r) return detail("Không nối được máy chủ lõi", 502);
  const data = (await r.json().catch(() => ({}))) as { token?: string; email?: string; expires_at?: string; tenants?: TenantChoice[]; detail?: string };
  if (!r.ok || !data.token || !data.email) return detail(data.detail ?? "Đăng nhập không thành công", r.status >= 400 && r.status < 500 ? r.status : 502);
  const tenants = data.tenants ?? [];
  const expires = data.expires_at ? Date.parse(data.expires_at) / 1000 : NaN;
  const exp = Number.isFinite(expires) ? Math.min(expires, Date.now() / 1000 + SESSION_MAX_AGE_S) : Date.now() / 1000 + SESSION_MAX_AGE_S;
  await writeSession(
    { v: 1, mode: "token", email: data.email, token: data.token, tenant: tenants.length === 1 ? tenants[0].id : null, tenants, exp },
    request,
  );
  return json({ email: data.email, tenants, tenant_id: tenants.length === 1 ? tenants[0].id : null });
}
