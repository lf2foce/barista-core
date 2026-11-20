// Soát thẻ thông tin của gói Đào tạo (ADR 0164): tổng quan chuỗi (giám đốc), "Sắp hết buổi", "Nguy cơ rời lớp", hồ sơ người học, thẻ lớp (giáo vụ),
// bề ngang máy tính và điện thoại. Dùng: pnpm shots:edu-cards [thư mục ra] — cần web + core chạy với `python -m barista.seed --reset --profile edu-demo`.
//   BARISTA_WEB_URL (mặc định http://localhost:3100)
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const OUT = process.argv[2] || "./shots-edu-cards";
const BASE = process.env.BARISTA_WEB_URL || "http://localhost:3100";
const DOMAIN = "sangtaonho.demo";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const errors = [];

async function login(who, viewport) {
  const ctx = await browser.newContext({ viewport, isMobile: viewport.width < 500, hasTouch: viewport.width < 500, locale: "vi-VN", colorScheme: "light" });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`[${who}] PAGEERROR ${e.message}`));
  page.on("console", (m) => m.type() === "error" && errors.push(`[${who}] CONSOLE ${m.text().slice(0, 300)}`));
  await page.goto(`${BASE}/login`);
  await page.click(`[data-login="${who}@${DOMAIN}"]`);
  await page.waitForTimeout(3500);
  return { ctx, page };
}
const shot = async (page, name) => {
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  console.log("shot", name, wide > 0 ? `TRÀN NGANG ${wide}px` : "");
};
const open = async (page, screen) => {
  const rail = page.getByRole("navigation", { name: "Thanh bên trái" }).getByRole("button", { name: /^Đào tạo/ });
  if (await rail.count()) await rail.first().click();
  await page.waitForTimeout(400);
  await page.getByRole("button", { name: new RegExp(`^${screen}`) }).first().click();
  await page.waitForTimeout(1500);
};

{
  const { ctx, page } = await login("giamdoc", { width: 1440, height: 900 });
  await open(page, "Tổng quan chuỗi");
  await shot(page, "01-tong-quan-chuoi");
  await ctx.close();
}
{
  const { ctx, page } = await login("gv1", { width: 1440, height: 900 });
  await open(page, "Sắp hết buổi");
  await shot(page, "02-sap-het-buoi");
  await open(page, "Nguy cơ rời lớp");
  await shot(page, "03-nguy-co-roi-lop");
  await page.locator("tbody tr").first().dblclick();
  await page.waitForTimeout(1500);
  await shot(page, "04-ho-so-nguoi-hoc");
  await page.keyboard.press("Escape");
  await open(page, "Lớp");
  await page.locator("tbody tr").first().dblclick();
  await page.waitForTimeout(1500);
  await shot(page, "05-the-lop");
  await ctx.close();
}
{
  const { ctx, page } = await login("gv1", { width: 390, height: 844 });
  await open(page, "Nguy cơ rời lớp");
  await shot(page, "06-dien-thoai-nguy-co");
  await page.locator("tbody tr").first().dblclick();
  await page.waitForTimeout(1500);
  await shot(page, "07-dien-thoai-ho-so");
  await ctx.close();
}
await browser.close();
if (errors.length) {
  console.log(errors.join("\n"));
  process.exit(1);
}
