import { coreBase, demoHeaders } from "@/lib/server/bff";
import { detail, json, originOk, writeSession } from "@/lib/server/handler";
import { devLoginAllowed, SESSION_MAX_AGE_S } from "@/lib/server/session";
import type { SessionInfo } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Đăng nhập dev: kiểm {email, công ty} với core (qua manifest) rồi đặt cookie. Chỉ khi môi trường cho phép.
 *  Trả kèm chính manifest vừa đọc (cùng header danh tính mà /api/core gửi cho phiên dev ⇒ cùng nội dung) + phiên cho trình duyệt (không mã phiên):
 *  trang chủ dùng ngay, khỏi hỏi lại core một lượt (lib/api.ts primeDesk). */
export async function POST(request: Request): Promise<Response> {
  if (!devLoginAllowed()) return detail("Đăng nhập dev đang tắt", 404);
  if (!(await originOk(request))) return detail("Yêu cầu từ trang khác bị từ chối", 403);
  const body = (await request.json().catch(() => ({}))) as { email?: unknown; tenant_id?: unknown };
  if (typeof body.email !== "string" || typeof body.tenant_id !== "string") return detail("Thiếu email / công ty", 422);
  const check = await fetch(`${coreBase()}/v1/manifest`, {
    headers: { "x-dev-user": body.email, "x-tenant-id": body.tenant_id, ...demoHeaders() },
    cache: "no-store",
  }).catch(() => null);
  if (!check) return detail("Không nối được máy chủ lõi", 502);
  if (!check.ok) {
    const d = (await check.json().catch(() => ({}))) as { detail?: string };
    return detail(d.detail ?? "Đăng nhập dev bị từ chối", check.status === 401 || check.status === 403 ? check.status : 502);
  }
  const manifest: unknown = await check.json().catch(() => null);
  await writeSession(
    { v: 1, mode: "dev", email: body.email, tenant: body.tenant_id, tenants: [], exp: Math.floor(Date.now() / 1000) + SESSION_MAX_AGE_S },
    request,
  );
  const session: SessionInfo = { email: body.email, tenant_id: body.tenant_id, mode: "dev", tenants: [] };
  return json({ status: "ok", manifest, session });
}
