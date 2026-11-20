// Chụp màn hình để soát giao diện (Playwright, headless — không đụng Chrome của người dùng).
// Dùng: pnpm shots [thư mục ra] — cần dev server web (3100) + core (8300) đang chạy.
//   BARISTA_SHOT_CHAT=0      bỏ bước hỏi trợ lý (cần khoá model ở core)
//   BARISTA_SHOT_DARK=1      chụp giao diện tối
// Ghi lỗi console / lỗi trang; có lỗi ⇒ thoát mã 1 (để bắt lỗi kiểu DropdownMenuLabel nằm ngoài Group).
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const OUT = process.argv[2] || "./shots";
const BASE = process.env.BARISTA_WEB_URL || "http://localhost:3100";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const errors = [];

async function session(email, name) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "vi-VN", colorScheme: process.env.BARISTA_SHOT_DARK ? "dark" : "light" });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`[${name}] PAGEERROR ${e.message}`));
  page.on("console", (m) => m.type() === "error" && errors.push(`[${name}] CONSOLE ${m.text().slice(0, 300)}`));
  await page.goto(`${BASE}/login`);
  await page.waitForSelector(`[data-login="${email}"]`);
  return { ctx, page };
}
const shot = async (page, name) => {
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log("shot", name);
};
const rail = (page, name) => page.getByRole("navigation", { name: "Thanh bên trái" }).getByRole("button", { name: new RegExp(`^${name}`) });

// ── trưởng nhóm: đăng nhập, trang chủ, bảng, hành trình khách, kanban, tin nhắn, phòng, phân hệ, ẩn thanh ──
{
  const { ctx, page } = await session("truonga@sanmau.demo", "truonga");
  await shot(page, "01-login");
  await page.click('[data-login="truonga@sanmau.demo"]');
  await page.getByRole("heading", { name: "Khung làm việc" }).waitFor();
  await page.waitForTimeout(1500);
  await shot(page, "02-home-assistant");

  // phân hệ Kinh doanh → Khách & lead
  await rail(page, "Kinh doanh").click();
  await page.waitForTimeout(300);
  await page.getByRole("button", { name: /^Khách & lead/ }).first().click();
  await page.waitForSelector("tbody tr");
  await page.waitForTimeout(800);
  await shot(page, "03-workspace-column-table");

  await page.locator("tbody tr").first().dblclick();
  await page.getByRole("dialog").getByText("Trao đổi").first().waitFor();
  await page.waitForTimeout(800);
  await shot(page, "04-journey-thread");
  await page.keyboard.press("Escape");

  await rail(page, "Việc & quy trình").click();
  await page.getByRole("button", { name: /^Việc của tôi/ }).first().click();
  await page.waitForSelector("[role=listitem][aria-label*='thẻ']");
  await page.waitForTimeout(800);
  await shot(page, "05-kanban");

  // Tin nhắn: danh sách → phòng "Kinh doanh A"
  await rail(page, "Tin nhắn").click();
  await page.getByRole("heading", { name: "Tin nhắn" }).waitFor();
  await page.waitForTimeout(1000);
  await shot(page, "06-messages-list");
  await page.getByRole("list", { name: "Danh sách tin nhắn" }).getByRole("button", { name: /^KD Kinh doanh A|Kinh doanh A/ }).first().click();
  await page.getByRole("log", { name: /Trao đổi: Kinh doanh A/ }).waitFor();
  await page.waitForTimeout(1500);
  await shot(page, "07-room-thread");
  // ngăn kiểm tra: thành viên phòng (thẻ talk.room) bên cạnh chat
  await page.getByRole("button", { name: "Thành viên", exact: true }).click();
  await page.getByRole("complementary", { name: "Ngăn kiểm tra" }).waitFor();
  await page.waitForTimeout(800);
  await shot(page, "07b-room-inspector");
  // trao đổi trên hồ sơ: ngăn Hồ sơ ⇒ khối Trao đổi trên thẻ thu lại một dòng
  await page.getByRole("list", { name: "Danh sách tin nhắn" }).getByRole("button", { name: /Lương Thùy Dung/ }).first().click();
  await page.getByRole("log").waitFor();
  await page.getByRole("button", { name: "Hồ sơ", exact: true }).click();
  await page.getByText("Trao đổi về hồ sơ này đang mở bên cạnh").waitFor({ timeout: 10000 });
  await page.waitForTimeout(800);
  await shot(page, "07c-record-inspector");
  // thông báo bên phải
  await page.getByRole("list", { name: "Danh sách tin nhắn" }).getByRole("button", { name: /^Thông báo/ }).click();
  await page.waitForTimeout(600);
  await shot(page, "08-notices-detail");
  // hẹp: chỉ danh sách; chọn ⇒ cuộc trò chuyện cả bề ngang + nút quay lại
  await page.setViewportSize({ width: 820, height: 860 });
  await page.getByRole("button", { name: "Về danh sách tin nhắn" }).click();
  await page.waitForTimeout(300);
  await shot(page, "08b-narrow-list");
  await page.getByRole("list", { name: "Danh sách tin nhắn" }).getByRole("button", { name: /Kinh doanh A/ }).first().click();
  await page.getByRole("log").waitFor();
  await page.waitForTimeout(800);
  await shot(page, "08c-narrow-thread");
  await page.getByRole("button", { name: "Về danh sách tin nhắn" }).click();
  await page.setViewportSize({ width: 1440, height: 900 });

  // ẩn thanh bên trái (phím tắt)
  await page.keyboard.press("Control+Shift+S");
  await page.waitForTimeout(400);
  await shot(page, "09-rail-hidden");
  await page.keyboard.press("Control+Shift+S");

  if (process.env.BARISTA_SHOT_CHAT !== "0") {
    await page.keyboard.press("Alt+1");
    await page.getByLabel("Hỏi trợ lý").fill(process.env.BARISTA_SHOT_Q || "Đội tôi hôm nay có việc gì đến hạn?");
    await page.keyboard.press("Enter");
    await page.waitForTimeout(25000);
    await shot(page, "10-chat");
  }
  await ctx.close();
}

// ── giám đốc: biểu đồ của exec.overview ──
{
  const { ctx, page } = await session("giamdoc@sanmau.demo", "giamdoc");
  await page.click('[data-login="giamdoc@sanmau.demo"]');
  await page.getByRole("heading", { name: "Khung làm việc" }).waitFor();
  await rail(page, "Kế toán").click();
  await page.getByRole("button", { name: /^Tổng quan điều hành/ }).first().click();
  await page.waitForSelector("figure", { timeout: 15000 });
  await page.waitForTimeout(1500);
  await shot(page, "11-exec-charts");
  // rê chuột lên cột đầu có số ⇒ ô số
  const bar = page.locator("figure .recharts-bar-rectangle path").last();
  if (await bar.count()) {
    await bar.hover();
    await page.waitForTimeout(400);
    await shot(page, "12-exec-chart-tooltip");
  }
  await page.locator("[data-slot=card-frame] .overflow-y-auto, section[aria-label] > div.overflow-y-auto").first().evaluate((el) => el.scrollBy(0, 700)).catch(() => null);
  await page.waitForTimeout(600);
  await shot(page, "13-exec-charts-more");
  await ctx.close();
}

await browser.close();
if (errors.length) {
  console.log(`\n${errors.length} lỗi console / trang:`);
  for (const e of errors) console.log(" ", e);
  process.exit(1);
}
console.log("0 lỗi console");
