// Kiểm bố cục trang báo cáo quản trị (0166) trong trình duyệt thật: mọi ô lưới có bề rộng đủ đọc, ô số không bị cắt, biểu đồ không co thành dải hẹp —
// ở nhiều cỡ cửa sổ, trong khung làm việc (màn ghim) và khi phóng to. Chạy khi sửa bố cục thẻ / khung màn:
//   BARISTA_WEB=http://localhost:3117 PERSONA="Ban giám đốc (thử)" node scripts/report-layout-check.mjs
// Cần core + web đang chạy, vai thấy menu "Báo cáo quản trị" (gói mgmt bật). Thoát mã 1 nếu có trang vỡ.
import { chromium } from "playwright";

const BASE = process.env.BARISTA_WEB || "http://localhost:3117";
const PERSONA = process.env.PERSONA || "Ban giám đốc (thử)";
const PAGES = ["Theo tháng", "Theo dự án", "Quỹ căn", "Kết quả kinh doanh", "Tổng quan tháng", "Doanh thu theo tháng", "Xếp hạng người bán", "Phí và thu tiền", "Hàng ôm"];
const VIEWPORTS = [[1860, 1000], [1440, 900], [1240, 900], [1100, 800], [820, 900], [420, 900]];
const MIN_CELL = 340;     // ô lưới CÓ biểu đồ hẹp hơn mức này = nhãn trục dính nhau (quy tắc: ba ô một hàng chỉ khi mỗi ô ≥ 360px, không thì hai, rồi một)
const MIN_CHART = 180;    // biểu đồ hẹp hơn mức này = dải hẹp

const browser = await chromium.launch();
const problems = [];
async function openPage(w, h) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.getByText(PERSONA).first().click();
  await page.waitForTimeout(2500);
  await page.getByText("Báo cáo quản trị").first().click().catch(() => {});
  await page.waitForTimeout(1200);
  return page;
}

async function measure(page) {
  return page.evaluate(({ MIN_CELL, MIN_CHART }) => {
    const out = [];
    for (const c of document.querySelectorAll(".grid-cell")) {
      const r = c.getBoundingClientRect();
      const sameRow = [...c.parentElement.children].filter((o) => Math.abs(o.getBoundingClientRect().top - r.top) < 4).length;
      if (c.querySelector("figure") && sameRow > 1 && r.width < MIN_CELL) out.push(`ô biểu đồ hẹp ${Math.round(r.width)}px (${sameRow} ô một hàng)`);   // một ô một hàng thì đã là bề rộng tối đa (điện thoại)
      const kid = c.firstElementChild;                       // ô cùng hàng cao bằng nhau: thẻ bên trong phải lấp đầy ô (không thấp hơn hàng)
      if (kid && r.height - kid.getBoundingClientRect().height > 6) out.push(`thẻ thấp hơn hàng ${Math.round(r.height - kid.getBoundingClientRect().height)}px`);
    }
    for (const f of document.querySelectorAll("figure")) if (f.getBoundingClientRect().width > 0 && f.getBoundingClientRect().width < MIN_CHART) out.push(`biểu đồ hẹp ${Math.round(f.getBoundingClientRect().width)}px`);
    for (const el of document.querySelectorAll(".tnum")) if (el.scrollWidth > el.clientWidth + 1) out.push(`số bị cắt: ${el.textContent}`);
    if (document.documentElement.scrollWidth > window.innerWidth + 1) out.push(`trang tràn ngang ${document.documentElement.scrollWidth}px`);
    return out;
  }, { MIN_CELL, MIN_CHART });
}

for (const [w, h] of VIEWPORTS) {
  let shared = w >= 980 ? await openPage(w, h) : null;   // từ 980px menu và khung làm việc cùng hiện ⇒ mở lần lượt trong một phiên; hẹp hơn ⇒ mỗi trang một phiên (menu và khung luân phiên)
  for (const title of PAGES) {
    for (const expanded of [false, true]) {
      const page = shared ?? (await openPage(w, h));
      try {
        await page.getByRole("button", { name: title, exact: true }).first().click({ timeout: 4000 });
        await page.waitForTimeout(4500);
        if (expanded) await page.getByRole("button", { name: /Phóng to|Expand/ }).first().click({ timeout: 2000 }).catch(() => {});
        const bad = await measure(page);
        if (bad.length) problems.push(`${w}x${h} · ${title}${expanded ? " (phóng to)" : ""}: ${[...new Set(bad)].slice(0, 4).join("; ")}`);
        if (expanded) await page.keyboard.press("Escape").catch(() => {});
      } catch (e) {
        problems.push(`${w}x${h} · ${title}: không mở được (${String(e).split("\n")[0].slice(0, 80)})`);
      }
      if (!shared) await page.close();
    }
  }
  if (shared) await shared.close();
}
await browser.close();
if (problems.length) {
  console.log(`VỠ BỐ CỤC (${problems.length}):\n- ` + problems.join("\n- "));
  process.exit(1);
}
console.log(`ổn: ${PAGES.length} trang × ${VIEWPORTS.length} cỡ cửa sổ × 2 chế độ`);
