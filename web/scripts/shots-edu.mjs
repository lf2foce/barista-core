// Soát màn giáo viên của gói Đào tạo ở bề ngang điện thoại (0116 lát 2): bàn việc → buổi chưa xác nhận → thẻ buổi (điểm danh một chạm, xác nhận).
// Dùng: pnpm shots:edu [thư mục ra] — cần dev server web + core đang chạy với dữ liệu mẫu `python -m barista.seed --reset --profile edu`.
//   BARISTA_WEB_URL (mặc định http://localhost:3100)
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const OUT = process.argv[2] || "./shots-edu";
const BASE = process.env.BARISTA_WEB_URL || "http://localhost:3100";
const STEP = process.env.BARISTA_SHOT_STEP || "all";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const errors = [];
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: "vi-VN", colorScheme: "light" });
const page = await ctx.newPage();
page.on("pageerror", (e) => errors.push(`PAGEERROR ${e.message}`));
page.on("console", (m) => m.type() === "error" && errors.push(`CONSOLE ${m.text().slice(0, 300)}`));
const shot = async (name) => {
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  console.log("shot", name, wide > 0 ? `TRÀN NGANG ${wide}px` : "");
};

await page.goto(`${BASE}/login`);
await page.click('[data-login="thayminh@anhngumau.demo"]');
await page.waitForSelector("[role=status]", { state: "detached", timeout: 60000 }).catch(() => {});
await page.waitForTimeout(3000);
await shot("01-home");
if (STEP !== "home") {
  await page.getByText("đã dạy chưa xác nhận").first().click();
  await page.waitForTimeout(1500);
  await shot("02-unconfirmed");
  await page.locator("tbody tr").first().dblclick();
  await page.waitForTimeout(1500);
  await shot("03-session-card");
  const all = page.getByRole("button", { name: "Cả lớp có mặt" });
  if (await all.count()) await all.first().click();   // chạy lại trên cùng dữ liệu: đã điểm danh từ lần trước
  await page.waitForTimeout(1500);
  await shot("04-all-present");
  await page.getByRole("button", { name: "Xác nhận buổi", exact: true }).first().click();
  await page.waitForTimeout(1500);
  await shot("05-confirmed");
}
await browser.close();
if (errors.length) {
  console.log(errors.join("\n"));
  process.exit(1);
}
