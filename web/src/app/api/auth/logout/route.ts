import { coreBase } from "@/lib/server/bff";
import { clearSession, detail, json, originOk, readSession } from "@/lib/server/handler";

export const dynamic = "force-dynamic";

/** Thu hồi phiên ở core (nếu là phiên thật) rồi xoá cookie. */
export async function POST(request: Request): Promise<Response> {
  if (!(await originOk(request))) return detail("Yêu cầu từ trang khác bị từ chối", 403);
  const s = await readSession();
  if (s?.mode === "token" && s.token) {
    await fetch(`${coreBase()}/v1/auth/logout`, { method: "POST", headers: { authorization: `Bearer ${s.token}` }, cache: "no-store" }).catch(() => null);
  }
  await clearSession();
  return json({ status: "ok" });
}
