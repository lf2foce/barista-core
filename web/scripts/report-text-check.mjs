// Chữ thô trên trang báo cáo quản trị: mở MỌI mục menu "Báo cáo quản trị" và tìm trong chữ hiển thị dấu nhãn chưa đổi ([[khoá]]) hoặc mã kỹ thuật (snake_case) —
// tiêu đề menu / trang, nhãn biểu đồ, chú giải, bảng, ô lọc. Chạy: BARISTA_WEB=http://localhost:3120 node scripts/report-text-check.mjs
import { chromium } from "playwright";
const BASE = process.env.BARISTA_WEB || "http://localhost:3117";
const PERSONA = process.env.PERSONA || "Ban giám đốc (thử)";
const RAW = /(\[\[|\]\]|\b[a-z]{2,}_[a-z_]{2,}\b)/g;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1860, height: 1050 } });
await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByText(PERSONA).first().click();
await page.waitForTimeout(4000);
await page.getByText("Báo cáo quản trị").first().click();
await page.waitForTimeout(1500);
const bad = [];
const names = ["Theo tháng", "Theo dự án", "Quỹ căn", "Kết quả kinh doanh", "Tổng quan tháng", "Doanh thu theo tháng", "Xếp hạng người bán", "Phí và thu tiền", "Dòng tiền dự phóng",
  "Hàng ôm", "Hiệu quả quảng cáo", "Chiến dịch", "KPI đội", "Xếp hạng KPI"];
const text = async () => (await page.evaluate(() => document.body.innerText)) + "\n" + (await page.evaluate(() => [...document.querySelectorAll("select option")].map((o) => o.textContent).join("\n")));
for (const n of names) {
  const b = page.getByRole("button", { name: n, exact: true }).first();
  if (!(await b.count())) { bad.push(`${n}: không thấy mục menu`); continue; }
  await b.click();
  await page.waitForTimeout(5000);
  const hits = [...new Set(((await text()).match(RAW)) ?? [])];
  console.log(`${n}: ${hits.length ? "CHỮ THÔ " + hits.slice(0, 8).join(", ") : "sạch"}`);
  if (hits.length) bad.push(`${n}: ${hits.slice(0, 8).join(", ")}`);
}
await browser.close();
if (bad.length) { console.log("LỖI:\n- " + bad.join("\n- ")); process.exit(1); }
console.log("ổn: không thấy dấu nhãn / mã thô trên các trang báo cáo quản trị");
