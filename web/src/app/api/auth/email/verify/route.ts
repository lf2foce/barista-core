import { detail, originOk } from "@/lib/server/handler";
import { loginThroughCore } from "@/lib/server/login";

export const dynamic = "force-dynamic";

/** 0205 §5c: email + mã 6 số ⇒ core kiểm và cấp phiên (như đăng nhập Google). */
export async function POST(request: Request): Promise<Response> {
  if (!(await originOk(request))) return detail("Yêu cầu từ trang khác bị từ chối", 403);
  const body = (await request.json().catch(() => ({}))) as { email?: unknown; code?: unknown };
  if (typeof body.email !== "string" || typeof body.code !== "string") return detail("Thiếu email hoặc mã", 422);
  return loginThroughCore("/v1/auth/email/verify", { email: body.email, code: body.code }, request);
}
