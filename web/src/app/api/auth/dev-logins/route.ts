import { coreBase, demoHeaders } from "@/lib/server/bff";
import { devLoginAllowed } from "@/lib/server/session";
import { detail, json } from "@/lib/server/handler";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  if (!devLoginAllowed()) return detail("Đăng nhập dev đang tắt", 404);
  try {
    const r = await fetch(`${coreBase()}/v1/dev/logins`, { headers: demoHeaders(), cache: "no-store" });
    if (!r.ok) return detail("Core không bật đăng nhập dev", r.status === 404 ? 404 : 502);
    return json(await r.json());
  } catch {
    return detail("Không nối được máy chủ lõi", 502);
  }
}
