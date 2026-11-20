import { json, readSession } from "@/lib/server/handler";
import type { SessionInfo } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Phiên hiện tại cho trình duyệt: KHÔNG kèm mã phiên. */
export async function GET(): Promise<Response> {
  const s = await readSession();
  if (!s) return json(null); // chưa đăng nhập: 200 + null (trang đăng nhập hỏi mà không sinh lỗi 401 trong console)
  const info: SessionInfo = { email: s.email, tenant_id: s.tenant, mode: s.mode, tenants: s.tenants };
  return json(info);
}
