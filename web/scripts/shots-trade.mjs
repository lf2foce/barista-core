// Chụp màn hình gói tài trợ thương mại (0072) — Playwright headless, không đụng Chrome của người dùng.
// Dùng: BARISTA_SHOT_EMAIL=ha@<miền>.nganhangmau.demo pnpm shots:trade [thư mục ra]
//   cần web dev trỏ core bản ngân hàng (vd BARISTA_CORE_URL=http://127.0.0.1:8302 … next dev -p 3102 ⇒ BARISTA_WEB_URL=http://localhost:3102)
//   và một bộ chứng từ đã kiểm (scripts/demo_trade_finance.py --keep ở backend tạo sẵn).
// Chụp: danh sách bộ chứng từ → hồ sơ → báo cáo (khối finding) → bấm một bằng chứng ⇒ hộp xem PDF. Lỗi console / trang / HTTP ⇒ thoát mã 1.
// Chromium headless KHÔNG vẽ PDF trong iframe ⇒ ảnh 04 khung PDF trống là bình thường; xem thật bằng trình duyệt.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const OUT = process.argv[2] || "./shots-trade";
const BASE = process.env.BARISTA_WEB_URL || "http://localhost:3102";
const EMAIL = process.env.BARISTA_SHOT_EMAIL || "ha@nganhangmau.demo";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const errors = [];
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "vi-VN", colorScheme: process.env.BARISTA_SHOT_DARK ? "dark" : "light" });
const page = await ctx.newPage();
page.on("pageerror", (e) => errors.push(`PAGEERROR ${e.message}`));
// lỗi tải tài nguyên ghi theo URL ở dưới (dòng console "Failed to load resource" chỉ có mã lỗi ⇒ bỏ để khỏi đếm hai lần)
page.on("console", (m) => m.type() === "error" && !m.text().startsWith("Failed to load resource") && errors.push(`CONSOLE ${m.text().slice(0, 300)}`));
// talk.inbox 404 = công ty không bật gói Trao đổi (ngân hàng mẫu) — web cố ý thử rồi bỏ qua (desk-context refreshTalk), không phải lỗi
page.on("response", (r) => {
  if (r.status() < 400 || (r.status() === 404 && r.url().endsWith("/v1/queries/talk.inbox"))) return;
  errors.push(`HTTP ${r.status()} ${r.request().method()} ${r.url().replace(BASE, "").slice(0, 160)}`);
});
const shot = async (name) => {
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log("shot", name);
};

await page.goto(`${BASE}/login`);
await page.click(`[data-login="${EMAIL}"]`);
await page.getByRole("heading", { name: "Khung làm việc" }).waitFor();
await page.getByRole("navigation", { name: "Thanh bên trái" }).getByRole("button", { name: /^Tài trợ thương mại/ }).click();
await page.getByRole("button", { name: /^Bộ chứng từ/ }).first().click();
await page.waitForSelector("tbody tr");
await page.waitForTimeout(800);
await shot("01-presentations");

await page.locator("tbody tr").first().dblclick();
const report = page.getByRole("button", { name: /^Báo cáo v\d+/ }).first();
await report.waitFor();
await page.waitForTimeout(600);
await shot("02-presentation");
await report.click();
const evidence = page.getByTitle("Mở chứng từ gốc đúng trang, đối chiếu câu nguyên văn").first();
await evidence.waitFor({ timeout: 15000 });
await page.waitForTimeout(800);
await shot("03-report-findings");

await evidence.click();
await page.locator("iframe").last().waitFor();
await page.waitForTimeout(3000);
await shot("04-evidence-pdf");

await browser.close();
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
