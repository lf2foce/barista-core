// Đi trọn mạch demo L/C trên web (Playwright headless) và chụp từng cảnh. Cần: core `scripts/demo_lc.py serve` (8361) + web dev trỏ core đó (3161).
// Dùng: pnpm shots:lc [thư mục ra] — BARISTA_SHOT_DARK=1 chụp nền tối; BARISTA_LC_SCENE=review chỉ xem lại các màn của trạng thái đã có (không gọi model).
// Chromium headless không vẽ PDF trong iframe ⇒ ảnh hộp bằng chứng khung PDF có thể trống; xem thật bằng trình duyệt / app Mac.
import { chromium } from "playwright";
import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const OUT = process.argv[2] || "./shots-lc";
const BASE = process.env.BARISTA_WEB_URL || "http://localhost:3161";
// hồ sơ mẫu ở gói tài trợ thương mại (repo barista-packs cạnh repo lõi — 0155); BARISTA_LC_PDF để chỉ chỗ khác
const PDF = process.env.BARISTA_LC_PDF || resolve(process.cwd(), "../../barista-packs/trade_finance/demo/pdf");
const FILES = ["TNSC-2411-1", "TNSC-2411-2", "TNSC-2411-3", "TNSC-2411-4", "TNSC-2411-5", "TNSC-2411-6"];
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const errors = [];
const session = async (email) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "vi-VN", colorScheme: process.env.BARISTA_SHOT_DARK ? "dark" : "light" });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`PAGEERROR ${e.message}`));
  page.on("console", (m) => m.type() === "error" && !m.text().startsWith("Failed to load resource") && errors.push(`CONSOLE ${m.text().slice(0, 200)}`));
  page.on("response", (r) => {
    if (r.status() >= 400 && !(r.status() === 404 && r.url().endsWith("/v1/queries/talk.inbox"))) errors.push(`HTTP ${r.status()} ${r.url().replace(BASE, "").slice(0, 120)}`);
  });
  await page.goto(`${BASE}/login`);
  await page.click(`[data-login="${email}"]`);
  await page.getByRole("heading", { name: "Khung làm việc" }).waitFor({ timeout: 30000 });
  await page.waitForTimeout(1500);
  return page;
};
const shot = async (page, name, full = false) => {
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full });
  console.log("shot", name);
};
const composer = (page) => page.getByRole("textbox", { name: "Hỏi trợ lý" });
// chờ trợ lý trả lời xong: nút Dừng xuất hiện rồi biến mất
const settle = async (page) => {
  await page.getByRole("button", { name: "Dừng" }).waitFor({ timeout: 15000 }).catch(() => null);
  await page.getByRole("button", { name: "Dừng" }).waitFor({ state: "detached", timeout: 240000 });
  await page.waitForTimeout(800);
};
const closeDialogs = async (page) => {
  for (let i = 0; i < 4 && (await page.getByRole("dialog").count()); i++) {
    await page.getByRole("dialog").last().getByRole("button", { name: /Close|Đóng/ }).first().click({ force: true });
    await page.waitForTimeout(500);
  }
};
const toAssistant = async (page) => {
  await closeDialogs(page);
  await page.getByRole("navigation", { name: "Thanh bên trái" }).getByRole("button", { name: /^Trợ lý/ }).click();
  await composer(page).waitFor();
};
const ask = async (page, text) => {
  await toAssistant(page);
  await composer(page).fill(text);
  await page.keyboard.press("Enter");
  await settle(page);
};
const openScreen = async (page) => {
  await page.getByRole("navigation", { name: "Thanh bên trái" }).getByRole("button", { name: /^Tài trợ thương mại/ }).click();
  await page.getByRole("button", { name: /^Bộ chứng từ/ }).first().click();
  await page.waitForSelector("tbody tr");
  await page.waitForTimeout(800);
};
const openRow = async (page, number) => {
  await openScreen(page);
  await page.locator("tbody tr", { hasText: number }).first().dblclick();
  await page.getByRole("button", { name: /^Báo cáo v\d+/ }).first().waitFor({ timeout: 15000 });
  await page.waitForTimeout(600);
};
const openReport = async (page, number) => {
  await openRow(page, number);
  await page.getByRole("button", { name: /^Báo cáo v\d+/ }).first().click();
  await page.waitForTimeout(1500);
};

const from = Number(process.env.BARISTA_LC_FROM || 0);   // chạy tiếp từ cảnh n trên trạng thái đang có
const ha = await session("ha@nganhangmau.demo");
if (from <= 0) {
  await shot(ha, "00-ban-viec");
  await openScreen(ha);
  await shot(ha, "01-bo-chung-tu");
}

if (from <= 1) {
  // Cảnh 1: thả 6 tệp, tên trung tính
  await toAssistant(ha);
  const payload = FILES.map((f) => ({ name: `${f}.pdf`, b64: readFileSync(`${PDF}/nhan-bo/${f}.pdf`).toString("base64") }));
  await ha.evaluate((items) => {
    const dt = new DataTransfer();
    for (const it of items) dt.items.add(new File([Uint8Array.from(atob(it.b64), (c) => c.charCodeAt(0))], it.name, { type: "application/pdf" }));
    const el = document.querySelector('[aria-label="Hội thoại với trợ lý"]') ?? document.body;
    for (const type of ["dragenter", "dragover", "drop"]) el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
  }, payload);
  await ha.getByRole("radio").first().waitFor({ timeout: 10000 });
  await ha.getByRole("radio").first().click();
  await ha.getByRole("button", { name: "Tải lên" }).click();
  await ha.getByRole("button", { name: "Xong" }).waitFor({ timeout: 300000 });
  await ha.getByText("Đang đọc loại chứng từ…").waitFor({ state: "detached", timeout: 120000 });
  await shot(ha, "02-chip-nhan-loai");
  await ha.getByRole("button", { name: "Xong" }).click();
  await ha.waitForTimeout(800);
  const draft = await composer(ha).inputValue();
  await composer(ha).fill(`${draft}Nhận vào bộ chứng từ đang mở của L/C MKB-LC-26-0417 rồi kiểm tra giúp em.`);
  await ha.keyboard.press("Enter");
  await settle(ha);
  await ha.getByText(/^Đã làm \d+ bước/).last().click().catch(() => null);
  await shot(ha, "03a-tro-ly-hoi-va-dung");
  await ask(ha, "Tệp TNSC-2411-4 là chứng từ ngoài bộ, L/C không đòi. Kiểm tra tiếp đi.");
  await shot(ha, "03b-tro-ly-kiem-xong");
}

if (from <= 2) {
  // Cảnh 2: báo cáo, bằng chứng
  await openReport(ha, "0417");
  await shot(ha, "04a-bao-cao-sai-biet");
  await ha.getByText(/^Đã kiểm tra, đạt:/).first().scrollIntoViewIfNeeded();
  await shot(ha, "04b-da-kiem-dat");
  const evidence = ha.getByRole("button", { name: /USD 495,000\.00/ }).first();
  await evidence.scrollIntoViewIfNeeded();
  await evidence.click();
  await ha.locator("iframe").last().waitFor({ timeout: 15000 });
  await ha.waitForTimeout(2500);
  await shot(ha, "05-mo-ban-goc");
  await closeDialogs(ha);
}

let link = "";
if (from <= 3) {
  // Cảnh 3: what-if
  await ask(ha, "Nếu người thụ hưởng chưa chấp nhận tu chỉnh 01 thì sao?");
  await shot(ha, "06a-what-if-chat");
  await ha.getByRole("button", { name: "Mở", exact: true }).last().click({ timeout: 5000 }).catch(() => null);
  await ha.waitForTimeout(1500);
  await shot(ha, "06b-what-if-the");
  // Cảnh 4: công văn + link
  await ask(ha, "Báo sai biệt cho người thụ hưởng, xin bản sửa.");
  await shot(ha, "07a-de-xuat-cong-van");
  await ha.getByRole("button", { name: /^Xác nhận/ }).first().click();
  await settle(ha);
  await ha.waitForTimeout(1500);
  await shot(ha, "07b-cong-van-va-link");
  await ha.getByRole("navigation", { name: "Thanh bên trái" }).getByRole("button", { name: /^Tài trợ/ }).click();
  link = (await ha.locator("body").innerText()).match(/https?:\/\/[^\s]+\/c\/[A-Za-z0-9_-]{20,}/)?.[0] ?? "";
  console.log("link", link ? "có" : "KHÔNG");
}

if (from <= 4 && link) {
  // Cảnh 5: người bán, không đăng nhập
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 800 }, locale: "vi-VN" });
  const pub = await ctx.newPage();
  await pub.goto(link);
  const inputs = pub.locator('input[type="file"]');
  await inputs.first().waitFor({ state: "attached", timeout: 30000 });
  await pub.waitForTimeout(800);
  await shot(pub, "08a-nguoi-ban-mo-link");
  const n = await inputs.count();
  const text = await pub.locator("body").innerText();
  const order = text.indexOf("Phiếu đóng gói") < text.indexOf("Bảo hiểm") ? ["xuat-trinh-2_3-phieu-dong-goi-thay.pdf", "xuat-trinh-2_4-bao-hiem-thay.pdf"] : ["xuat-trinh-2_4-bao-hiem-thay.pdf", "xuat-trinh-2_3-phieu-dong-goi-thay.pdf"];
  for (let i = 0; i < n; i++) {
    await inputs.nth(i).setInputFiles(`${PDF}/${order[i]}`);
    await pub.waitForTimeout(2500);
  }
  await shot(pub, "08b-nguoi-ban-da-nop");
  await pub.close();
  const t0 = Date.now();
  for (;;) {
    await ha.reload();
    await ha.getByRole("heading", { name: "Khung làm việc" }).waitFor({ timeout: 30000 });
    await openScreen(ha);
    const row = await ha.locator("tbody tr", { hasText: "0417" }).first().innerText();
    if (/v3/.test(row) || Date.now() - t0 > 240000) break;
    await ha.waitForTimeout(4000);
  }
  console.log("kiểm tra lại xong sau", Math.round((Date.now() - t0) / 1000), "s");
  await shot(ha, "09a-tu-kiem-lai");
}

if (from <= 5) {
  // Cảnh 6: gửi duyệt, kiểm soát viên, hồ sơ
  await openRow(ha, "0417");
  await ha.getByRole("button", { name: "Công văn sai biệt" }).first().click();
  await ha.waitForTimeout(2000);
  await shot(ha, "07c-cong-van");
  await closeDialogs(ha);
  await openReport(ha, "0417");
  await shot(ha, "09b-bao-cao-0-sai-biet");
  await ha.getByRole("button", { name: "Gửi duyệt kết luận" }).first().click();
  await ha.waitForTimeout(1500);
  await ha.getByRole("dialog").last().getByRole("button", { name: "Gửi duyệt kết luận" }).click();
  await ha.waitForTimeout(2500);
  await shot(ha, "10a-da-gui-duyet");
  await closeDialogs(ha);
  const quan = await session("quan@nganhangmau.demo");
  await quan.getByRole("navigation", { name: "Thanh bên trái" }).getByRole("button", { name: /^Việc/ }).click();
  await quan.waitForTimeout(1500);
  await shot(quan, "10b-phieu-cho-duyet");
  await quan.getByText("Phiếu duyệt").first().click();
  await quan.waitForTimeout(1500);
  await shot(quan, "10b2-danh-sach-phieu");
  await quan.getByText(/MKB-LC-26-0417/).first().click();
  await quan.waitForTimeout(1500);
  await shot(quan, "10c-kiem-soat-vien-xem-phieu");
  await quan.getByRole("button", { name: /^Duyệt/ }).first().click();
  await quan.waitForTimeout(1200);
  const yes = quan.getByRole("dialog").last().getByRole("button", { name: /Duyệt|Xác nhận/ }).last();
  if (await yes.count()) await yes.click();
  await quan.waitForTimeout(2500);
  await shot(quan, "10d-da-duyet");
  await closeDialogs(quan);
  await openReport(quan, "0417");
  await quan.getByRole("button", { name: "Xuất bộ hồ sơ kiểm tra" }).first().click();
  await quan.waitForTimeout(1200);
  await quan.getByRole("dialog").last().getByRole("button", { name: "Xuất bộ hồ sơ kiểm tra" }).click();
  await quan.waitForTimeout(3000);
  await shot(quan, "10e-ho-so-kiem");
}

// Cảnh phụ: bộ 0533, chưa đủ dữ kiện
if (from <= 6) {
  await openReport(ha, "0533");
  await shot(ha, "11-chua-du-du-kien");
}
await browser.close();
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
