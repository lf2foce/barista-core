import { coreBase, demoHeaders } from "@/lib/server/bff";
import { detail, json, originOk, readSession, writeSession } from "@/lib/server/handler";

export const dynamic = "force-dynamic";

/** Chọn / đổi công ty trong phiên đang có. Phiên Google: chỉ trong danh sách core đã trả; phiên dev: kiểm với core. */
export async function POST(request: Request): Promise<Response> {
  if (!(await originOk(request))) return detail("Yêu cầu từ trang khác bị từ chối", 403);
  const session = await readSession();
  if (!session) return detail("Chưa đăng nhập", 401);
  const body = (await request.json().catch(() => ({}))) as { tenant_id?: unknown };
  if (typeof body.tenant_id !== "string") return detail("Thiếu công ty", 422);
  if (session.mode === "token" && !session.tenants.some((t) => t.id === body.tenant_id)) return detail("Không thuộc công ty này", 403);
  if (session.mode === "dev") {
    const ok = await fetch(`${coreBase()}/v1/manifest`, { headers: { "x-dev-user": session.email, "x-tenant-id": body.tenant_id, ...demoHeaders() }, cache: "no-store" })
      .then((r) => r.ok)
      .catch(() => false);
    if (!ok) return detail("Không phải thành viên của công ty này", 403);
  }
  await writeSession({ ...session, tenant: body.tenant_id }, request);
  return json({ status: "ok" });
}
