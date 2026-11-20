import { clientIp, coreBase, edgeSecret } from "@/lib/server/bff";
import { detail, json, originOk } from "@/lib/server/handler";

export const dynamic = "force-dynamic";

/** 0205 §5c: xin mã đăng nhập qua email. Core trả lời như nhau dù email có được mời hay không; giới hạn theo email + địa chỉ trình duyệt (theo khoá
 *  proxy biên — như các đường không đăng nhập khác, không tin X-Forwarded-For trình duyệt tự gửi). */
export async function POST(request: Request): Promise<Response> {
  if (!(await originOk(request))) return detail("Yêu cầu từ trang khác bị từ chối", 403);
  const body = (await request.json().catch(() => ({}))) as { email?: unknown };
  if (typeof body.email !== "string" || !body.email.includes("@")) return detail("Nhập email của bạn", 422);
  const headers = new Headers({ "content-type": "application/json" });
  const secret = edgeSecret();
  const ip = clientIp(request.headers, secret);
  if (ip) {
    headers.set("x-forwarded-for", ip);
    headers.set("x-barista-edge", secret);
  }
  const r = await fetch(`${coreBase()}/v1/auth/email/start`, { method: "POST", headers, body: JSON.stringify({ email: body.email }), cache: "no-store" })
    .catch(() => null);
  if (!r) return detail("Không nối được máy chủ lõi", 502);
  const data = (await r.json().catch(() => ({}))) as { detail?: string };
  return r.ok ? json({ status: "sent" }) : detail(data.detail ?? "Chưa gửi được mã", r.status);
}
