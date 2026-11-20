// PID 1 của container web: SIGTERM ⇒ VẪN PHỤC VỤ 20 giây (Traefik hỏi Swarm lại mỗi ~15 giây mới biết bản mới — xem serve.py), rồi bật
// cờ rút (/api/health 503 ⇒ Traefik thôi gửi request mới), đợi thêm 35 giây cho lượt chat đang stream qua BFF chạy xong (lượt dài nhất
// đo được ~33 giây), rồi mới tắt server Next. Tổng 55 giây < stop_grace_period 75 giây.
import { spawn } from "node:child_process";
import { rmSync, writeFileSync } from "node:fs";

// Chế độ production (ADR 0194): đăng nhập dev bật ở bản production ⇒ KHÔNG lên (healthcheck hỏng, Swarm giữ bản cũ) — chốt bằng code, không
// dựa vào mẫu deploy/stack.json đã đặt đúng. devLoginAllowed() (web/src/lib/server/session.ts) cũng tắt khi BARISTA_ENV=production.
if (process.env.BARISTA_ENV === "production" && process.env.BARISTA_WEB_DEV_LOGIN === "1") {
  console.error("Chế độ production từ chối khởi động: BARISTA_WEB_DEV_LOGIN=1 (đăng nhập dev) phải tắt");
  process.exit(1);
}
const flag = process.env.BARISTA_DRAIN_FILE || "/tmp/barista-web-draining";
rmSync(flag, { force: true });
// kết nối rảnh từ Traefik giữ 95 giây (mặc định Node 5): Traefik giữ kết nối tới container 90 giây ⇒ Traefik luôn đóng trước (không 502 vì dùng lại
// kết nối server vừa đóng), lần bấm sau vài giây không phải mở kết nối mới (12/11/2026). Next standalone đọc KEEP_ALIVE_TIMEOUT (ms).
const child = spawn(process.execPath, ["server.js"], { stdio: "inherit", env: { KEEP_ALIVE_TIMEOUT: "95000", ...process.env } });
let draining = false;
const drain = () => {
  if (draining) return;
  draining = true;
  setTimeout(() => writeFileSync(flag, ""), 20_000);
  setTimeout(() => child.kill("SIGTERM"), 55_000);
};
process.on("SIGTERM", drain);
process.on("SIGINT", drain);
child.on("exit", (code) => process.exit(code ?? 0));
