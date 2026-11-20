// Mở MỌI menu / hộp thả xuống / hộp thoại của khung (headless) và đếm lỗi console — bắt lỗi kiểu base-ui
// "MenuGroupLabel phải nằm trong Menu.Group". Dùng: pnpm menus — cần dev server web (3100) + core (8300). Có lỗi ⇒ thoát mã 1.
import { chromium } from "playwright";

const BASE = process.env.BARISTA_WEB_URL || "http://localhost:3100";
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "vi-VN" });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`PAGEERROR ${e.message}`));
page.on("console", (m) => m.type() === "error" && errors.push(`CONSOLE ${m.text().slice(0, 300)}`));
let opened = 0;
const step = async (name, fn) => {
  try {
    await fn();
    opened++;
    console.log("ok  ", name);
  } catch (e) {
    errors.push(`STEP ${name}: ${e.message.split("\n")[0]}`);
    console.log("FAIL", name);
  }
  await page.keyboard.press("Escape").catch(() => null);
  await page.waitForTimeout(150);
};
const menuOpen = () => page.getByRole("menu").first().waitFor({ timeout: 4000 });
const rail = (name) => page.getByRole("navigation", { name: "Thanh bên trái" }).getByRole("button", { name: new RegExp(`^${name}`) });

await page.goto(`${BASE}/login`);
await page.click('[data-login="truonga@sanmau.demo"]');
await page.getByRole("heading", { name: "Khung làm việc" }).waitFor();
await page.waitForTimeout(800);

await step("tài khoản (đáy thanh bên trái)", async () => { await page.getByRole("button", { name: "Tài khoản" }).click(); await menuOpen(); });
await step("cuộc trò chuyện trước", async () => { await page.getByRole("button", { name: "Cuộc trò chuyện trước" }).click(); await menuOpen(); });
await step("tìm nhanh (thanh bên trái)", async () => { await rail("Tìm").click(); await page.getByTestId("palette").waitFor(); await page.getByPlaceholder(/Tìm khách/).fill("Lan"); await page.waitForTimeout(800); });
await step("tìm nhanh (⌘K)", async () => { await page.keyboard.press("Control+k"); await page.getByTestId("palette").waitFor(); });

await rail("Kinh doanh").click();
await page.getByRole("button", { name: /^Khách & lead/ }).first().click();
await page.waitForSelector("tbody tr");
await step("nút nhập (Nhập một / từ Excel)", async () => { await page.getByRole("button", { name: /Thêm cách nhập/ }).click(); await menuOpen(); });
await step("form Thêm khách / lead", async () => { await page.getByRole("button", { name: "Thêm khách / lead" }).first().click(); await page.getByRole("dialog").waitFor(); await page.waitForTimeout(500); });
await step("hành trình khách + @ trong khối trao đổi", async () => {
  await page.locator("tbody tr").first().dblclick();
  await page.getByRole("dialog").getByLabel(/Nhắc tên/).click();
  await menuOpen();
  await page.keyboard.press("Escape");
});

await rail("Việc & quy trình").click();
await page.getByRole("button", { name: /^Việc của tôi/ }).first().click();
await page.waitForSelector("article");
await step("chuyển cột trên bảng kanban", async () => { await page.locator("article").first().hover(); await page.getByRole("button", { name: /Chuyển thẻ/ }).first().click(); await menuOpen(); });

await rail("Tin nhắn").click();
await page.getByRole("heading", { name: "Tin nhắn" }).waitFor();
await step("tạo phòng (form có chọn nhiều)", async () => {
  await page.getByRole("button", { name: "Tạo phòng nhắn tin nhóm" }).click();
  await page.getByRole("dialog").getByText("Thành viên").first().waitFor();
  await page.waitForTimeout(600);
});
await step("phòng + @ trong cột", async () => {
  await page.getByRole("list", { name: "Danh sách tin nhắn" }).getByRole("button", { name: /Kinh doanh A/ }).first().click();
  await page.getByRole("log", { name: /Trao đổi: Kinh doanh A/ }).waitFor();
  await page.getByLabel(/Nhắc tên/).first().click();
  await menuOpen();
});
await step("ngăn Thành viên + Thêm người", async () => {
  await page.getByRole("button", { name: "Thành viên", exact: true }).click();
  await page.getByRole("complementary", { name: "Ngăn kiểm tra" }).getByRole("button", { name: "Thêm người" }).click();
  await page.getByRole("dialog").waitFor();
  await page.waitForTimeout(500);
});
await step("thông báo trong cột", async () => {
  await page.getByRole("list", { name: "Danh sách tin nhắn" }).getByRole("button", { name: /^Thông báo/ }).click();
  await page.getByRole("list", { name: "Thông báo" }).waitFor();
});
await step("ẩn / hiện thanh bên trái", async () => {
  await page.getByRole("button", { name: /Ẩn thanh bên trái/ }).click();
  await page.getByRole("button", { name: /Hiện thanh bên trái/ }).click();
});
await step("Alt+1…: chuyển mục", async () => {
  await page.keyboard.press("Alt+1");
  await page.getByLabel("Hỏi trợ lý").waitFor();
  await page.keyboard.press("Alt+3");
  await page.getByRole("heading", { name: "Kinh doanh", exact: true }).first().waitFor();
});

await browser.close();
console.log(`\n${opened} mục đã mở`);
if (errors.length) {
  console.log(`${errors.length} lỗi:`);
  for (const e of errors) console.log(" ", e);
  process.exit(1);
}
console.log("0 lỗi console");
