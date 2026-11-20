import { detail, originOk } from "@/lib/server/handler";
import { loginThroughCore } from "@/lib/server/login";

export const dynamic = "force-dynamic";

/** id_token Google (từ Google Identity Services) + nonce ⇒ core kiểm và cấp phiên ⇒ BFF giữ mã phiên trong cookie httpOnly. */
export async function POST(request: Request): Promise<Response> {
  if (!(await originOk(request))) return detail("Yêu cầu từ trang khác bị từ chối", 403);
  const body = (await request.json().catch(() => ({}))) as { id_token?: unknown; nonce?: unknown };
  if (typeof body.id_token !== "string") return detail("Thiếu id_token", 422);
  return loginThroughCore("/v1/auth/google", { id_token: body.id_token, nonce: typeof body.nonce === "string" ? body.nonce : "" }, request);
}
