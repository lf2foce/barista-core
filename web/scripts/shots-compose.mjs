// Chụp màn "Soạn với AI" (thử nghiệm, dạng trò chuyện): ô trò chuyện mới → gõ yêu cầu → đang chạy → xong (→ nói tiếp nếu có SHOT_FOLLOW).
// Cần web dev + core đang chạy; core cần model (khoá thật, hoặc model giả tiêm vào compose.MODEL khi chạy thử).
// Dùng: BARISTA_WEB_URL=http://localhost:3100 node scripts/shots-compose.mjs [thư mục ra]
//   BARISTA_SHOT_USER (mặc định giamdoc@sanmau.demo) · SHOT_BRIEF · SHOT_FOLLOW="Rút gọn đoạn 2" · BARISTA_SHOT_DARK=1 · SHOT_W=1000
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const OUT = process.argv[2] || "./shots-compose";
const BASE = process.env.BARISTA_WEB_URL || "http://localhost:3100";
const EMAIL = process.env.BARISTA_SHOT_USER || "giamdoc@sanmau.demo";
const BRIEF = process.env.SHOT_BRIEF || "Báo cáo tình hình giao dịch tháng này cho giám đốc";
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const errors = [];
const w = Number(process.env.SHOT_W || 1440);
const ctx = await browser.newContext({ viewport: { width: w, height: 900 }, locale: "vi-VN", colorScheme: process.env.BARISTA_SHOT_DARK ? "dark" : "light" });
const page = await ctx.newPage();
page.on("pageerror", (e) => errors.push(`PAGEERROR ${e.message}`));
page.on("console", (m) => m.type() === "error" && errors.push(`CONSOLE ${m.text().slice(0, 300)}`));
const shot = async (name) => { await page.screenshot({ path: `${OUT}/${name}.png` }); console.log("shot", name); };
await page.goto(`${BASE}/login`);
await page.waitForSelector(`[data-login]`);
const emails = await page.$$eval("[data-login]", (els) => els.map((e) => e.getAttribute("data-login")));
const email = emails.includes(EMAIL) ? EMAIL : emails[0];
console.log("login", email);
await page.click(`[data-login="${email}"]`);
await page.getByRole("heading", { name: "Khung làm việc" }).waitFor();
await page.getByRole("navigation", { name: "Thanh bên trái" }).getByRole("button", { name: /^Soạn với AI/ }).click();
await page.waitForTimeout(600);
const scr = page.getByRole("button", { name: /^Soạn với AI/ });
if (await scr.count() > 1) await scr.nth(1).click().catch(() => {});
await page.getByRole("heading", { name: "Bạn cần soạn gì?" }).waitFor({ timeout: 15000 });
await page.waitForTimeout(500);
await shot("01-new-chat");
await page.getByRole("textbox", { name: "Cần soạn gì" }).fill(BRIEF);
await page.keyboard.press("Enter");
await page.waitForTimeout(700);
await shot("02-streaming");
await page.waitForFunction(() => !document.querySelector('[aria-label="Trò chuyện với AI"] [role=status]'), null, { timeout: 120000 });
await page.waitForTimeout(800);
await shot("03-after-first-turn");
if (process.env.SHOT_FOLLOW) {
  await page.getByRole("textbox", { name: "Muốn sửa gì" }).fill(process.env.SHOT_FOLLOW);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(500);
  await shot("04-followup-streaming");
  await page.waitForFunction(() => !document.querySelector('[aria-label="Trò chuyện với AI"] [role=status]'), null, { timeout: 120000 });
  await page.waitForTimeout(800);
  await shot("05-after-followup");
}
await browser.close();
if (errors.length) {
  console.log(errors.join("\n"));
  process.exit(1);
}
console.log("ok");
