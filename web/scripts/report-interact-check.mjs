// Kiểm tương tác của trang báo cáo quản trị (0173): cột lọc bên trái DÍNH suốt chiều cuộn; bấm một cột biểu đồ ⇒ bộ lọc tương ứng đổi và
// số liệu hẹp lại; bấm lại ⇒ bỏ lọc. Chạy: BARISTA_WEB=http://localhost:3117 PERSONA="Ban giám đốc (thử)" node scripts/report-interact-check.mjs
import { chromium } from "playwright";
const BASE = process.env.BARISTA_WEB || "http://localhost:3117";
const PERSONA = process.env.PERSONA || "Ban giám đốc (thử)";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 760 } });
const fails = [];
await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByText(PERSONA).first().click();
await page.waitForTimeout(2500);
await page.getByText("Báo cáo quản trị").first().click();
await page.waitForTimeout(1200);
await page.getByRole("button", { name: "Theo tháng", exact: true }).first().click();
await page.waitForTimeout(10000);
// 1) dính khi cuộn
const topOf = () => page.evaluate(() => {
  const a = document.querySelector("aside[aria-label='Bộ lọc']");
  let sc = a?.parentElement;
  while (sc && getComputedStyle(sc).overflowY !== "auto" && getComputedStyle(sc).overflowY !== "scroll") sc = sc.parentElement;
  return a && sc ? { aside: Math.round(a.getBoundingClientRect().top), box: Math.round(sc.getBoundingClientRect().top), scrollable: sc.scrollHeight - sc.clientHeight } : null;
});
const before = await topOf();
for (const y of [300, 900, 1800, 4000]) {
  await page.evaluate((y) => { const a = document.querySelector("aside[aria-label='Bộ lọc']"); let sc = a?.parentElement; while (sc && !["auto", "scroll"].includes(getComputedStyle(sc).overflowY)) sc = sc.parentElement; sc?.scrollTo(0, y); }, y);
  await page.waitForTimeout(300);
  const t = await topOf();
  if (!t || Math.abs(t.aside - t.box) > 6) fails.push(`cột lọc không dính ở cuộn ${y}px: ${JSON.stringify(t)}`);
}
// 2) bấm cột biểu đồ ⇒ lọc chi nhánh
await page.evaluate(() => { const a = document.querySelector("aside[aria-label='Bộ lọc']"); let sc = a?.parentElement; while (sc && !["auto", "scroll"].includes(getComputedStyle(sc).overflowY)) sc = sc.parentElement; sc?.scrollTo(0, 0); });
const clickFirstBar = async () => {   // chuột thật: rê tới cột rồi bấm (Recharts cần ô số đang hiện để biết cột nào)
  const box = await page.locator(".recharts-bar-rectangle").first().boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForTimeout(500);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForTimeout(7000);
};
const branch = () => page.locator("aside select").nth(2).inputValue();
await clickFirstBar();
const sel = await branch();
if (!sel) fails.push("bấm cột chi nhánh không đặt bộ lọc Chi nhánh");
await clickFirstBar();
if (sel && (await branch())) fails.push("bấm lại cột đang chọn không bỏ lọc");
await browser.close();
if (fails.length) { console.log("LỖI:\n- " + fails.join("\n- ")); process.exit(1); }
console.log(`ổn: cột lọc dính (ban đầu ${JSON.stringify(before)}), bấm cột ⇒ lọc chi nhánh = ${sel}`);
