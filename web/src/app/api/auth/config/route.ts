import { coreBase, demoHeaders } from "@/lib/server/bff";
import { devLoginAllowed } from "@/lib/server/session";
import { detail, json } from "@/lib/server/handler";

export const dynamic = "force-dynamic";

/** Cách đăng nhập: Google (client ID) và dev (chỉ khi core bật VÀ BFF cho phép). */
export async function GET(): Promise<Response> {
  try {
    const r = await fetch(`${coreBase()}/v1/auth/config`, { headers: demoHeaders(), cache: "no-store" });
    if (!r.ok) return detail("Máy chủ lõi không trả cấu hình đăng nhập", 502);
    const cfg = (await r.json()) as { google: { client_id: string; web_client_id?: string } | null; dev: boolean };
    // GIS cần client loại "Web"; core hiện chỉ công bố client của app (iOS) — cho phép ghi đè bằng env hoặc trường web_client_id (additive).
    const clientId = process.env.BARISTA_GOOGLE_WEB_CLIENT_ID || cfg.google?.web_client_id || cfg.google?.client_id;
    return json({ google: cfg.google && clientId ? { client_id: clientId } : null, dev: cfg.dev && devLoginAllowed() });
  } catch {
    return detail("Không nối được máy chủ lõi", 502);
  }
}
