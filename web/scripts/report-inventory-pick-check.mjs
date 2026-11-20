// Lọc chéo trang "Quỹ căn" (kiểu Power BI): bấm một ô của treemap / một cột của biểu đồ thống kê / một lát của biểu đồ lý do khoá ⇒ bộ lọc Dự án / Lý do khoá đổi; bấm lại ⇒ bỏ.
//   BARISTA_WEB=http://localhost:3120 node scripts/report-inventory-pick-check.mjs   (core + web đang chạy, vai thấy Báo cáo quản trị)
import { chromium } from "playwright";
const BASE = process.env.BARISTA_WEB || "http://localhost:3117";
const PERSONA = process.env.PERSONA || "Ban giám đốc (thử)";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1860, height: 1050 } });
const fails = [];
await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByText(PERSONA).first().click();
await page.waitForTimeout(4000);
await page.getByText("Báo cáo quản trị").first().click();
await page.waitForTimeout(1500);
await page.getByRole("button", { name: "Quỹ căn", exact: true }).first().click();
await page.waitForTimeout(6000);
await page.getByRole("button", { name: /Phóng to/ }).first().click().catch(() => {});
await page.waitForTimeout(3000);
const sel = (i) => page.locator("aside select").nth(i).inputValue();
const fig = (title) => page.locator(`figure[aria-label^="${title}"]`);
const settle = () => page.waitForTimeout(4500);

async function check(name, i, click) {
  await click();
  await settle();
  const v = await sel(i);
  if (!v) { fails.push(`${name}: bấm không đặt bộ lọc`); return; }
  await click();
  await settle();
  if (await sel(i)) fails.push(`${name}: bấm lại không bỏ lọc`);
  console.log(`  ${name}: đặt = ${v}, bấm lại ⇒ bỏ`);
}
const mouseClick = async (loc) => {   // chuột thật: rê rồi bấm (Recharts cần biết cột đang hiện)
  const box = await loc.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForTimeout(500);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
};
await check("treemap (ô dự án)", 0, async () => { const g = fig("Phân bổ giá trị còn bán").locator("g[style*='cursor']").first(); await g.click({ force: true }); });
await check("cột thống kê quỹ căn", 0, async () => mouseClick(fig("Thống kê quỹ căn theo dự án").locator(".recharts-bar-rectangle").first()));
await check("cột còn bán + % đã bán (15 dự án)", 0, async () => mouseClick(fig("Số căn còn bán và % đã bán").locator(".recharts-bar-rectangle").first()));
await check("lát lý do khoá", 1, async () => { await fig("Căn đang khoá theo lý do").locator(".cursor-pointer").first().click(); });
await browser.close();
if (fails.length) { console.log("LỖI:\n- " + fails.join("\n- ")); process.exit(1); }
console.log("ổn: lọc chéo Quỹ căn (treemap, hai biểu đồ cột, biểu đồ lý do khoá)");
