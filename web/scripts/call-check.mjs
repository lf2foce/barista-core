// Kiểm cuộc gọi trực tiếp 1–1 thật (core 0114 bổ sung 07/11): MỘT Chromium headless, HAI ngữ cảnh, micro / camera giả. Đo bằng getStats.
// Dùng: node scripts/call-check.mjs — cần web (BARISTA_WEB_URL, mặc định http://localhost:3100) + core có đăng nhập dev đang chạy.
// Chỉ thử trên một máy: không NAT, không TURN (ICE nối thẳng qua địa chỉ của máy). In từng bước + số gói tin; lỗi ⇒ thoát mã 1.
import { chromium } from "playwright";

const BASE = process.env.BARISTA_WEB_URL || "http://localhost:3100";
const A_EMAIL = process.env.CALL_A || "giamdoc@sanmau.demo";
const B_EMAIL = process.env.CALL_B || "truongb@sanmau.demo";
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const errors = [];

const browser = await chromium.launch({
  args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
});

async function context(name) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: "vi-VN", permissions: ["microphone", "camera"] });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`[${name}] PAGEERROR ${e.message}`));
  page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && errors.push(`[${name}] CONSOLE ${m.text().slice(0, 300)}`));
  return { ctx, page };
}

async function login(page, email) {
  await page.goto(`${BASE}/login`);
  const logins = await page.evaluate(async () => (await fetch("/api/auth/dev-logins")).json());
  const me = logins.find((l) => l.email === email);
  if (!me) throw new Error(`không có đăng nhập dev ${email}`);
  const r = await page.evaluate(async (b) => (await fetch("/api/auth/dev", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b) })).status, { email, tenant_id: me.tenant_id });
  if (r !== 200) throw new Error(`đăng nhập ${email}: ${r}`);
}

const api = (page, path, body) =>
  page.evaluate(async ([p, b]) => {
    const r = await fetch(`/api/core${p}`, { method: "POST", headers: { "content-type": "application/json", "idempotency-key": crypto.randomUUID() }, body: JSON.stringify(b) });
    return r.json();
  }, [path, body]);

const phase = (page) => page.locator("[data-phase]").getAttribute("data-phase");
async function waitPhase(page, want, ms = 20_000) {
  const t = Date.now();
  while (Date.now() - t < ms) {
    if ((await phase(page).catch(() => null)) === want) return Date.now() - t;
    await sleep(200);
  }
  const info = await page.evaluate(() => JSON.stringify({ d: window.__baristaCall?.debug(), s: window.__baristaCall?.snap })).catch(() => "?");
  throw new Error(`không tới ${want} sau ${ms} ms (đang ${await phase(page).catch(() => "?")}) ${info}`);
}
const flow = (page) => page.evaluate(() => window.__baristaCall.flow());
const dbg = (page) => page.evaluate(() => window.__baristaCall.debug());
async function flowing(page, label, key = "audioIn") {
  const a = await flow(page);
  await sleep(2000);
  const b = await flow(page);
  log(label, JSON.stringify(b), `+${b[key] - a[key]} gói ${key} / 2 giây`);
  if (!(b[key] > a[key])) throw new Error(`${label}: ${key} không tăng`);
  return b[key] - a[key];
}

let failed = false;
try {
  const A = await context("A");
  const B = await context("B");
  await login(A.page, A_EMAIL);
  await login(B.page, B_EMAIL);
  const room = await api(A.page, "/v1/commands/talk.create_room", { name: `Phòng gọi thử ${Date.now() % 100000}`, members: [B_EMAIL] });
  if (room.status !== "ok") throw new Error(`tạo phòng: ${room.error}`);
  const started = await api(A.page, "/v1/commands/meet.start", { subject_kind: "talk.room", subject_id: room.output.room_id, provider: "direct", with_member: B_EMAIL });
  if (started.status !== "ok") throw new Error(`bắt đầu: ${started.error}`);
  const cid = started.output.call_id;
  log("cuộc gọi", cid, started.output.call_page);

  // 1. Hai thành viên vào, nối, có tiếng hai chiều
  await A.page.goto(`${BASE}/goi/${cid}`);
  await A.page.getByRole("button", { name: "Vào cuộc gọi" }).click();
  await waitPhase(A.page, "waiting");
  await B.page.goto(`${BASE}/goi/${cid}`);
  const t0 = Date.now();
  await B.page.getByRole("button", { name: "Vào cuộc gọi" }).click();
  await waitPhase(B.page, "connected");
  await waitPhase(A.page, "connected");
  log(`đã nối sau ${Date.now() - t0} ms từ lúc B bấm Vào`);
  await flowing(A.page, "A nhận");
  await flowing(B.page, "B nhận");

  // 2. Khởi động lại ICE trên kết nối đang sống (đường rớt mạng ngắn đi): vẫn chạy, đổi bộ ICE
  const ufrag = () => A.page.evaluate(() => window.__baristaCall["pc"].localDescription.sdp.match(/a=ice-ufrag:(\S+)/)[1]);
  const before = await ufrag();
  await A.page.evaluate(() => window.__baristaCall.restartIce());
  await sleep(2500);
  const after = await ufrag();
  log("khởi động lại ICE: ufrag", before, "→", after);
  if (before === after) throw new Error("ICE không khởi động lại");
  await flowing(B.page, "B nhận sau khởi động lại ICE");

  // 3. Rớt mạng thật phía B (ngữ cảnh offline) — xem WebRTC có bị ảnh hưởng không
  await B.ctx.setOffline(true);
  await sleep(8000);
  log("sau 8 giây B offline:", JSON.stringify(await dbg(A.page)), "/ B", JSON.stringify(await dbg(B.page)));
  await B.ctx.setOffline(false);
  await sleep(1500);

  // 4. Rớt hẳn phía B: kết nối của B bị đóng ngang (không chào tạm biệt) ⇒ A mất nối, B tự dựng lại, hai bên nối lại
  const g0 = (await dbg(B.page)).gen;
  const a0 = (await dbg(A.page)).gen;
  const tDrop = Date.now();
  await B.page.evaluate(() => window.__baristaCall.simulateDrop());
  // A dựng lại kết nối (thế hệ mới) khi B chào lại — có thể nhanh hơn một vòng hỏi trạng thái nên đo bằng thế hệ, không bằng chữ "mất nối"
  await A.page.waitForFunction((g) => window.__baristaCall.debug().gen > g, a0, { timeout: 30_000 });
  log(`A dựng lại kết nối sau ${Date.now() - tDrop} ms`);
  try {
    await waitPhase(B.page, "connected", 40_000);
    await waitPhase(A.page, "connected", 40_000);
  } catch (e) {
    log("A:", await A.page.evaluate(() => JSON.stringify(window.__baristaCall.debug())));
    throw e;
  }
  log(`nối lại sau ${Date.now() - tDrop} ms; thế hệ kết nối B ${g0} → ${(await dbg(B.page)).gen}`);
  await flowing(A.page, "A nhận sau khi nối lại");
  await flowing(B.page, "B nhận sau khi nối lại");

  // 5. Hình: A bật camera trên CÙNG kết nối ⇒ B thấy hình
  await A.page.getByRole("button", { name: "Bật camera" }).click();
  await B.page.waitForFunction(() => window.__baristaCall.snap.remoteVideo === true, null, { timeout: 15_000 });
  await flowing(B.page, "B nhận hình", "videoIn");
  await A.page.screenshot({ path: process.env.CALL_SHOT_A || "/tmp/call-a.png" });
  await B.page.screenshot({ path: process.env.CALL_SHOT_B || "/tmp/call-b.png" });

  // 6. A kết thúc cho cả hai ⇒ B nhận "đã kết thúc"
  await A.page.getByRole("button", { name: "Kết thúc cuộc gọi cho cả hai" }).click();
  await waitPhase(B.page, "ended", 15_000);
  const card = await api(A.page, "/v1/queries/meet.call", { call_id: cid });
  const ends = card.output.blocks.find((b) => b.type === "list").items.map((i) => `${i.title}: ${i.subtitle}`);
  log("thẻ cuộc gọi:", ends.join(" | "));

  // 7. Người ngoài bằng link mời: cuộc gọi mới, B đổi thành ngữ cảnh không đăng nhập
  await B.ctx.close();
  const G = await context("G");
  const second = await api(A.page, "/v1/commands/meet.start", { subject_kind: "talk.room", subject_id: room.output.room_id, provider: "direct" });
  const cid2 = second.output.call_id;
  const inv = await api(A.page, "/v1/commands/meet.invite", { call_id: cid2, name: "Khách thử", hours: 1, adult_declared: true });
  if (inv.status !== "ok") throw new Error(`mời: ${inv.error}`);
  const path = new URL(inv.output.once_url).pathname;
  log("link mời", path.slice(0, 16) + "…");
  await A.page.goto(`${BASE}/goi/${cid2}`);
  await A.page.getByRole("button", { name: "Vào cuộc gọi" }).click();
  await G.page.goto(`${BASE}${path}`);
  await G.page.getByRole("button", { name: "Vào cuộc gọi" }).click();
  await waitPhase(G.page, "connected");
  await waitPhase(A.page, "connected");
  await flowing(A.page, "A nhận từ người ngoài");
  await flowing(G.page, "người ngoài nhận");
  // thu hồi lời mời giữa cuộc gọi ⇒ máy chủ báo "người kia không còn" ⇒ A đóng kết nối ngay, người ngoài nhận "gone" và đóng; rồi mời lại
  await api(A.page, "/v1/commands/meet.revoke_invite", { call_id: cid2 });
  await waitPhase(A.page, "waiting", 10_000);
  await waitPhase(G.page, "closed", 10_000);
  log("thu hồi ⇒ A về chờ, người ngoài bị đóng:", JSON.stringify(await dbg(A.page)));
  const inv2 = await api(A.page, "/v1/commands/meet.invite", { call_id: cid2, name: "Khách thử", hours: 1, adult_declared: true });
  await G.page.goto(`${BASE}${new URL(inv2.output.once_url).pathname}`);
  await G.page.getByRole("button", { name: "Vào cuộc gọi" }).click();
  await waitPhase(G.page, "connected");
  await waitPhase(A.page, "connected");
  await flowing(A.page, "A nhận từ người ngoài (link mới)");
  // người ngoài đóng thẻ ⇒ A thấy "đã rời", chờ
  await G.page.close();
  await waitPhase(A.page, "waiting", 15_000);
  log("người ngoài đóng thẻ ⇒ A về chờ");
  await A.page.getByRole("button", { name: "Kết thúc cuộc gọi cho cả hai" }).click();
  await sleep(2500);
  const card2 = await api(A.page, "/v1/queries/meet.call", { call_id: cid2 });
  log("thẻ cuộc gọi 2:", card2.output.blocks.find((b) => b.type === "list").items.map((i) => `${i.title}: ${i.subtitle}`).join(" | "));
  await G.ctx.close();
  await A.ctx.close();
} catch (e) {
  failed = true;
  console.error("LỖI:", e instanceof Error ? e.message : e);
} finally {
  await browser.close();
}
if (errors.length) console.error(errors.join("\n"));
process.exit(failed || errors.length ? 1 : 0);
