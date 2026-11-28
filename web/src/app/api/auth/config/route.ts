import { coreBase, demoHeaders } from "@/lib/server/bff";
import { devLoginAllowed } from "@/lib/server/session";
import { detail, json } from "@/lib/server/handler";

export const dynamic = "force-dynamic";

/** Cách đăng nhập: Google (client ID) và dev (chỉ khi core bật VÀ BFF cho phép). Dev bật ⇒ kèm luôn danh sách vai (`dev_logins`, đọc SONG SONG
 *  với cấu hình) để trang đăng nhập vẽ danh sách sau MỘT lượt hỏi thay vì hai lượt nối nhau. */
export async function GET(): Promise<Response> {
  try {
    const logins = devLoginAllowed()
      ? fetch(`${coreBase()}/v1/dev/logins`, { headers: demoHeaders(), cache: "no-store" }).then((x) => (x.ok ? (x.json() as Promise<unknown>) : null)).catch(() => null)
      : Promise.resolve(null);
    const r = await fetch(`${coreBase()}/v1/auth/config`, { headers: demoHeaders(), cache: "no-store" });
    if (!r.ok) return detail("Máy chủ lõi không trả cấu hình đăng nhập", 502);
    const cfg = (await r.json()) as { google: { client_id: string; web_client_id?: string } | null; dev: boolean; email?: boolean };
    // GIS cần client loại "Web"; core hiện chỉ công bố client của app (iOS) — cho phép ghi đè bằng env hoặc trường web_client_id (additive).
    const clientId = process.env.BARISTA_GOOGLE_WEB_CLIENT_ID || cfg.google?.web_client_id || cfg.google?.client_id;
    const dev = cfg.dev && devLoginAllowed();
    const list = dev ? await logins : null;
    // email: đăng nhập bằng mã qua email (0205 §5c) — bản production chỉ email (ADR 0268) thì đây là cửa duy nhất, phải chuyển nguyên
    return json({ google: cfg.google && clientId ? { client_id: clientId } : null, dev, email: cfg.email === true, ...(Array.isArray(list) ? { dev_logins: list } : {}) });
  } catch {
    return detail("Không nối được máy chủ lõi", 502);
  }
}
