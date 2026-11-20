import { existsSync } from "node:fs";

export const dynamic = "force-dynamic";

/** Cho Swarm / Traefik: bản đang chạy (mã commit) và 503 khi đang rút (deploy/serve-web.mjs nhận SIGTERM). Không gọi core —
 *  core có kiểm tra riêng; web vẫn trả trang đăng nhập / báo lỗi đẹp khi core chập chờn. */
export function GET(): Response {
  const release = process.env.BARISTA_RELEASE || "dev";
  const draining = existsSync(process.env.BARISTA_DRAIN_FILE || "/tmp/barista-web-draining");
  return Response.json({ status: draining ? "draining" : "ok", release }, { status: draining ? 503 : 200, headers: { "cache-control": "no-store" } });
}
