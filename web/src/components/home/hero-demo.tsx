"use client";

import { useState, useSyncExternalStore } from "react";
import { ArrowDownLeft, ChartGantt, House, Inbox, Landmark, ReceiptText, RotateCcw, Users } from "lucide-react";
import { Badge, Bar, Check, Collapse, Count, Field, Stage, States, Text, Window, type Act, type Layout } from "@/components/stageshow";
import { StillContext } from "@/components/stageshow/reduced-motion";
import "./hero-demo.css";

/** Ảnh động đầu trang giới thiệu: một khoản tiền về → trợ lý tự đối chiếu, đề xuất ghi nhận thanh toán (đã chạy thử) → NGƯỜI bấm Duyệt → dòng tiền
 *  chuyển "Đã khớp", số khoản chưa khớp giảm. Đúng luật của sản phẩm: trợ lý soạn sẵn, người duyệt. Dữ liệu là GIẢ (tên cửa hàng, số tiền, mã đơn).
 *  Chạy một lần khi cuộn tới; ảnh tĩnh (poster) là kết quả cuối — người bật giảm chuyển động chỉ thấy ảnh này. */

type S = { arrived: boolean; open: number; phase: "idle" | "think" | "card"; run: "idle" | "busy" | "done"; matched: boolean };

const initial: S = { arrived: false, open: 2, phase: "idle", run: "idle", matched: false };
const poster: S = { arrived: true, open: 2, phase: "card", run: "done", matched: true };

async function script(a: Act<S>) {
  await a.wait(1600);
  a.mark("arrive");
  a.put({ arrived: true, open: 3 });
  await a.wait(1000);
  a.mark("think");
  a.put({ phase: "think" });
  await a.wait(1300);
  a.mark("card");
  a.put({ phase: "card" });
  await a.wait(1500);
  a.mark("approve");
  await a.click({ at: "approve", x: 0.5, y: 0.6 });
  a.put({ run: "busy" });
  await a.wait(800);
  a.put({ run: "done" });
  await a.wait(450);
  a.mark("match");
  a.put({ matched: true, open: 2 });
  await a.move({ wide: { x: 1060, y: 420 }, compact: { x: 330, y: 540 } });
  await a.wait(3000);
}

/** Theo giao diện sáng / tối của app (lớp .dark trên <html>, đặt bởi lib/theme.ts). */
function subscribe(cb: () => void) {
  const mo = new MutationObserver(cb);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => mo.disconnect();
}
function useDark() {
  return useSyncExternalStore(subscribe, () => document.documentElement.classList.contains("dark"), () => false);
}

const ICON = { size: 15, strokeWidth: 1.8, className: "bh-icon" } as const;

function Sidebar() {
  const nav = (icon: React.ReactNode, w: number) => (
    <div className="bh-nav">
      {icon}
      <Bar w={w} h={5} tone={3} />
    </div>
  );
  return (
    <div className="bh-side">
      <div className="bh-org"><Bar w={88} h={6} tone={4} /></div>
      <div className="bh-group">
        {nav(<House {...ICON} />, 44)}
        {nav(<Inbox {...ICON} />, 58)}
        <div className="bh-nav" data-active>
          <Landmark {...ICON} className="bh-icon bh-icon--on" />
          <Text className="bh-nav__label">Tiền vào</Text>
        </div>
        {nav(<ReceiptText {...ICON} />, 50)}
        {nav(<ChartGantt {...ICON} />, 62)}
        {nav(<Users {...ICON} />, 40)}
      </div>
    </div>
  );
}

function Money({ s, compact }: { s: S; compact: boolean }) {
  const filler = compact ? [] : [[170, 96], [140, 72], [186, 84], [128, 66], [160, 88]];
  return (
    <div className="bh-money">
      <div className="bh-list">
        <Collapse open={s.arrived} reveal="drop">
          <div className="bh-row" data-target="row" data-new={!s.matched || undefined}>
            <span className="bh-lead"><ArrowDownLeft size={16} strokeWidth={2} /></span>
            <span className="bh-main">
              <span className="bh-amt">+48.500.000 ₫</span>
              <span className="bh-memo">CK DH1042 MOC LAN</span>
            </span>
            <States
              value={s.matched ? "ok" : "wait"}
              align="end"
              states={{
                wait: <Badge tone="warning">Chưa khớp</Badge>,
                ok: <Badge tone="success" icon={<Check on size={12} />}>Đã khớp DH-1042</Badge>,
              }}
            />
          </div>
        </Collapse>
        <div className="bh-row">
          <span className="bh-lead"><ArrowDownLeft size={16} strokeWidth={2} /></span>
          <span className="bh-main">
            <span className="bh-amt">+12.000.000 ₫</span>
            <span className="bh-memo">TT HD0211 CUA HANG AN BINH</span>
          </span>
          <Badge tone="success">Đã khớp</Badge>
        </div>
        {filler.map(([a, b], i) => (
          <div className="bh-row" key={i}>
            <span className="bh-lead bh-lead--quiet" />
            <span className="bh-main">
              <Bar w={a} h={6} tone={3} />
              <Bar w={b} h={5} tone={2} />
            </span>
            <Bar w={58} h={18} r={9} tone={1} />
          </div>
        ))}
      </div>
    </div>
  );
}

function Assistant({ s }: { s: S }) {
  return (
    <div className="bh-chat">
      <div className="bh-chat__head"><Text className="bh-chat__title">Trợ lý</Text></div>
      <div className="bh-thread">
        {[[150, "78%", "52%"]].map(([q, a1, a2], i) => (
          <div className="bh-past" key={i}>
            <Bar w={q} h={28} r={14} tone={1} className="bh-past-q" />
            <Bar w={a1} h={7} r={4} tone={2} />
            <Bar w={a2} h={7} r={4} tone={2} />
          </div>
        ))}
        <div className="bh-reply">
          <Collapse open={s.phase === "think"}>
            <Text className="bh-thinking">Đang đối chiếu với sổ đơn hàng</Text>
          </Collapse>
          <Collapse open={s.phase === "card"} reveal="drop">
            <div className="bh-a">
              <Text className="bh-line">Khoản 48,5 triệu vừa về khớp đơn DH-1042 của Nội thất Mộc Lan: đúng mã đơn, đủ số tiền.</Text>
              <div className="bh-card" data-done={s.run === "done" || undefined}>
                <div className="bh-card__head">
                  <span className="bh-card__title">Ghi nhận thanh toán DH-1042</span>
                  <span className="bh-tried"><Check on size={12} />Đã chạy thử</span>
                </div>
                <div className="bh-kv"><span>Số tiền</span><span className="bh-kv__v">48.500.000 ₫</span></div>
                <div className="bh-kv"><span>Còn phải thu sau khi ghi</span><span className="bh-kv__v">0 ₫</span></div>
                <span className="bh-approve" data-target="approve" data-cursor="pointer" data-done={s.run === "done" || undefined}>
                  <span className="bh-layer" data-on={s.run === "idle" || undefined}>Duyệt</span>
                  <span className="bh-layer" data-on={s.run === "busy" || undefined}><Check on={false} pending size={16} /></span>
                  <span className="bh-layer" data-on={s.run === "done" || undefined}><Check on={s.run === "done"} size={16} />Đã duyệt</span>
                </span>
              </div>
            </div>
          </Collapse>
        </div>
      </div>
      <Field className="bh-composer" value="" placeholder="Hỏi trợ lý hoặc giao việc" />
    </div>
  );
}

/** Tên màn + số khoản chưa khớp nằm trên thanh cửa sổ (đầu cột nội dung). */
function Lead({ s }: { s: S }) {
  return (
    <span className="bh-headline">
      <Text className="bh-title">Tiền vào hôm nay</Text>
      <span className="bh-open">
        <span className="bh-open__n"><Count value={s.open} /></span>
        <Text>chưa khớp</Text>
      </span>
    </span>
  );
}

function Scene({ s, layout }: { s: S; layout: Layout }) {
  const compact = layout === "compact";
  if (compact) {
    return (
      <Window lead={<Lead s={s} />} toolbar={<span />} className="bh-win">
        <div className="bh-stack">
          <Money s={s} compact />
          <Assistant s={s} />
        </div>
      </Window>
    );
  }
  return (
    <Window lead={<Lead s={s} />} className="bh-win" sidebar={<Sidebar />} sidebarWidth={196} body={{ className: "bh-body" }}>
      <div className="bh-split">
        <Money s={s} compact={false} />
        <Assistant s={s} />
      </div>
    </Window>
  );
}

export function HeroDemo() {
  const dark = useDark();
  const [run, setRun] = useState(0);   // bấm "Xem lại" ⇒ dựng lại sân khấu từ đầu (bộ dựng không có lệnh phát lại)
  return (
    <div>
      <div className="bl-small mb-3 flex items-center justify-between gap-4 px-4 text-muted-foreground sm:px-6">
        <span>Tiền về → trợ lý đối chiếu và đề xuất → bạn bấm Duyệt</span>
        <button type="button" onClick={() => setRun((n) => n + 1)} className="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 font-medium text-foreground hover:bg-surface2">
          <RotateCcw className="size-3.5" /> Xem lại
        </button>
      </div>
      {/* chạy cả khi máy bật "giảm chuyển động" — như sơ đồ trợ lý (anh Anh chọn 23/11/2026): chuyển động nhẹ là phần chính của trang giới thiệu */}
      <div className="bh-wrap">
        <StillContext.Provider value={false}>
          <HeroStage key={run} dark={dark} />
        </StillContext.Provider>
      </div>
    </div>
  );
}

function HeroStage({ dark }: { dark: boolean }) {
  return (
    <Stage<S>
      width={1152}
      height={480}
      compact={{ below: 900, width: 358, height: 640 }}
      fill
      theme={dark ? "dark" : "light"}
      accent="var(--primary)"
      tokens={{ "--ss-font": "var(--font-sans)", "--ss-success": "var(--ok)", "--ss-warning": "var(--warn)" }}
      initial={initial}
      poster={poster}
      script={script}
      play="once"
      cursor={{ at: { wide: { x: 1060, y: 455 }, compact: { x: 330, y: 600 } } }}
      className="bh"
    >
      {(s, _rect, layout) => <Scene s={s} layout={layout} />}
    </Stage>
  );
}
