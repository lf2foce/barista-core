"use client";

import { Check, ChevronDown, ChevronLeft, Maximize2, Minimize2, Pin, PinOff, RefreshCw, Sparkles, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatCard, ScreenSpec } from "@/lib/types";
import { api } from "@/lib/api";
import { IconTile, moduleTint, screenTint, SfIcon } from "@/components/icons";
import { actionClass, Spinner } from "@/components/primitives";
import { newKey } from "@/lib/idempotency";
import { useEffect, useMemo, useRef, useState } from "react";
import { FormBody } from "@/components/forms/form-body";
import { ScreenView } from "@/components/pages/screen-view";
import { CardView } from "@/components/pages/card";
import { DetailView } from "@/components/pages/detail";
import { DataTable, KpiRow, HeaderCharts } from "@/components/pages/table";
import { Empty } from "@/components/pages/page-content";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { PANE_HEADER, RailToggle } from "./column";
import { screenItemId, useDesk, type CanvasItem } from "./desk-context";
import { useChat } from "./chat-context";
import { ProposalRow } from "./chat-cards";
import { BriefView, SettledLine } from "./brief";
import { FullFrameProvider } from "./full-frame";
import { FrameHeadProvider, FrameShellProvider, useFrameHead } from "./frame-context";
import { useSolo } from "./solo";
import { ClampNote } from "@/components/clamp-note";

/** Khung làm việc: thứ trợ lý vừa đụng tới (hoặc màn bấm mở nhanh) bung ra thành THẺ LỚN; thẻ cũ lùi xuống kệ "Đã mở trước đó". */
export function CanvasView() {
  const desk = useDesk();
  const chat = useChat();
  const solo = useSolo();
  const hero = desk.canvas.find((c) => c.id === desk.activeId);
  const shelf = chat.builder ? [] : desk.canvas.filter((c) => c.id !== hero?.id);   // trình tạo: chỉ app đang dựng
  // phân hệ một màn: màn là CẢ khung (không đầu khung, không thẻ bọc) — màn tự vẽ các ngăn của mình (FullFrame)
  const bare = !!solo && hero?.kind === "screen" && hero.id === solo.id;
  if (bare) {
    // phân hệ một màn: màn là CẢ khung — không đầu khung, không đệm, không kệ; màn tự vẽ ngăn của mình (FullFrame)
    return (
      <FullFrameProvider>
        <div className="relative flex h-full min-h-0 flex-col bg-background"><FrameContent key={hero.id} item={hero} /></div>
      </FullFrameProvider>
    );
  }
  return (
    <div className="relative flex h-full min-h-0 flex-col bg-canvas">
      {desk.canvasMax && hero ? null : <CanvasHeader />}
      {hero ? (
        <div className={cn("flex min-h-0 flex-1 flex-col gap-3", desk.canvasMax ? "p-2" : "p-2 pt-0 sm:p-3 sm:pt-0 min-[980px]:p-4 min-[980px]:pt-0")}>
          <CardFrame key={hero.id} item={hero} inCanvasView />
          {shelf.length && !desk.canvasMax ? (
            // kệ gọn một hàng chip (không chiếm ~120px như thẻ to): nhãn nhỏ + chip theo tên màn
            <div className="flex shrink-0 items-center gap-2">
              <p className="t12m shrink-0 text-muted-foreground">Đã mở:</p>
              <div className="no-scrollbar flex min-w-0 gap-1.5 overflow-x-auto" role="tablist" aria-label="Đã mở trước đó">
                {shelf.map((i) => <ShelfCard key={i.id} item={i} />)}
              </div>
            </div>
          ) : null}
        </div>
      ) : desk.manifest ? (
        <EmptyCanvas />
      ) : (
        <div className="grid flex-1 place-items-center"><Spinner className="size-6" /></div>
      )}
    </div>
  );
}

/** Màn ghim trên đầu khung THEO CHỖ ĐANG ĐỨNG: trong một phân hệ ⇒ ghim của phân hệ đó; trợ lý / việc / tin nhắn ⇒ bộ ghim chung; trình tạo app
 *  (0098) ⇒ không có (khung dành cho app đang dựng). */
function useContextPins(): ScreenSpec[] {
  const desk = useDesk();
  const chat = useChat();
  const m = desk.manifest;
  if (chat.builder) return [];
  if (desk.section.kind !== "workspace" || !m) return desk.quickScreens;
  const ws = m.workspaces.find((w) => w.key === (desk.section as { key: string }).key);
  const keys = new Set((m.pins ?? []).filter((p) => p.workspace === ws?.key).map((p) => p.key));
  return ws ? ws.screens.filter((s) => keys.has(s.key)) : [];
}

/** Màn liệt kê trong menu thả xuống ở đầu khung khi hẹp (không có chỗ cho một hàng nút): trong phân hệ ⇒ MỌI màn của phân hệ (như cột danh sách bên
 *  trái ở bề rộng đủ); ngoài phân hệ ⇒ màn ghim. */
function useMenuScreens(): { title: string; screens: ScreenSpec[] } {
  const desk = useDesk();
  const chat = useChat();
  const pins = useContextPins();
  if (chat.builder || desk.section.kind !== "workspace") return { title: "Màn ghim", screens: pins };
  const key = desk.section.key;
  const ws = desk.manifest?.workspaces.find((w) => w.key === key);
  return { title: ws?.title ?? "Màn", screens: ws?.screens ?? [] };
}

function CanvasHeader() {
  const desk = useDesk();
  const chat = useChat();
  const solo = useSolo();
  const pins = useContextPins();
  const menu = useMenuScreens();
  const inbox = chat.builder ? null : desk.manifest?.inbox;
  const title = chat.builder ? "Xem trước app" : solo ? solo.ws.title : "Khung làm việc";
  // mờ hai mép CHỈ khi thanh ghim thật sự tràn (cuộn ngang được) — không tràn thì nút rõ nét
  const pinsRef = useRef<HTMLElement>(null);
  const [pinsOverflow, setPinsOverflow] = useState(false);
  useEffect(() => {
    const el = pinsRef.current;
    if (!el) return;
    const check = () => setPinsOverflow(el.scrollWidth > el.clientWidth + 1);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [pins.length]);
  // hẹp (< 980px): không có chỗ cho một hàng nút ⇒ chính tên ở đầu khung là nút mở menu các màn; rộng: tên tĩnh + hàng ghim như trước
  const showMenu = !chat.builder && !solo && menu.screens.length > 1;
  return (
    <header className={cn(PANE_HEADER, "px-3 min-[980px]:px-6")}>
      {desk.railHidden || solo ? <RailToggle /> : null}
      <button type="button" onClick={() => desk.setNarrowShowsCanvas(false)} className={cn("grid size-8 shrink-0 place-items-center rounded-full border border-border bg-surface hover:bg-accent min-[980px]:hidden", solo && "!hidden")} aria-label="Về cột bên trái" title="Về cột bên trái">
        <ChevronLeft className="size-4" />
      </button>
      <div className="min-w-0 shrink-0">
        {showMenu ? (
          <DropdownMenu>
            <DropdownMenuTrigger className="t16 -mx-1.5 inline-flex max-w-[60vw] items-center gap-1 rounded-md px-1.5 py-0.5 outline-none hover:bg-foreground/5 focus-visible:ring-2 focus-visible:ring-ring data-popup-open:bg-foreground/5 min-[980px]:hidden" aria-label={`${title} — chọn màn`}>
              <span className="truncate">{title}</span>
              <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="w-72 max-w-[calc(100vw-1.5rem)]">
              <DropdownMenuGroup>
                <DropdownMenuLabel>{menu.title}</DropdownMenuLabel>
                {menu.screens.map((s) => {
                  const on = desk.activeId === screenItemId(s);
                  return (
                    <DropdownMenuItem key={s.key + s.query} onClick={() => desk.openScreen(s)} aria-current={on ? "true" : undefined} className="gap-2.5 px-2 py-1.5">
                      <IconTile icon={s.icon} tint={screenTint(s)} size={24} />
                      <span className={cn("min-w-0 flex-1 truncate text-[14px]", on ? "font-medium" : "font-normal")}>{s.title}</span>
                      {s.badge ? <span className="t12m shrink-0 rounded-full bg-primary/12 px-2 py-px text-primary">{s.badge}</span> : null}
                      {on ? <Check className="size-4 shrink-0 text-primary" aria-hidden /> : null}
                    </DropdownMenuItem>
                  );
                })}
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
        <h2 className={cn("t16 whitespace-nowrap", showMenu && "max-[979px]:hidden")}>{title}</h2>
        <p className="t12 hidden text-muted-foreground min-[1100px]:block">
          {chat.builder ? "Cập nhật sau mỗi bước của trình tạo — dữ liệu thật, đúng quyền của bạn" : `Dữ liệu đọc đúng quyền của ${desk.manifest?.user.name ?? "bạn"}`}
        </p>
      </div>
      <nav ref={pinsRef} className={cn("no-scrollbar ml-auto flex min-w-0 items-center gap-2 overflow-x-auto py-1", solo && "hidden",
                                        pinsOverflow && "[mask-image:linear-gradient(to_right,transparent,#000_16px,#000_calc(100%-16px),transparent)]")} aria-label="Màn ghim">
        {inbox && desk.inboxCount > 0 ? (
          <button type="button" onClick={() => desk.openScreen(inbox)} className="t14m inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg bg-ok px-2.5 text-white">
            <SfIcon name="checkmark.seal.fill" className="size-3.5" />
            {desk.inboxCount} chờ duyệt
          </button>
        ) : null}
        {pins.map((s) => (
          <button key={s.key + s.query} type="button" onClick={() => desk.openScreen(s)} title={s.title} className={cn("t14m h-8 shrink-0 items-center gap-1.5 rounded-full border border-border bg-surface px-3 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring", showMenu ? "hidden min-[980px]:inline-flex" : "inline-flex")}>
            <span style={{ color: screenTint(s) }}><SfIcon name={s.icon} className="size-3.5" /></span>
            <span className="whitespace-nowrap">{s.title}</span>
          </button>
        ))}
      </nav>
      {desk.canvas.length && !solo ? (
        <button type="button" onClick={desk.clearCanvas} className="shrink-0 text-muted-foreground/70 hover:text-foreground" aria-label="Dọn khung" title="Dọn khung">
          <XCircle className="size-[18px]" />
        </button>
      ) : null}
    </header>
  );
}

function itemStyle(item: CanvasItem): { icon: string; tint: string; title: string } {
  if (item.kind === "screen") return { icon: item.spec.icon, tint: screenTint(item.spec), title: item.spec.title };
  const c = item.card;
  if (c.kind === "proposal") return { icon: "sparkles", tint: "#af52de", title: `Đề xuất: ${c.title}` };
  return { icon: iconOfCard(c), tint: moduleTint(c.query ?? c.command ?? ""), title: c.title };
}
function iconOfCard(c: ChatCard): string {
  return c.page?.kind === "card" && c.page.icon ? c.page.icon : "list.bullet.rectangle";
}

/** Thẻ lớn: mặt thẻ bo 20, viền mảnh, không đổ bóng; đầu thẻ = ô biểu tượng + tên + nút đóng. */
/** `onClose`: thay hành vi nút đóng (vd ngăn kiểm tra cạnh cuộc trò chuyện chỉ ẩn ngăn, không đóng thẻ). */
export function CardFrame({ item, onClose, closeLabel = "Đóng thẻ", inCanvasView = false }: { item: CanvasItem; onClose?: () => void; closeLabel?: string; inCanvasView?: boolean }) {
  const desk = useDesk();
  const st = itemStyle(item);
  // nội dung tự vẽ khung (khối soạn với AI — 0231) ⇒ thẻ không vẽ đầu / viền, chỉ giao các nút cho khối (không lồng hai lớp khung)
  const [bare, setBare] = useState(false);
  // màn mở từ menu / ghim: phụ đề nói màn thuộc đâu (phân hệ · nhóm) — không ghi "Màn ghim" cho mọi màn
  const home = item.kind === "screen" ? desk.manifest?.workspaces.find((w) => w.screens.some((s) => s.key === item.spec.key && s.query === item.spec.query)) : undefined;
  const sub =
    item.kind === "screen"
      ? [home?.title, item.spec.group].filter(Boolean).join(" · ")
      : item.card.kind === "proposal"
        ? (desk.settled(item.card)?.label ?? "Trợ lý soạn sẵn và đã chạy thử — chưa ghi gì cho tới khi bạn xác nhận")
        : item.card.id.startsWith("open:")
          ? "Mở từ tin nhắn / thông báo"
          : `Trợ lý mở${item.card.subtitle ? ` · ${item.card.subtitle}` : ""}${item.card.page?.kind === "table" ? ` → ${item.card.page.rows.length} dòng` : ""}`;
  const actions = (
    <>
      {item.kind === "screen" ? (
        <button type="button" onClick={() => desk.refreshItem(item.id)} className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" aria-label="Tải lại" title="Tải lại">
          <RefreshCw className="size-4" />
        </button>
      ) : null}
      {inCanvasView ? (
        <button type="button" onClick={desk.toggleCanvasMax} className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" aria-label={desk.canvasMax ? "Thu nhỏ" : "Phóng to"} aria-pressed={desk.canvasMax} title={desk.canvasMax ? "Thu nhỏ (Esc)" : "Phóng to — thẻ chiếm trọn màn hình"}>
          {desk.canvasMax ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
        </button>
      ) : null}
      {item.kind === "screen" ? <PinButton spec={item.spec} /> : null}
      <button type="button" onClick={onClose ?? (() => desk.closeItem(item.id))} className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" aria-label={closeLabel} title={closeLabel}>
        <X className="size-4" />
      </button>
    </>
  );
  // đầu thẻ gọn: màn cắm bộ lọc chính + nút hỏi trợ lý vào `tools`, đặt khoảng ngày làm phụ đề (`ownSub`) — ô số lên sát đầu thẻ.
  // Thẻ đủ rộng (đo theo THẺ, không theo cửa sổ): công cụ nằm cùng hàng tên, bên phải; hẹp: xuống một hàng riêng ngay dưới tên.
  const [tools, setTools] = useState<HTMLElement | null>(null);
  const [ownSub, setOwnSub] = useState<string | null>(null);
  const head = useMemo(() => (bare ? null : { tools, setSub: setOwnSub }), [bare, tools]);
  // cùng một cây (section › [đầu] › nội dung) ở cả hai kiểu ⇒ đổi kiểu không dựng lại nội dung (không tải lại màn)
  return (
    <FrameShellProvider value={{ actions: bare ? actions : null, setBare }}>
      {/* điện thoại: CẢ thẻ cuộn (đầu thẻ + bộ lọc trôi đi theo) — đầu thẻ ghim cố định ăn mất nửa màn hình nhỏ, bảng chỉ còn vài dòng */}
      <section className={cn("@container flex min-h-0 flex-1 flex-col", !bare && "gap-3 rounded-[20px] border border-border bg-surface p-3 max-sm:overflow-y-auto min-[980px]:p-4")} aria-label={st.title}>
        {bare ? null : (
          <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2.5 gap-y-2 @min-[760px]:grid-cols-[auto_minmax(220px,1fr)_auto_auto]">
            <IconTile icon={st.icon} tint={st.tint} />
            <div className="min-w-0">
              <h3 className="t16 truncate">{st.title}</h3>
              <p className={cn("t12 text-muted-foreground", ownSub ? "line-clamp-2 select-text" : "truncate")} title={ownSub ? [sub, ownSub].join(" · ") : undefined}>{ownSub ?? sub}</p>
            </div>
            <div ref={setTools} className="col-span-3 row-start-2 flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-2 empty:hidden @min-[760px]:col-span-1 @min-[760px]:col-start-3 @min-[760px]:row-start-1 @min-[760px]:justify-end" />
            <div className="col-start-3 row-start-1 flex shrink-0 items-center @min-[760px]:col-start-4">{actions}</div>
          </div>
        )}
        <div className={cn("flex min-h-0 flex-1 flex-col", !bare && "overflow-y-auto max-sm:flex-none max-sm:overflow-visible")}>
          <FrameHeadProvider value={head}>
            <FrameContent item={item} />
          </FrameHeadProvider>
        </div>
      </section>
    </FrameShellProvider>
  );
}

/** Ghim / bỏ ghim màn lên đầu sidebar của CHÍNH mình (0098). Màn ghim mặc định theo vai không có nút (vai quyết). */
function PinButton({ spec }: { spec: ScreenSpec }) {
  const desk = useDesk();
  const [busy, setBusy] = useState(false);
  const ws = desk.manifest?.workspaces.find((w) => w.screens.some((s) => s.key === spec.key && s.query === spec.query))?.key;
  if (!ws) return null;
  const pin = desk.manifest?.pins.find((p) => p.workspace === ws && p.key === spec.key);
  if (pin && !pin.mine) return null;
  const label = pin ? "Bỏ ghim" : "Ghim lên sidebar";
  const toggle = async () => {
    setBusy(true);
    try {
      const r = await api.command(pin ? "core.unpin" : "core.pin", { workspace: ws, key: spec.key }, newKey());
      if (r.status !== "ok") desk.show(r.error || label, true);
      else await desk.reloadManifest();
    } catch (e) {
      desk.show(e instanceof Error ? e.message : label, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button type="button" disabled={busy} onClick={() => void toggle()} aria-pressed={!!pin} aria-label={label} title={label}
            className={cn("grid size-7 place-items-center rounded-md hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring", pin ? "text-primary" : "text-muted-foreground hover:text-foreground")}>
      {pin ? <PinOff className="size-4" /> : <Pin className="size-4" />}
    </button>
  );
}

function FrameContent({ item }: { item: CanvasItem }) {
  const desk = useDesk();
  if (item.kind === "screen") return <ScreenView spec={item.spec} refresh={item.rev} onChanged={() => void desk.refreshInbox()} />;
  const card = item.card;
  if (card.kind === "proposal") return <ProposalBody card={card} />;
  const spec = desk.screenFor(card.query ?? "");
  // cùng truy vấn với một màn ghim ⇒ có luôn bộ lọc, nút thao tác dòng, xuất Excel của màn đó
  if (spec) return <ScreenView spec={spec} initialFilters={card.input ?? {}} initialPage={card.page ?? null} onChanged={() => void desk.refreshInbox()} />;
  if (card.page) return <StaticPage page={card.page} />;
  return <Empty text="Thẻ không có nội dung" />;
}

/** Trang không gắn màn ghim nào (gợi ý căn, profile căn, kết quả tìm…): vẽ thẳng KPI + bảng / chi tiết / thẻ. */
function StaticPage({ page }: { page: NonNullable<ChatCard["page"]> }) {
  const head = useFrameHead();
  if (page.kind === "detail") return <DetailView page={page} />;
  if (page.kind === "card") return <FrameHeadProvider value={null}><CardView page={page} head={head} /></FrameHeadProvider>;
  return (
    <div className="flex flex-col gap-3">
      <KpiRow kpis={page.kpis} inset />
      {page.note ? <ClampNote text={page.note} /> : null}
      <HeaderCharts page={page} />
      {page.rows.length ? <div className="overflow-auto rounded-xl border border-hairline"><DataTable page={page} /></div> : <Empty text={page.empty} />}
    </div>
  );
}

/**
 * Thẻ đề xuất trên khung làm việc: bản chạy thử chuẩn trước (hồ sơ · cũ → mới · vì sao · đảo được) cùng nút Xác nhận / Xem, sửa / Bỏ — cùng hình với thẻ trong chat.
 * "Xem, sửa" mới mở form đủ ô (thẻ không bày cả form lên màn). Đã quyết ⇒ server nói trạng thái (`state`), kèm đường mở đúng phiếu nếu thành "Đã gửi duyệt".
 */
function ProposalBody({ card }: { card: ChatCard }) {
  const desk = useDesk();
  const chat = useChat();
  const [editing, setEditing] = useState(!card.brief || card.source !== "morning");
  const [dismissing, setDismissing] = useState(false);
  const done = desk.settled(card);
  const resumable = card.resumable === true;
  const morning = card.source === "morning"; // đề xuất soạn sẵn buổi sáng (0074 A) — quyết qua morning/{id}/decide
  if (done) return <SettledLine settled={done} className="t16" />;
  const finish = (message: string, approvalId?: string) => {
    desk.setConfirmed(card.id, message, approvalId);
    chat.addSystem(`✓ ${message}`);
    void desk.refreshInbox();
  };
  const dismiss = async () => {
    setDismissing(true);
    try {
      const o = morning ? await chat.decideMorning(card, "dismiss", {}, newKey()) : await chat.decide(card, "dismiss", {}, newKey());
      if (o.done) {
        desk.setConfirmed(card.id, "Đã bỏ đề xuất");
        chat.addSystem(`✕ Đã bỏ đề xuất: ${card.title}`);
      } else desk.show(o.message || o.status, true);
    } finally {
      setDismissing(false);
    }
  };
  if (!editing) {
    return (
      <div className="flex flex-col gap-3">
        <p className="t16">{card.title}</p>
        <ProposalRow card={card} onEdit={() => setEditing(true)} />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {card.brief ? <BriefView brief={card.brief} className="rounded-xl bg-surface2 px-3 py-2.5" /> : null}
      <FormBody
        key={card.id}
        action={{ command: card.command ?? "", title: "Xác nhận", style: "primary", bind: {}, preset: {}, when: {}, importable: false }}
        initial={desk.proposalEdits[card.id] ?? card.input ?? {}}
        prefetched={card.fields}
        embedded
        onEdit={(v) => desk.setProposalEdit(card.id, v)}
        // lượt đang dừng (0049): Xác nhận chạy lệnh bằng quyền NGƯỜI qua `decide` rồi trợ lý làm tiếp; thẻ cũ: gọi lệnh trực tiếp
        submitter={
          morning ? (input, key) => chat.decideMorning(card, "confirm", input, key) : resumable ? (input, key) => chat.decide(card, "confirm", input, key) : undefined
        }
        onDone={(m) => finish(m.replace("Xác nhận: ", ""))}
      />
      {resumable || morning ? (
        <div className="flex flex-wrap items-center gap-2.5">
          <button type="button" onClick={() => void dismiss()} disabled={dismissing} className={actionClass("destructive")}>
            <XCircle className="size-3.5" aria-hidden />
            {dismissing ? "Đang bỏ…" : "Bỏ đề xuất"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ShelfCard({ item }: { item: CanvasItem }) {
  const desk = useDesk();
  const st = itemStyle(item);
  return (
    <button type="button" role="tab" aria-selected={false} onClick={() => desk.setActiveId(item.id)} title={st.title}
            className="t12m flex h-8 max-w-[200px] shrink-0 items-center gap-1.5 rounded-lg border border-border bg-surface px-2 text-left outline-none hover:bg-surface2 focus-visible:ring-2 focus-visible:ring-ring">
      <IconTile icon={st.icon} tint={st.tint} size={20} />
      <span className="truncate">{st.title}</span>
    </button>
  );
}

function EmptyCanvas() {
  const chat = useChat();
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
      <span className="grid size-16 place-items-center rounded-full text-white" style={{ background: "radial-gradient(circle at 30% 25%, #7fb5ff, #007aff 55%, #5856d6)" }}>
        <Sparkles className={cn("size-7", chat.live && "animate-pulse")} aria-hidden />
      </span>
      <h3 className="t16">Chưa có gì mở</h3>
      <p className="t14 max-w-[420px] text-muted-foreground">Hỏi trợ lý ở bên trái — bảng hàng, khách, giao dịch, ví hoa hồng, dòng tiền, chiến dịch… sẽ bung ra ở đây.</p>
      <p className="t12 text-muted-foreground">Hoặc mở nhanh bằng các nút phía trên</p>
    </div>
  );
}
