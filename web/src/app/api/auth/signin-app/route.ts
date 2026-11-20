import { coreBase } from "@/lib/server/bff";
import { detail, json } from "@/lib/server/handler";

export const dynamic = "force-dynamic";

/** Tên ứng dụng first-party của một yêu cầu đăng nhập (0205) — trang đăng nhập (chưa có phiên) ghi "Đăng nhập <ứng dụng>" bằng tên thật, không bằng chữ trên URL. */
export async function GET(req: Request): Promise<Response> {
  const id = new URL(req.url).searchParams.get("req") ?? "";
  if (!/^[A-Za-z0-9_-]{16,128}$/.test(id)) return detail("Yêu cầu không hợp lệ", 404);
  try {
    const r = await fetch(`${coreBase()}/v1/oauth/signin/${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!r.ok) return detail("Không có yêu cầu đăng nhập này", 404);
    return json(await r.json());
  } catch {
    return detail("Không nối được máy chủ lõi", 502);
  }
}
