"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowUp, Check, ChevronRight, CircleAlert, Copy, Download, FileDown, FileSpreadsheet, FileText, History, Link2, MessageSquarePlus, RefreshCw,
  RotateCcw, Sparkles, Square, Trash2, X,
} from "lucide-react";
import { api, composeStream, type ComposeTurnBody } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { newKey } from "@/lib/idempotency";
import { applyComposeEvent, currentOverflow, groupSteps, liveTurn, overflowActions, retryTarget, splitPlaceholders, type LiveStep, type LiveTurn } from "@/lib/composer";
import { datetime } from "@/lib/format";
import { documentFileUrl } from "@/lib/links";
import type { ComposerData, ComposerItem, ComposerOverflowBlock, ComposerOverflowPage, ComposerSection, ComposerTurn, Input, Option } from "@/lib/types";
import { actionClass } from "@/components/primitives";
import { useDesk } from "@/components/desk/desk-context";
import { useFrameActions } from "@/components/desk/frame-context";
import { DataTable, KpiRow } from "@/components/pages/table";
import { useFullFrame } from "@/components/desk/full-frame";
import { HeaderGroup, Orb, PANE_HEADER, pillBtn, RailToggle } from "@/components/desk/column";
import { DictateMic } from "@/components/desk/dictate-mic";
import { COMPOSER_PILL, COMPOSER_TEXT, SendStopButton } from "@/components/desk/send-button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

const ORIGIN: Record<string, string> = { ai: "AI viết", human: "Người sửa", data: "Số liệu", kb: "Tài liệu", template: "Mẫu gói" };
const VIA: Record<string, string> = { ai: "AI", agent: "Trợ lý", human: "Bạn" };
/** Bấm "Tiếp tục" trên bản dở: một lượt sửa cả bản như người dùng tự gõ (ghi vào lịch sử trò chuyện). */
const CONTINUE = "Tiếp tục: làm nốt phần còn thiếu, dùng số liệu đã lấy được.";
// 0231 / 0249: ẤN PHẨM MARKETING (tờ rơi, tờ giới thiệu, slide, bài đăng). Công văn / hợp đồng ⇒ phân hệ Giấy tờ; báo cáo số nội bộ ⇒ hỏi trợ lý.
const EXAMPLES = [
  "Tờ rơi ưu đãi tháng này cho sản phẩm bán chạy nhất",
  "Tờ giới thiệu sản phẩm đang bán, kèm bảng giá",
  "Slide giới thiệu công ty cho buổi gặp khách hàng mới",
  "Bài đăng Facebook giới thiệu sản phẩm mới, kèm lời kêu gọi",
];

function base64Blob(b64: string, type: string): Blob {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type });
}

/** Chữ có chỗ giữ "[cần số liệu: …]" ⇒ tô vàng chỗ giữ (server đã tách sẵn danh sách; web chỉ tô). */
function Prose({ text }: { text: string }) {
  return (
    <>
      {splitPlaceholders(text).map((p, i) =>
        p.hole ? <mark key={i} className="rounded bg-warn/25 px-0.5 text-foreground">{p.text}</mark> : <span key={i}>{p.text}</span>,
      )}
    </>
  );
}

/** Lấy khối composer của trang bản soạn (server dựng; web chỉ vẽ). */
async function loadComposer(query: string, sid: string): Promise<ComposerData | null> {
  const page = await api.query(query, { session_id: sid });
  const c = page.kind === "card" ? page.blocks.find((b) => b.type === "composer") : undefined;
  return c?.type === "composer" ? c : null;
}

/**
 * Ấn phẩm (thử nghiệm; khối server tên kỹ thuật `composer`) — như một cuộc trò chuyện: trái = lịch sử bản soạn + trò chuyện (gõ yêu cầu, AI báo từng bước, lỗi nói rõ kèm
 * "Thử lại"), phải = bản soạn (khối hiện dần khi AI lấy được số liệu; sửa từng khối, xuất tệp). Mọi lượt AI đi qua luồng `/v1/compose/stream`
 * (cùng cửa lệnh `compose.begin` / `compose.revise_begin`); các thao tác khác là lệnh server. Web không tự tính số.
 */
export function ComposerBlock({ block }: { block: ComposerData; onChanged?: () => void }) {
  const desk = useDesk();
  const [doc, setDoc] = useState(block);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [local, setLocal] = useState<{ user: LiveTurn; error: string } | null>(null);   // lỗi trước khi có bản soạn (vd công ty đang soạn 2 bản)
  const [busy, setBusy] = useState<string | null>(null);
  const [kind, setKind] = useState(block.kind || "brochure");
  const [stopping, setStopping] = useState(false);
  const [draftOpen, setDraftOpen] = useState(block.blocks.length > 0);   // rộng: ngăn bản soạn bên phải mở / đóng
  const [view, setView] = useState<"chat" | "draft">(block.blocks.length > 0 ? "draft" : "chat");   // hẹp: một ngăn một lúc, hai thẻ "Trò chuyện | Bản soạn" luôn hiện; có bản soạn ⇒ mở thẳng bản soạn
  const [pct, setPct] = useState(56);                                    // rộng: bề ngang ngăn bản soạn (% khung), kéo đổi được
  const row = useRef<HTMLDivElement>(null);
  const full = useFullFrame();   // màn chiếm trọn khung (phân hệ một màn) ⇒ có nút ẩn / hiện thanh bên trái như đầu ngăn trợ lý
  const c = doc.commands;
  const viewQuery = c.view ?? "";   // tên thao tác do SERVER gửi (app chỉ vẽ — không viết cứng tên thao tác của gói)

  // trang chứa thẻ tải lại (server gửi khối mới) ⇒ vẽ theo bản mới
  const [seen, setSeen] = useState(block);
  if (seen !== block) {
    setSeen(block);
    setDoc(block);
    setLocal(null);
  }

  // ngăn bản soạn chỉ có khi CÓ bản soạn: khối đầu tiên chảy về (hoặc mở lại một bản đã lưu) ⇒ tự mở; người dùng đóng thì giữ đóng tới bản kế
  const hasDraft = doc.blocks.length > 0 || (!!live && (live.data.length > 0 || !!live.blocks));
  const [hadDraft, setHadDraft] = useState(hasDraft);
  if (hasDraft !== hadDraft) {
    setHadDraft(hasDraft);
    if (hasDraft) setDraftOpen(true);
    else setView("chat");
  }
  const [sess, setSess] = useState(doc.session_id);
  if (sess !== doc.session_id) {
    setSess(doc.session_id);
    setDraftOpen(hasDraft);
    setView(hasDraft ? "draft" : "chat");   // mở lại / vừa dựng một ấn phẩm có nội dung ⇒ hẹp cũng thấy bản soạn ngay, không phải bấm tìm
  }

  const reload = useCallback(async (sid: string) => {
    if (!sid || !viewQuery) return;
    try {
      const next = await loadComposer(viewQuery, sid);
      if (next) setDoc(next);
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không tải được ấn phẩm", true);
    }
  }, [viewQuery, desk]);

  // bản đang soạn do việc khác khởi tạo (trợ lý chat gọi compose.start): tự làm mới tới khi xong — không bắt người dùng mở lại
  useEffect(() => {
    // cũng tự làm mới khi server đang kiểm / rút gọn cho vừa trang (chạy nền sau mỗi lần ghi phiên bản của mẫu nhiều trang)
    if ((doc.status !== "drafting" && !doc.fitting) || live || !doc.session_id) return;
    const started = Date.now();
    const t = setInterval(() => {
      if (Date.now() - started > 120_000) clearInterval(t);
      else void reload(doc.session_id);
    }, 1500);
    return () => clearInterval(t);
  }, [doc.status, doc.fitting, doc.session_id, live, reload]);

  async function send(text: string, opts: { block?: string; mode?: string; retry?: boolean } = {}) {
    const t = text.trim();
    if (!t || live) return;
    const sid = doc.session_id;
    const body: ComposeTurnBody = sid
      ? { session_id: sid, instruction: t, block: opts.block ?? "", mode: opts.mode ?? "rewrite", retry: !!opts.retry }
      : { kind, brief: t };
    let turn = liveTurn(t, opts.block ?? "", opts.mode ?? "");
    setLocal(null);
    setLive(turn);
    try {
      for await (const ev of composeStream(body, undefined, doc.stream)) {
        turn = applyComposeEvent(turn, ev);
        setLive(turn);
      }
    } catch (e) {
      turn = { ...turn, done: true, error: e instanceof Error ? e.message : "Không soạn được — thử lại" };
    }
    if (turn.sessionId) await reload(turn.sessionId);   // server đã nhận lượt: lời người nói + kết quả (kể cả lỗi) đã lưu
    if (turn.blocks?.length && !turn.error) {   // soạn xong có kết quả ⇒ hẹp: chuyển sang "Bản soạn" (rộng: ngăn bản soạn vốn đã hiện)
      setDraftOpen(true);
      setView("draft");
    }
    else setLocal({ user: turn, error: turn.error || "Không soạn được — thử lại" });   // bị từ chối trước khi bắt đầu (vd công ty đang soạn 2 bản)
    setLive(null);
  }

  /** "Dừng" thật: lệnh server huỷ lượt model đang chạy (lượt vẫn chạy khi đóng trang, nên chỉ ngắt kết nối là nói dối). Luồng kết thúc bằng "đã dừng". */
  async function stop() {
    const sid = live?.sessionId;
    if (!sid || stopping || !c.stop) return;
    setStopping(true);
    try {
      const r = await api.command(c.stop, { session_id: sid }, newKey());
      if (r.status !== "ok") desk.show(r.error || "Không dừng được", true);
      else {
        const out = r.output as { stopped?: boolean; reason?: string } | null;
        // chỉ dừng được lượt chạy ở CÙNG tiến trình máy chủ: đang chạy ở tiến trình khác ⇒ nói thật, không giả vờ đã dừng
        if (out?.stopped === false) desk.show(out.reason === "elsewhere" ? "Đang chạy ở máy khác — thử dừng lại sau vài giây" : "Lượt này đã xong, không còn gì để dừng");
      }
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không dừng được", true);
    } finally {
      setStopping(false);
    }
  }

  function drag(e: React.PointerEvent<HTMLDivElement>) {
    const box = row.current?.getBoundingClientRect();
    if (!box || !(e.buttons & 1)) return;
    setPct(Math.min(75, Math.max(30, ((box.right - e.clientX) / box.width) * 100)));
  }

  async function run(command: string | undefined, input: Input, what: string): Promise<Record<string, unknown> | null> {
    if (!command) return null;
    setBusy(what);
    try {
      const r = await api.command(command, input, newKey());
      if (r.status !== "ok") {
        desk.show(r.error || `Không làm được (${r.status})`, true);
        return null;
      }
      return (r.output && typeof r.output === "object" && !Array.isArray(r.output) ? r.output : {}) as Record<string, unknown>;
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không làm được", true);
      return null;
    } finally {
      setBusy(null);
    }
  }

  const act = async (command: string | undefined, input: Input, what: string) => {
    const out = await run(command, input, what);
    if (out) await reload(doc.session_id);
    return out;
  };

  async function download(id: string) {
    if (!c.download) return;
    try {
      const f = await api.queryRaw<{ filename: string; content_type: string; data_b64: string }>(c.download, { export_id: id });
      saveBlob(base64Blob(f.data_b64, f.content_type), f.filename);
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không tải được", true);
    }
  }

  async function exportAs(format: string) {
    const out = await act(c.export, { session_id: doc.session_id, format }, `Đang xuất ${format.toUpperCase()}…`);
    if (!out) return;
    if (typeof out.warning === "string" && out.warning) desk.show(out.warning);
    if (typeof out.export_id === "string") await download(out.export_id);
  }

  async function open(id: string) {
    if (id === doc.session_id || live) return;
    setLocal(null);
    await reload(id);
  }

  function fresh() {
    if (live) return;
    setLocal(null);
    setDoc({ ...doc, session_id: "", title: "Ấn phẩm mới", brief: "", version: 0, status: "new", status_label: "", error: "", blocks: [],
             stats: { flagged: 0, placeholders: 0, data: 0 }, layout: { ...doc.layout, template: "", sections: [], html: "" }, shares: [], versions: [], exports: [], turns: [] });
  }

  async function remove(id: string) {
    const out = await run(c.delete, { session_id: id }, "Đang xoá…");
    if (!out) return;
    if (id === doc.session_id) fresh();
    setDoc((d) => ({ ...d, history: d.history.filter((h) => h.id !== id) }));
  }

  const recipe = useRecipe(doc, kind, !!live, (sid) => void reload(sid));
  const panes = hasDraft && draftOpen;
  const showDraft = () => { setView("draft"); setDraftOpen(true); };
  const tabs = hasDraft ? <ViewTabs view={view} onView={(v) => (v === "draft" ? showDraft() : setView("chat"))} fresh={!!live && live.data.length > 0 && !live.blocks} /> : null;
  // "Tài liệu đã soạn" thuộc phần CHÍNH (khung bản soạn); chưa mở bản soạn nào ⇒ nằm ở màn bắt đầu của trò chuyện. Đầu ngăn trò chuyện chỉ có những gì
  // đầu ngăn Trợ lý có.
  const docs = <HistoryMenu doc={doc} disabled={!!live} onOpen={open} onNew={fresh} onDelete={remove} />;
  const title = live?.blocks ? live.title || doc.title : doc.session_id ? doc.title : "Ấn phẩm mới";
  const frameActions = useFrameActions();   // trong thẻ của khung làm việc: nút của khung (tải lại · phóng to · ghim · đóng) — thẻ không vẽ đầu riêng (0231)
  return (
    // Y NHƯ màn Trợ lý: bên trái ngăn trò chuyện (đầu ngăn PANE_HEADER, hội thoại trên nền trang, thanh nhập ở đáy); bên phải KHUNG LÀM VIỆC chứa bản soạn,
    // mở khi khối đầu tiên chảy về (kéo đổi bề ngang, đóng được). Chưa có bản soạn ⇒ trò chuyện chiếm cả bề ngang. Hẹp ⇒ một ngăn một lúc + nút nổi
    // "Bản soạn" (như nút "Khung làm việc · N" của trợ lý), ngăn bản soạn có nút quay lại. Bố cục theo CHỖ của khối (container query).
    <div ref={row} className={cn("@container relative flex min-h-0 flex-1", full ? "" : "min-h-[min(75vh,680px)] overflow-hidden rounded-xl border border-hairline")}
         style={{ "--draft": `${100 - pct}%` } as React.CSSProperties}>
      <section className={cn("relative flex min-h-0 min-w-0 flex-1 flex-col bg-background", panes && "@3xl:flex-none @3xl:basis-(--draft)",
                             hasDraft && view === "draft" && "@max-3xl:hidden")} aria-label="Trò chuyện với AI">
        <header className={cn(PANE_HEADER, "px-3")}>
          {full ? <RailToggle /> : null}
          <Orb busy={!!live} size={36} />
          <div className="min-w-0 flex-1">
            <h1 className="t16 truncate">Ấn phẩm</h1>
            <p className="t12 flex items-center gap-1.5 text-muted-foreground">
              <span className={cn("size-[7px] shrink-0 rounded-full", live ? "bg-primary" : "bg-ok")} />
              <span className="truncate">{live ? "đang soạn…" : doc.session_id ? title : "Thử nghiệm · số liệu đúng quyền của bạn"}</span>
            </p>
          </div>
          <HeaderGroup>
          {hasDraft && !draftOpen ? (
            <button type="button" className={pillBtn} onClick={() => setDraftOpen(true)} aria-label="Mở ấn phẩm" title="Mở ấn phẩm">
              <FileText className="size-4" />
            </button>
          ) : null}
          <button type="button" className={pillBtn} onClick={fresh} disabled={!!live} aria-label="Ấn phẩm mới" title="Ấn phẩm mới">
            <MessageSquarePlus className="size-4" />
          </button>
          </HeaderGroup>
          {frameActions}
        </header>
        {tabs}
        <Conversation doc={doc} live={live} local={local} docs={docs} showDraft={hasDraft ? showDraft : undefined} draftOpen={draftOpen} onRetry={(t, retry) => void send(t.text, { block: t.block, mode: t.mode, retry })}
                      onExample={(t) => void send(t)} onContinue={() => void send(CONTINUE)}
                      onFit={(b) => { if (b.action) void send(b.action.input.instruction, { block: b.action.input.block, mode: b.action.input.mode }); }}
                      fromRecord={<FromRecord r={recipe} />} kinds={doc.kinds} kind={kind} setKind={setKind} />
        <ChatInput doc={doc} hasDraft={hasDraft} recipe={recipe} running={!!live} stopping={stopping} canStop={!!live?.sessionId}
                   onSend={(t) => void send(t)} onStop={() => void stop()} />
      </section>

      {hasDraft ? (
        <>
          {panes ? (
            <div role="separator" aria-orientation="vertical" aria-label="Kéo để đổi bề ngang ấn phẩm (hoặc dùng phím mũi tên trái / phải)" tabIndex={0}
                 aria-valuemin={30} aria-valuemax={75} aria-valuenow={Math.round(pct)}
                 onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)} onPointerMove={drag}
                 onKeyDown={(e) => {
                   if (e.key === "ArrowLeft") setPct((v) => Math.min(75, v + 3));
                   else if (e.key === "ArrowRight") setPct((v) => Math.max(30, v - 3));
                   else return;
                   e.preventDefault();
                 }}
                 className="group relative z-10 -mx-1 hidden w-2 shrink-0 cursor-col-resize justify-center outline-none @3xl:flex">
              <span className="w-px bg-hairline transition-colors group-hover:bg-primary group-focus-visible:w-0.5 group-focus-visible:bg-primary" />
            </div>
          ) : null}
          <section className={cn("min-h-0 min-w-0 flex-1 flex-col bg-canvas", view === "draft" ? "flex" : "hidden", panes ? "@3xl:flex" : "@3xl:hidden")}
                   aria-label="Khung làm việc — ấn phẩm">
            <header className={cn(PANE_HEADER, "px-3 @3xl:px-6")}>
              <div className="min-w-0 flex-1">
                <h2 className="t16 truncate">{title}</h2>
                <p className="t12 truncate text-muted-foreground">
                  {doc.kind_label}{doc.version ? ` · bản ${doc.version}` : ""}{live ? " · AI đang viết…" : doc.status === "stopped" ? " · đã dừng" : ""}
                </p>
              </div>
              {docs}
              <span className="contents @3xl:hidden">{frameActions}</span>   {/* hẹp: ngăn trò chuyện (có nút của khung) đang ẩn ⇒ nút khung đi theo ngăn đang thấy */}
              <button type="button" onClick={() => { setDraftOpen(false); setView("chat"); }} aria-label="Đóng ấn phẩm" title="Đóng ấn phẩm (chỉ ẩn ngăn — bản vẫn được lưu)"
                      className="hidden size-8 shrink-0 place-items-center rounded-md text-muted-foreground outline-none hover:bg-foreground/5 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring @3xl:grid">
                <X className="size-4" />
              </button>
            </header>
            {tabs}
            <div className="flex min-h-0 flex-1 flex-col p-3 @3xl:p-4">
              <DocumentPanel doc={doc} live={live} busy={busy}
                             onRevise={(b, instruction, mode) => { setView("chat"); void send(instruction, { block: b, mode }); }}
                             onEdit={(b, text) => void act(c.edit, { session_id: doc.session_id, block: b, text }, "Đang lưu…")}
                             onRemove={(b) => void act(c.remove, { session_id: doc.session_id, block: b }, "Đang xoá khối…")}
                             onRefresh={() => void act(c.refresh, { session_id: doc.session_id }, "Đang chạy lại các truy vấn…")}
                             onRestore={(v) => void act(c.restore, { session_id: doc.session_id, version: v }, "Đang quay lại…")}
                             onExport={(f) => void exportAs(f)} onDownload={(id) => void download(id)}
                             onLayout={(template) => void act(c.set_layout, { session_id: doc.session_id, template }, "Đang đổi trang mẫu…")}
                             onSlot={(b, slot) => void act(c.set_slot, { session_id: doc.session_id, block: b, slot }, "Đang chuyển khối…")}
                             onCover={(b) => void act(c.set_cover, { session_id: doc.session_id, block: b }, "Đang ghim ảnh bìa…")}
                             onShare={async (recipient, days) => {
                               const out = await act(c.share, { session_id: doc.session_id, recipient, days }, "Đang tạo link…");
                               return out && typeof out.url === "string" ? out.url : null;
                             }}
                             onUnshare={(id) => void act(c.unshare, { share_id: id }, "Đang thu hồi link…")}
                             onDuplicate={async (leadId) => {
                               const out = await run(c.duplicate, { session_id: doc.session_id, lead_id: leadId }, "Đang làm bản cho khách…");
                               if (out && typeof out.session_id === "string") await reload(out.session_id);
                             }} />
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}

// ── Bố cục tờ giới thiệu (0224): máy chủ xếp khối vào phần (bìa · dòng chảy · cột · ô nổi bật · lưới ảnh · chân); web chỉ vẽ theo `doc.layout.sections` ──

const DARK_BAND = "color-mix(in oklab, var(--brand) 82%, #000)";   // nền dải màu: pha tối để chữ trắng luôn đọc được dù màu thương hiệu sáng
const trackStyle = (cols: number, min = "10rem"): React.CSSProperties =>
  ({ gridTemplateColumns: `repeat(auto-fit, minmax(max(${min}, calc(100% / ${cols} - 1rem)), 1fr))` });

function Tiles({ items, color }: { items: { label: string; value: string }[]; color: string }) {
  return (
    <ul className="grid gap-2" style={trackStyle(Math.min(items.length || 1, 4), "9.5rem")}>
      {items.map((it, i) => (
        <li key={i} className="flex min-w-0 flex-col gap-0.5 rounded-xl border-t-4 bg-foreground/[0.04] px-3 py-2.5" style={{ borderColor: color }}>
          <span className={cn("select-text font-semibold", it.value.length > 14 ? "t14 break-words" : it.value.length > 9 ? "t16" : "t20")} style={{ color }}>{it.value}</span>
          <span className="t12 text-muted-foreground">{it.label}</span>
        </li>
      ))}
    </ul>
  );
}

function LayoutBody({ doc, title, color, busy, onRevise, onEdit, onRemove, onSlot, onCover }: {
  doc: ComposerData; title: string; color: string; busy: boolean;
  onRevise: (block: string, instruction: string, mode: "rewrite" | "insert_after") => void; onEdit: (block: string, text: string) => void;
  onRemove: (block: string) => void; onSlot: (block: string, slot: string) => void; onCover: (block: string) => void;
}) {
  const by = new Map(doc.blocks.map((b) => [b.id, b]));
  const row = (id: string, extra: { view?: React.ReactNode; imgClass?: string; onDark?: boolean } = {}) => {
    const b = by.get(id);
    if (!b) return null;
    return (
      <BlockRow key={id} b={b} color={color} busy={busy} kind={doc.kind} slots={doc.layout.slots} onSlot={(slot) => onSlot(id, slot)} onCover={() => onCover(id)}
                onRevise={(instruction, mode) => onRevise(id, instruction, mode)} onEdit={(text) => onEdit(id, text)} onRemove={() => onRemove(id)} {...extra} />
    );
  };
  const part = (s: ComposerSection, i: number): React.ReactNode => {
    switch (s.kind) {
      case "cover":
        return (
          <section key={i} aria-label="Bìa" className="overflow-hidden rounded-xl">
            {s.hero ? row(s.hero, { imgClass: "max-h-80 w-full object-cover" }) : null}
            <div className="flex flex-col gap-1 px-5 py-5 text-white" style={{ background: DARK_BAND }}>
              <h2 className="t20 font-semibold">{title}</h2>
              {s.text.map((id) => row(id, { onDark: true }))}
            </div>
          </section>
        );
      case "flow":
        return <div key={i} className="flex flex-col gap-1">{s.ids.map((id) => row(id))}</div>;
      case "columns":
        return (
          <div key={i} className="grid gap-4" style={trackStyle(s.cols, "12rem")}>
            {s.columns.map((col, j) => <div key={j} className="flex min-w-0 flex-col gap-1">{col.map((id) => row(id))}</div>)}
          </div>
        );
      case "tiles":
        return <div key={i} className="flex flex-col gap-1">{s.ids.map((id) => row(id, { view: <Tiles items={s.items.filter((t) => t.block === id)} color={color} /> }))}</div>;
      case "grid":
        return (
          <div key={i} className="grid gap-2" style={trackStyle(s.cols, "9rem")}>
            {s.ids.map((id) => row(id, { imgClass: "aspect-[4/3] w-full rounded-lg object-cover" }))}
          </div>
        );
      case "footer":
        return (
          <footer key={i} aria-label="Liên hệ" className="flex flex-col gap-1 rounded-xl px-5 py-4 text-white" style={{ background: DARK_BAND }}>
            {s.ids.map((id) => row(id, { onDark: true, view: s.lines[id]?.length ? <div className="flex flex-col gap-0.5">{s.lines[id].map((t, j) => <p key={j} className="t14 select-text">{t}</p>)}</div> : undefined }))}
          </footer>
        );
    }
  };
  return <div className="flex flex-col gap-3" data-layout={doc.layout.template}>{doc.layout.sections.map(part)}</div>;
}

/** Chia sẻ qua link: trang web chụp lúc bấm, hết hạn / thu hồi được, hiện số lượt khách mở. Link chỉ hiện ĐÚNG LẦN TẠO (máy chủ chỉ giữ mã băm). */
function SharePanel({ doc, locked, onShare, onUnshare }: {
  doc: ComposerData; locked: boolean; onShare: (recipient: string, days: number) => Promise<string | null>; onUnshare: (id: string) => void;
}) {
  const [recipient, setRecipient] = useState("");
  const [days, setDays] = useState(30);
  const [url, setUrl] = useState("");
  const [copied, setCopied] = useState(false);
  const blocked = doc.stats.flagged > 0 || doc.stats.placeholders > 0;
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-hairline bg-surface p-3" aria-label="Chia sẻ qua link">
      <p className="t12 text-muted-foreground">
        Khách mở link xem trang web của bản này, không cần đăng nhập. Trang là ảnh chụp lúc bấm “Tạo link”: sửa ấn phẩm sau đó không đổi trang đã gửi.
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="t12 flex min-w-[10rem] flex-1 flex-col gap-1">Gửi cho (để bạn nhớ)
          <input value={recipient} maxLength={120} onChange={(e) => setRecipient(e.target.value)} placeholder="Vd: Chị Lan"
                 className="t14 h-8 rounded-lg border border-border bg-surface px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring" />
        </label>
        <label className="t12 flex flex-col gap-1">Có hạn
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="t14 h-8 rounded-lg border border-border bg-surface px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {[7, 14, 30, 60].map((d) => <option key={d} value={d}>{d} ngày</option>)}
          </select>
        </label>
        <button type="button" disabled={locked || blocked} className={cn(actionClass("primary"), "h-8")}
                title={blocked ? "Còn số không khớp hoặc chỗ “cần số liệu” — xử lý trước khi gửi ra ngoài" : "Tạo link chia sẻ"}
                onClick={() => { void onShare(recipient.trim(), days).then((u) => { if (u) { setUrl(u); setCopied(false); } }); }}>
          <Link2 className="size-3.5" aria-hidden /> Tạo link
        </button>
      </div>
      {blocked ? <p className="t12 text-bad" role="note">Còn số không khớp hoặc chỗ “cần số liệu” trong bản — sửa trước khi gửi ra ngoài công ty.</p> : null}
      {url ? (
        <div className="flex flex-col gap-1 rounded-lg bg-surface2 p-2">
          <div className="flex items-center gap-2">
            <input readOnly value={url} aria-label="Link chia sẻ" onFocus={(e) => e.currentTarget.select()}
                   className="t12 h-8 min-w-0 flex-1 rounded-md border border-border bg-surface px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring" />
            <button type="button" className={cn(actionClass("default"), "h-8")}
                    onClick={() => { void navigator.clipboard?.writeText(url).then(() => setCopied(true)); }}>
              {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />} {copied ? "Đã chép" : "Chép link"}
            </button>
          </div>
          <p className="t12 text-muted-foreground" role="status">Link chỉ hiện lần này — hãy chép ngay. Mất link thì tạo link mới.</p>
        </div>
      ) : null}
      {doc.shares.length ? (
        <ul className="flex flex-col gap-1" aria-label="Các lần đã chia sẻ">
          {doc.shares.map((x) => (
            <li key={x.id} className="t12 flex items-center gap-2 rounded-lg px-2 py-1 hover:bg-foreground/5">
              <span className="min-w-0 flex-1 truncate">{x.recipient || "(không ghi tên)"} <span className="text-muted-foreground">· bản {x.version} · {datetime(x.at)}</span></span>
              <span className="tnum shrink-0">{x.views} lượt xem</span>
              <span className={cn("shrink-0", x.active ? "text-ok" : "text-muted-foreground")}>{x.active ? "đang mở" : "đã đóng"}</span>
              {x.active ? <button type="button" disabled={locked} onClick={() => onUnshare(x.id)} className="shrink-0 rounded px-1.5 py-0.5 text-bad hover:bg-bad/10">Thu hồi</button> : null}
            </li>
          ))}
        </ul>
      ) : <p className="t12 text-muted-foreground">Chưa chia sẻ lần nào.</p>}
    </div>
  );
}

// ── Tài liệu đã soạn (như danh sách cuộc trò chuyện) ──

/** "Tài liệu đã soạn": các bản soạn của chính người dùng, mới sửa trước — mở lại một bản (trò chuyện + bản soạn), xoá bản không dùng nữa. */
function HistoryMenu({ doc, disabled, onOpen, onNew, onDelete }: {
  doc: ComposerData; disabled: boolean; onOpen: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setConfirm(null); }}>
      <PopoverTrigger className={cn(actionClass("default"), "h-8 shrink-0 px-2.5")} aria-label="Ấn phẩm đã làm" title="Ấn phẩm đã làm">
        <History className="size-4" aria-hidden />
        <span className="@max-md:sr-only">Ấn phẩm đã làm</span>
        {doc.history.length ? <span className="t12m rounded-full bg-primary/12 px-1.5 text-primary">{doc.history.length}</span> : null}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(22rem,calc(100vw-1.5rem))] gap-1.5 p-2">
        <button type="button" onClick={() => { setOpen(false); onNew(); }} disabled={disabled} className={cn(actionClass("primary"), "h-9 w-full justify-start")}>
          <MessageSquarePlus className="size-4" aria-hidden /> Ấn phẩm mới
        </button>
        <ul className="flex max-h-[min(60vh,420px)] flex-col gap-0.5 overflow-y-auto" aria-label="Ấn phẩm đã làm">
          {doc.history.length ? null : <li className="t12 px-2 py-2 text-muted-foreground">Chưa có ấn phẩm nào.</li>}
          {doc.history.map((h) => {
            const current = h.id === doc.session_id;
            return (
              <li key={h.id} className={cn("group flex items-start gap-1 rounded-lg", current ? "bg-primary/12" : "hover:bg-foreground/5")}>
                <button type="button" onClick={() => { setOpen(false); onOpen(h.id); }} disabled={disabled} aria-current={current ? "true" : undefined}
                        className="flex min-w-0 flex-1 flex-col rounded-lg px-2 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <span className={cn("t14 truncate", current && "t14m")}>{h.title}</span>
                  <span className="t12 flex items-center gap-1 truncate text-muted-foreground">
                    {h.status === "failed" ? <CircleAlert className="size-3 shrink-0 text-warn" aria-hidden /> : null}
                    {h.status === "drafting" ? <span className="block size-2.5 shrink-0 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-violet" aria-hidden /> : null}
                    {h.status === "ready" ? h.kind_label : h.status_label} · {datetime(h.at).slice(0, 16)}
                  </span>
                </button>
                {confirm === h.id ? (
                  <span className="flex shrink-0 items-center gap-1 py-1.5 pr-1">
                    <button type="button" onClick={() => { setConfirm(null); onDelete(h.id); }} className={cn(actionClass("destructive"), "h-6 px-2 text-[12px]")}>Xoá</button>
                    <button type="button" onClick={() => setConfirm(null)} className="t12m h-6 rounded-md px-1.5 text-muted-foreground hover:bg-accent">Thôi</button>
                  </span>
                ) : (
                  <button type="button" onClick={() => setConfirm(h.id)} disabled={disabled} aria-label={`Xoá ấn phẩm ${h.title}`} title="Xoá ấn phẩm"
                          className="mt-1.5 mr-1 shrink-0 rounded p-1 text-muted-foreground hover:bg-bad/10 hover:text-bad focus-visible:ring-2 focus-visible:ring-ring">
                    <Trash2 className="size-3.5" aria-hidden />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

// ── Trò chuyện ──

const SELECT = "t14 h-9 w-full rounded-lg border border-border bg-surface px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60";

/**
 * Làm ấn phẩm TỪ HỒ SƠ (ADR 0249): công thức (theo loại đang chọn) → hồ sơ trong danh sách server trả theo quyền → khách (tuỳ chọn). "Thêm ý" CHÍNH LÀ ô trò chuyện:
 * đã chọn đủ hồ sơ thì Enter ở ô chat = dựng (chữ gõ vào thành ý thêm, bỏ trống cũng dựng được). Số lấy từ hồ sơ, AI chỉ viết chữ. Danh sách / lệnh đều do server nêu
 * (`commands.recipe` · `subjects` · `customers`) — web chỉ vẽ ô chọn. Trạng thái nằm ở đây để hai nơi (hàng chọn trong hội thoại, ô chat) dùng chung.
 */
function useRecipe(doc: ComposerData, kind: string, disabled: boolean, onBuilt: (sessionId: string) => void) {
  const desk = useDesk();
  const c = doc.commands;
  const recipes = doc.recipes.filter((r) => r.piece === kind);
  const [picked, setPicked] = useState("");
  const recipe = recipes.find((r) => r.key === picked) ?? recipes[0];
  const [subject, setSubject] = useState("");
  const [customer, setCustomer] = useState("");
  const [subjects, setSubjects] = useState<Option[] | null>(null);
  const [customers, setCustomers] = useState<Option[] | null>(null);
  const [busy, setBusy] = useState(false);
  const key = recipe?.key ?? "";
  const needs = !!recipe?.needs_subject;
  const takesCustomer = !!recipe?.customer;
  const [seenKey, setSeenKey] = useState(key);
  if (seenKey !== key) {   // đổi công thức (hoặc đổi loại ở hàng chip) ⇒ hồ sơ đã chọn không còn hợp
    setSeenKey(key);
    setSubject("");
    setSubjects(null);
  }
  useEffect(() => {
    if (!key || !needs || !c.subjects) return;
    let live = true;
    api.options(c.subjects, { kind: key }).then((o) => { if (live) setSubjects(o); }, () => { if (live) setSubjects([]); });
    return () => { live = false; };
  }, [key, needs, c.subjects]);
  useEffect(() => {
    if (!takesCustomer || !c.customers || customers) return;
    api.options(c.customers).then(setCustomers, () => setCustomers([]));
  }, [takesCustomer, c.customers, customers]);
  const available = !!recipe && !!c.recipe;
  const ready = available && (!needs || !!subject) && !disabled && !busy;
  async function build(extra = "") {
    if (!recipe || !c.recipe || !ready) return;
    setBusy(true);
    try {
      const r = await api.command(c.recipe, { kind: recipe.key, subject, ...(customer ? { lead_id: customer } : {}), ...(extra.trim() ? { extra: extra.trim() } : {}) }, newKey());
      const out = r.output as { session_id?: string } | null;
      if (r.status !== "ok" || !out?.session_id) desk.show(r.error || `Không dựng được (${r.status})`, true);
      else onBuilt(String(out.session_id));
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không dựng được", true);
    } finally {
      setBusy(false);
    }
  }
  return { available, ready, busy, recipes, recipe, needs, takesCustomer, subject, setSubject, subjects, customer, setCustomer, customers, setPicked, build };
}
type Recipe = ReturnType<typeof useRecipe>;

/** Hàng "làm từ hồ sơ" trong hội thoại (như câu hỏi của trợ lý): chip công thức, ô chọn hồ sơ / khách; dựng bằng Enter ở ô chat (thêm ý nếu muốn). */
function FromRecord({ r }: { r: Recipe }) {
  if (!r.available || !r.recipe) return null;
  const dis = r.busy;
  return (
    <div className="flex flex-col gap-2" role="group" aria-label="Làm từ hồ sơ">
      <p className="t12m text-muted-foreground">Làm từ hồ sơ — số lấy từ hồ sơ, AI chỉ viết chữ</p>
      {r.recipes.length > 1 ? (
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Công thức">
          {r.recipes.map((x) => (
            <button key={x.key} type="button" role="radio" aria-checked={r.recipe?.key === x.key} disabled={dis} title={x.about} onClick={() => r.setPicked(x.key)}
                    className={cn("t12 rounded-full border px-2.5 py-1.5", r.recipe?.key === x.key ? "border-primary bg-primary text-white" : "border-border bg-surface hover:bg-accent")}>
              {x.label}
            </button>
          ))}
        </div>
      ) : <p className="t14">{r.recipe.label}{r.recipe.about ? <span className="t12 text-muted-foreground"> · {r.recipe.about}</span> : null}</p>}
      <div className="flex flex-wrap gap-2">
        {r.needs ? (
          <select value={r.subject} disabled={dis || r.subjects === null} onChange={(e) => r.setSubject(e.target.value)} aria-label="Hồ sơ" className={cn(SELECT, "min-w-0 flex-1 basis-52")}>
            <option value="">{r.subjects === null ? "Đang tải…" : r.subjects.length ? "Chọn hồ sơ" : "Chưa có hồ sơ nào bạn được xem"}</option>
            {(r.subjects ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        ) : null}
        {r.takesCustomer ? (
          <select value={r.customer} disabled={dis} onChange={(e) => r.setCustomer(e.target.value)} aria-label="Cho khách (không bắt buộc)" className={cn(SELECT, "min-w-0 flex-1 basis-52")}>
            <option value="">Không riêng khách nào</option>
            {(r.customers ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        ) : null}
      </div>
      <p className="t12 text-muted-foreground">
        {r.busy ? "Đang dựng…" : r.ready ? "Gõ thêm ý rồi Enter (hoặc bấm gửi để dựng luôn)" : r.needs ? "Chọn hồ sơ để dựng" : ""}
      </p>
    </div>
  );
}

/** Hẹp (< @3xl): một ngăn một lúc — hai thẻ luôn nhìn thấy, không nút nổi che nội dung. Rộng: ẩn (hai ngăn cạnh nhau). */
function ViewTabs({ view, onView, fresh }: { view: "chat" | "draft"; onView: (v: "chat" | "draft") => void; fresh: boolean }) {
  const tab = (v: "chat" | "draft", label: string) => (
    <button type="button" role="tab" aria-selected={view === v} onClick={() => onView(v)}
            className={cn("t14m relative inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          view === v ? "bg-surface text-foreground" : "text-muted-foreground hover:text-foreground")}>
      {label}
      {v === "draft" && fresh && view !== "draft" ? <span className="size-1.5 rounded-full bg-primary" aria-label="đang có số liệu mới" /> : null}
    </button>
  );
  return (
    <div role="tablist" aria-label="Trò chuyện hoặc bản soạn" className="mx-3 mb-2 flex shrink-0 gap-0.5 rounded-full bg-surface2 p-0.5 @3xl:hidden">
      {tab("chat", "Trò chuyện")}
      {tab("draft", "Bản soạn")}
    </div>
  );
}

function Conversation({ doc, live, local, docs, showDraft, draftOpen, onRetry, onExample, onContinue, onFit, fromRecord, kinds, kind, setKind }: {
  doc: ComposerData; live: LiveTurn | null; local: { user: LiveTurn; error: string } | null; docs: React.ReactNode;
  showDraft?: () => void; draftOpen: boolean;   // có bản soạn ⇒ lượt xong có nút "Xem bản soạn" khi bản soạn đang ẩn (hẹp: ở thẻ Trò chuyện · rộng: ngăn đã đóng)
  onRetry: (t: { text: string; block: string; mode: string }, retry: boolean) => void; onExample: (text: string) => void; onContinue: () => void;
  /** nút "Rút gọn khối này" ở lời báo tràn: chạy đúng lệnh server đã viết sẵn cho khối đó (compose.revise qua luồng — cùng cửa executor), rồi server tự đo lại */
  onFit: (b: ComposerOverflowBlock) => void;
  fromRecord: React.ReactNode; kinds: ComposerData["kinds"]; kind: string; setKind: (k: string) => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  const retry = retryTarget(doc.turns);
  const empty = !doc.turns.length && !live && !local;
  const over = live ? [] : currentOverflow(doc.turns, doc.version);   // đang chạy lượt mới ⇒ lời báo cũ sắp đổi, không vẽ nút
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [doc.turns.length, live, local]);
  return (
    // cùng khoảng cách với hội thoại của trợ lý (px-4 py-3.5, gap-3.5); chiếm cả bề ngang thì giữ cột chữ ở giữa cho dễ đọc
    <div className="relative min-h-0 flex-1">
      <div className="h-full overflow-y-auto px-4 py-3.5" role="log" aria-live="polite" aria-label="Hội thoại với AI soạn">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-3.5">
          {empty ? (
            <div className="flex flex-col gap-3.5 pt-2">
              <div className="flex items-center justify-between gap-3">
                <h2 className="t20">Bạn cần làm ấn phẩm gì?</h2>
                {doc.history.length ? docs : null}
              </div>
              <p className="t14 text-muted-foreground">
                Tờ rơi, tờ giới thiệu, slide hay bài đăng. Chọn hồ sơ để làm từ dữ liệu thật — hoặc nói ngắn gọn cần gì. Ấn phẩm mở bên cạnh. Công văn, hợp đồng: dùng phân hệ Giấy tờ.
              </p>
              <div className="flex flex-col gap-2">
                <p className="t12m text-muted-foreground">Loại ấn phẩm</p>
                <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Loại ấn phẩm">
                  {kinds.map((k) => (
                    <button key={k.key} type="button" role="radio" aria-checked={kind === k.key} onClick={() => setKind(k.key)}
                            className={cn("t12 rounded-full border px-2.5 py-1.5", kind === k.key ? "border-primary bg-primary text-white" : "border-border bg-surface hover:bg-accent")}>
                      {k.label}
                    </button>
                  ))}
                </div>
              </div>
              {fromRecord}
              <div className="flex flex-col gap-2">
                <p className="t12m text-muted-foreground">Hoặc nói AI cần gì</p>
                {EXAMPLES.map((e) => (
                  <button key={e} type="button" onClick={() => onExample(e)}
                          className="t14 flex items-center justify-between gap-2 rounded-[18px] bg-incoming px-3.5 py-2.5 text-left outline-none hover:brightness-95 focus-visible:ring-2 focus-visible:ring-ring">
                    {e}
                    <ArrowUp className="size-3 shrink-0 rotate-45 text-muted-foreground" aria-hidden />
                  </button>
                ))}
              </div>
              {doc.note ? <p className="t12 text-muted-foreground">{doc.note}</p> : null}
            </div>
          ) : null}
          {doc.turns.map((t, i) => (
            <TurnBubble key={i} t={t} onRetry={i === doc.turns.length - 1 && retry && !live ? () => onRetry(retry, true) : undefined}
                        onContinue={i === doc.turns.length - 1 && t.partial && !live ? onContinue : undefined}
                        onShowDraft={i === doc.turns.length - 1 && t.version && showDraft ? showDraft : undefined} draftOpen={draftOpen}
                        overflow={i === doc.turns.length - 1 ? over : undefined} onFit={onFit} fitting={!!doc.fitting} />
          ))}
          {doc.status === "drafting" && !live ? <Working status="Em đang soạn — tự cập nhật, không cần mở lại" steps={[]} /> : null}
          {doc.fitting && doc.status !== "drafting" && !live ? (
            <Working status={doc.fitting === "shorten" ? "Trang tràn khung — AI đang rút gọn chữ cho vừa…" : "Đang kiểm các trang có vừa khung in không…"} steps={[]} />
          ) : null}
          {local ? (
            <>
              <UserBubble text={local.user.text} block={local.user.block} mode={local.user.mode} />
              <Failed text={local.error} onRetry={() => onRetry(local.user, false)} />
            </>
          ) : null}
          {live ? (
            <>
              <UserBubble text={live.text} block={live.block} mode={live.mode} />
              <Working status={live.status} steps={live.steps} />
            </>
          ) : null}
          <div ref={end} />
        </div>
      </div>
    </div>
  );
}

function UserBubble({ text, block, mode }: { text: string; block: string; mode: string }) {
  return (
    <div className="flex justify-end pl-12">
      <div className="flex max-w-full flex-col items-end gap-1">
        {block ? <span className="t12 text-muted-foreground">{mode === "insert_after" ? "Chèn sau" : "Viết lại"} khối {block}</span> : null}
        <div className="max-w-full rounded-[18px] bg-cold px-3 py-1.5 text-white">
          <p className="t14 select-text whitespace-pre-wrap break-words">{text}</p>
        </div>
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const desk = useDesk();
  return (
    <button type="button" aria-label="Sao chép" title="Sao chép"
            onClick={() => { void navigator.clipboard?.writeText(text).then(() => desk.show("Đã sao chép"), () => desk.show("Không sao chép được", true)); }}
            className="grid size-6 place-items-center rounded-md text-muted-foreground outline-none hover:bg-foreground/5 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring">
      <Copy className="size-3.5" aria-hidden />
    </button>
  );
}

function TurnBubble({ t, onRetry, onContinue, onShowDraft, draftOpen, overflow, onFit, fitting }: {
  t: ComposerTurn; onRetry?: () => void; onContinue?: () => void; onShowDraft?: () => void; draftOpen?: boolean;
  overflow?: ComposerOverflowPage[]; onFit?: (b: ComposerOverflowBlock) => void; fitting?: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (t.role === "user") return <UserBubble text={t.text} block={t.block} mode={t.mode} />;
  if (t.status === "failed") return <Failed text={t.text} steps={t.steps} onRetry={onRetry} />;
  const stopped = t.status === "stopped";
  return (
    <div className="group flex flex-col gap-2 px-1">
      <p className="t14 flex items-start gap-2 select-text">
        {stopped ? <Square className="mt-1 size-3.5 shrink-0 text-muted-foreground" fill="currentColor" strokeWidth={0} aria-hidden />
          : t.partial ? <CircleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden /> : <Check className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden />}
        <span>{t.text}</span>
      </p>
      {onContinue || onRetry || onShowDraft ? (
        <div className="ml-6 flex flex-wrap gap-2">
          {onShowDraft ? (
            // rộng + ngăn đang mở ⇒ bản soạn vốn nằm bên cạnh: nút chỉ hiện khi hẹp (bản soạn ở thẻ kia) hoặc ngăn đã đóng
            <button type="button" onClick={onShowDraft} className={cn(actionClass(onContinue ? "default" : "primary"), "h-8 w-fit px-3", draftOpen && "@3xl:hidden")}>
              <FileText className="size-3.5" aria-hidden /> Xem bản soạn
            </button>
          ) : null}
          {onContinue ? (
            <button type="button" onClick={onContinue} className={cn(actionClass("primary"), "h-8 w-fit px-3")}>
              <Sparkles className="size-3.5" aria-hidden /> Tiếp tục
            </button>
          ) : null}
          {onRetry ? (
            <button type="button" onClick={onRetry} className={cn(actionClass("default"), "h-8 w-fit px-3")}>
              <RotateCcw className="size-3.5" aria-hidden /> Thử lại
            </button>
          ) : null}
        </div>
      ) : null}
      {overflow?.length && onFit ? <OverflowActions pages={overflow} onFit={onFit} busy={!!fitting} /> : null}
      {t.steps.length ? (
        <>
          <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
                  className="t12m ml-6 flex w-fit items-center gap-1 rounded-full border border-border bg-surface px-2 py-0.5 outline-none focus-visible:ring-2 focus-visible:ring-ring">
            Đã chạy {t.steps.length} bước
            <ChevronRight className={cn("size-3 text-muted-foreground transition-transform", open && "rotate-90")} aria-hidden />
          </button>
          {open ? <Steps steps={t.steps} className="ml-6" /> : null}
        </>
      ) : null}
      <div className="ml-6 flex items-center gap-1"><CopyButton text={t.text} /></div>
    </div>
  );
}

/** Nút bấm một chạm dưới lời báo "chưa vừa khung in": mỗi trang tràn một dòng, mỗi khối gây tràn một nút (nhãn + lời dặn do server viết sẵn). */
function OverflowActions({ pages, onFit, busy }: { pages: ComposerOverflowPage[]; onFit: (b: ComposerOverflowBlock) => void; busy: boolean }) {
  return (
    <ul className="ml-6 flex flex-col gap-1.5" aria-label="Trang chưa vừa khung in">
      {pages.map((pg) => {
        const acts = overflowActions(pg);
        return (
          <li key={pg.page} className="flex flex-wrap items-center gap-2">
            <span className="t12m text-muted-foreground">Trang {pg.page}{pg.axis === "h" ? ` · tràn ~${pg.lines} dòng` : " · rộng quá khung"}</span>
            {acts.length ? acts.map((b) => (
              <button key={b.id} type="button" disabled={busy} onClick={() => onFit(b)} title={b.label}
                      className={cn(actionClass("default"), "h-7 max-w-full px-2.5 text-[12px]")}>
                <Sparkles className="size-3.5 shrink-0" aria-hidden />
                <span className="truncate">{b.action?.label} · {b.kind}{b.label ? ` “${b.label}”` : ""}</span>
              </button>
            )) : <span className="t12 text-muted-foreground">bớt ảnh / khối hoặc chuyển bớt sang trang khác</span>}
          </li>
        );
      })}
    </ul>
  );
}

function Failed({ text, steps = [], onRetry }: { text: string; steps?: { title: string; status: string; reason?: string }[]; onRetry?: () => void }) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-warn/40 bg-warn/10 px-3 py-2" role="alert">
      <p className="t14 flex items-start gap-2 select-text">
        <CircleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
        <span>{text || "Chưa soạn được — thử lại"}</span>
      </p>
      {steps.length ? <Steps steps={steps} className="ml-6" /> : null}
      {onRetry ? (
        <button type="button" onClick={onRetry} className={cn(actionClass("default"), "ml-6 h-7 w-fit px-2.5 text-[12px]")}>
          <RotateCcw className="size-3.5" aria-hidden /> Thử lại
        </button>
      ) : null}
    </div>
  );
}

function Steps({ steps, className }: { steps: (LiveStep | { title: string; status: string; reason?: string })[]; className?: string }) {
  return (
    <ul className={cn("flex flex-col gap-1", className)}>
      {groupSteps(steps).map((s, i) => (
        <li key={i} className="t12 flex items-center gap-1.5">
          {s.status === "running" ? <span className="block size-3 shrink-0 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary" aria-hidden />
            : s.status === "ok" ? <Check className="size-3.5 shrink-0 text-primary" aria-hidden /> : <CircleAlert className="size-3.5 shrink-0 text-warn" aria-hidden />}
          <span className="truncate">{s.status === "running" ? `Đang lấy số liệu: ${s.title}…` : s.title}</span>
          {s.count > 1 ? <span className="t12m shrink-0 text-muted-foreground">×{s.count}</span> : null}
          {s.status === "error" ? <span className="min-w-0 text-muted-foreground">· không lấy được{s.reason ? `: ${s.reason}` : ""}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function Working({ status, steps }: { status: string; steps: LiveStep[] }) {
  // y như dòng "đang làm" của trợ lý (LiveBubble): lấp lánh + chữ chạy sáng, các bước bên dưới, ba chấm khi chưa có chữ
  return (
    <div className="flex flex-col gap-2 px-1" role="status">
      <p className="flex items-center gap-2">
        <Sparkles className="size-3.5 text-primary" aria-hidden />
        <span className="t12m animate-[barista-shimmer_1.6s_linear_infinite] bg-[linear-gradient(90deg,var(--muted-foreground)_40%,var(--foreground)_50%,var(--muted-foreground)_60%)] bg-[length:200%_100%] bg-clip-text text-transparent">{status}</span>
      </p>
      {steps.length ? <Steps steps={steps} /> : null}
      <span className="flex gap-1 py-1.5" aria-label="Đang soạn">{[0, 1, 2].map((i) => <span key={i} className="size-1.5 rounded-full bg-muted-foreground animate-[barista-dot_1.2s_ease-in-out_infinite]" style={{ animationDelay: `${i * 0.2}s` }} />)}</span>
    </div>
  );
}

function ChatInput({ doc, hasDraft, recipe, running, stopping, canStop, onSend, onStop }: {
  doc: ComposerData; hasDraft: boolean; recipe: Recipe; running: boolean; stopping: boolean; canStop: boolean;
  onSend: (text: string) => void; onStop: () => void;
}) {
  const [draft, setDraft] = useState("");
  const ta = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);
  const fresh = !doc.session_id;
  const fromRecord = fresh && recipe.ready;   // đã chọn đủ hồ sơ ⇒ ô chat là "Thêm ý": Enter dựng từ hồ sơ (bỏ trống cũng được)
  const has = draft.trim().length > 0 || fromRecord;
  const submit = () => {
    if (!has || running || recipe.busy) return;
    if (fromRecord) void recipe.build(draft);
    else onSend(draft);
    setDraft("");
  };
  return (
    <div className="flex flex-col gap-1.5 px-3 pt-1 pb-3">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-1.5">
        <div className={cn(COMPOSER_PILL, "pl-3")}>
            <textarea ref={ta} value={draft} rows={1} autoFocus onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                          e.preventDefault();
                          submit();
                        }
                      }}
                      placeholder={fromRecord ? "Thêm ý (không bắt buộc), Enter để dựng…" : hasDraft ? "Sửa ấn phẩm…" : "Mô tả ấn phẩm cần làm…"}   // ngắn như "Hỏi trợ lý…"; ví dụ dài nằm ở gợi ý màn bắt đầu
                      aria-label={fresh ? "Cần soạn gì" : "Muốn sửa gì"}
                      className={COMPOSER_TEXT} />
            {/* đọc chính tả như ô nhập của trợ lý: chữ chèn vào ô, người xem / sửa rồi tự gửi */}
            <DictateMic onText={(text) => { setDraft((d) => (d.trim() ? `${d.trimEnd()} ${text}` : text)); ta.current?.focus(); }} />
          <SendStopButton canSend={has} running={running} canStop={canStop} stopping={stopping} onSend={submit} onStop={onStop} />
        </div>
      </div>
    </div>
  );
}

// ── Bản soạn ──

function DocumentPanel({ doc, live, busy, onRevise, onEdit, onRemove, onRefresh, onRestore, onExport, onDownload, onLayout, onSlot, onCover, onShare, onUnshare, onDuplicate }: {
  doc: ComposerData; live: LiveTurn | null; busy: string | null;
  onRevise: (block: string, instruction: string, mode: "rewrite" | "insert_after") => void; onEdit: (block: string, text: string) => void;
  onRemove: (block: string) => void; onRefresh: () => void; onRestore: (version: number) => void; onExport: (format: string) => void;
  onDownload: (id: string) => void; onLayout: (template: string) => void; onSlot: (block: string, slot: string) => void; onCover: (block: string) => void;
  onShare: (recipient: string, days: number) => Promise<string | null>; onUnshare: (id: string) => void; onDuplicate: (leadId: string) => Promise<void>;
}) {
  const [showVersions, setShowVersions] = useState(false);
  const [showShare, setShowShare] = useState(false);
  // màu nhận diện công ty (màu in) — nền tối thì pha sáng để đọc được; tệp xuất vẫn dùng đúng màu gốc
  const color = "var(--brand-ink)";
  const locked = !!busy || !!live;
  // đang soạn: bảng vừa lấy hiện trước (bản cũ — nếu có — vẫn thấy tới khi bản mới về)
  const blocks = live?.blocks ?? doc.blocks;
  const ready = doc.blocks.length > 0 && !live?.blocks;
  const title = live?.blocks ? live.title || doc.title : doc.session_id ? doc.title : "Ấn phẩm mới";
  return (
    // như thẻ lớn của khung làm việc (CardFrame): mặt thẻ bo 20, viền mảnh; thanh xuất tệp ở đầu thẻ
    <article className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto rounded-[20px] border border-border bg-surface p-3.5 @3xl:p-[18px]" aria-label="Ấn phẩm">
      {ready ? (
        <div className="flex flex-wrap items-center gap-2">
          {doc.formats.map((f, i) => (   // định dạng hợp loại ấn phẩm (server quyết); đầu = định dạng chính
            <button key={f.key} type="button" disabled={locked || doc.stats.flagged > 0} onClick={() => onExport(f.key)}
                    title={doc.stats.flagged ? "Còn khối có số không khớp số liệu — sửa hoặc xoá trước khi xuất" : `Xuất ${f.label}`}
                    className={cn(actionClass(i === 0 ? "primary" : "default"), "h-8")}>
              <FileDown className="size-3.5" aria-hidden /> {f.label}
            </button>
          ))}
          <button type="button" disabled={locked} onClick={onRefresh} className={cn(actionClass("default"), "h-8")} title="Chạy lại các truy vấn bằng quyền của bạn lúc này">
            <RefreshCw className="size-3.5" aria-hidden /> Cập nhật số liệu
          </button>
          <button type="button" onClick={() => setShowVersions((s) => !s)} aria-expanded={showVersions} className={cn(actionClass("default"), "h-8")}>
            <History className="size-3.5" aria-hidden /> Bản {doc.version}
          </button>
          {doc.layout.templates.length ? (
            <label className="t12 flex items-center gap-1.5 text-muted-foreground">
              Trang mẫu
              <select value={doc.layout.template} disabled={locked} onChange={(e) => onLayout(e.target.value)} aria-label="Trang mẫu của ấn phẩm"
                      className="t12 h-8 rounded-lg border border-border bg-surface px-2 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
                {doc.layout.template ? null : <option value="">Bố cục cũ</option>}
                {doc.layout.templates.map((t) => <option key={t.key} value={t.key} title={t.about}>{t.label}</option>)}
              </select>
            </label>
          ) : null}
          <button type="button" onClick={() => setShowShare((x) => !x)} aria-expanded={showShare} className={cn(actionClass("default"), "h-8")}
                  title="Gửi khách một link xem trang web của bản này (có hạn, thu hồi được, đếm lượt xem)">
            <Link2 className="size-3.5" aria-hidden /> Chia sẻ{doc.shares.some((x) => x.active) ? ` · ${doc.shares.filter((x) => x.active).length}` : ""}
          </button>
          {doc.commands.duplicate && doc.commands.customers ? <DuplicateMenu doc={doc} locked={locked} onDuplicate={onDuplicate} /> : null}
          {busy ? <span className="t12 flex items-center gap-1.5 text-muted-foreground" role="status">
            <span className="block size-3 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary" aria-hidden />{busy}</span> : null}
        </div>
      ) : null}
      {ready && showVersions ? <Versions doc={doc} locked={locked} onRestore={onRestore} onDownload={onDownload} /> : null}
      {ready && showShare ? <SharePanel doc={doc} locked={locked} onShare={onShare} onUnshare={onUnshare} /> : null}

      <div className="flex min-w-0 flex-1 flex-col gap-1 [--brand-ink:var(--brand)] dark:[--brand-ink:color-mix(in_oklab,var(--brand)_45%,white)]"
           style={{ fontFamily: doc.brand.font, "--brand": `#${doc.brand.color}` } as React.CSSProperties}>
        <div className="mb-2 flex items-start gap-3 border-b-4 pb-3" style={{ borderColor: color }}>
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <h2 className="t20" style={{ color }}>{title}</h2>
            {doc.subject.label || doc.customer.label ? (
              <p className="t12 text-muted-foreground">
                {doc.subject.label ? `Về: ${doc.subject.label}` : ""}{doc.subject.label && doc.customer.label ? " · " : ""}{doc.customer.label ? `Cho: ${doc.customer.label}` : ""}
              </p>
            ) : null}
          </div>
          {doc.brand.logo ? (
            // eslint-disable-next-line @next/next/no-img-element -- logo nhỏ server gửi dạng data URL
            <img src={doc.brand.logo} alt="Logo công ty" className="h-10 w-auto shrink-0" />
          ) : null}
        </div>
        {ready || live?.blocks ? (
          <p className={cn("t12 mb-1", doc.stats.flagged && !live ? "text-bad" : "text-muted-foreground")}>
            {doc.kind_label} · {doc.stats.data} bảng số liệu (số chỉ in từ đây)
            {doc.stats.flagged && !live ? ` · ${doc.stats.flagged} khối có số không khớp số liệu — chưa xuất được` : ""}
            {doc.stats.placeholders && !live ? ` · ${doc.stats.placeholders} chỗ “cần số liệu” còn trống` : ""}
          </p>
        ) : null}
        {live && !live.blocks ? (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-2" aria-label="AI đang viết">
              {[0.9, 0.7, 0.8].map((w, i) => <span key={i} className="h-3 animate-pulse rounded-full bg-foreground/[0.06]" style={{ width: `${w * 100}%` }} />)}
            </div>
            {live.data.map((b) => <DataView key={b.id} b={b} />)}
          </div>
        ) : null}
        {!live && !blocks.length ? (
          <div className="grid place-items-center rounded-xl border border-dashed border-border px-4 py-16 text-center">
            <p className="t14 max-w-sm text-muted-foreground">
              {doc.status === "failed" ? "Chưa có nội dung — xem lời báo bên khung trò chuyện rồi bấm “Thử lại”." : "Ấn phẩm sẽ hiện ở đây khi AI viết xong. Bảng số liệu hiện ngay khi lấy được."}
            </p>
          </div>
        ) : null}
        {(!live || live.blocks) && !live?.blocks && doc.layout.html ? (
          // mẫu nhiều trang: đúng trang HTML của bản gửi / PDF — sandbox rỗng (không script, không điều hướng)
          <iframe title="Xem trước ấn phẩm" srcDoc={doc.layout.html} sandbox="" className="h-[70vh] w-full rounded-lg border border-hairline bg-surface2" />
        ) : null}
        {(!live || live.blocks) && !live?.blocks && !doc.layout.html && doc.layout.template && doc.layout.sections.length ? (
          <LayoutBody doc={doc} title={title} color={color} busy={locked || !ready} onRevise={onRevise} onEdit={onEdit} onRemove={onRemove} onSlot={onSlot} onCover={onCover} />
        ) : null}
        {(!live || live.blocks) && !(!live?.blocks && (doc.layout.html || (doc.layout.template && doc.layout.sections.length))) && blocks.map((b) => (
          <BlockRow key={b.id} b={b} color={color} busy={locked || !ready} kind={doc.kind}
                    onRevise={(instruction, mode) => onRevise(b.id, instruction, mode)} onEdit={(text) => onEdit(b.id, text)} onRemove={() => onRemove(b.id)} />
        ))}
        {doc.brand.footer ? <p className="t12 mt-4 border-t border-hairline pt-2 text-center text-muted-foreground">{doc.brand.footer}</p> : null}
      </div>
    </article>
  );
}

/** "Làm bản cho khách khác": chọn khách (danh sách server trả theo quyền) ⇒ ấn phẩm mới riêng cho khách đó — dựng lại từ hồ sơ nếu ấn phẩm làm từ hồ sơ. */
function DuplicateMenu({ doc, locked, onDuplicate }: { doc: ComposerData; locked: boolean; onDuplicate: (leadId: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<Option[] | null>(null);
  const [lead, setLead] = useState("");
  const query = doc.commands.customers ?? "";
  useEffect(() => {
    if (!open || options || !query) return;
    api.options(query).then(setOptions, () => setOptions([]));
  }, [open, options, query]);
  const choices = (options ?? []).filter((o) => o.value !== doc.customer.id);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger disabled={locked} className={cn(actionClass("default"), "h-8")}
                      title={doc.from_record ? "Dựng lại từ hồ sơ cho một khách khác (số, lời chào theo khách đó)" : "Chép ấn phẩm này cho một khách khác"}>
        <Copy className="size-3.5" aria-hidden /> Làm bản cho khách khác
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(20rem,calc(100vw-1.5rem))] gap-2 p-3">
        <label className="t12 flex flex-col gap-1">Cho khách
          <select value={lead} onChange={(e) => setLead(e.target.value)} disabled={options === null} className={SELECT}>
            <option value="">{options === null ? "Đang tải…" : choices.length ? "Chọn khách" : "Chưa có khách nào bạn được xem"}</option>
            {choices.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <button type="button" disabled={!lead || locked} className={cn(actionClass("primary"), "h-8 self-start")}
                onClick={() => { setOpen(false); void onDuplicate(lead).then(() => setLead("")); }}>
          {doc.from_record ? "Dựng bản mới" : "Chép thành bản mới"}
        </button>
      </PopoverContent>
    </Popover>
  );
}

function Versions({ doc, locked, onRestore, onDownload }: { doc: ComposerData; locked: boolean; onRestore: (v: number) => void; onDownload: (id: string) => void }) {
  return (
    <div className="grid gap-3 rounded-xl border border-hairline bg-surface p-3 md:grid-cols-2">
      <section className="flex flex-col gap-1">
        <h3 className="t14m">Phiên bản</h3>
        <ul className="flex max-h-56 flex-col gap-0.5 overflow-y-auto">
          {doc.versions.map((v) => (
            <li key={v.version} className="t12 flex items-start gap-2 rounded-lg px-2 py-1 hover:bg-foreground/5">
              <span className="tnum shrink-0 text-muted-foreground">{v.version}</span>
              <span className="min-w-0 flex-1">{v.note} <span className="text-muted-foreground">· {VIA[v.via] ?? v.via}</span></span>
              {v.version !== doc.version ? (
                <button type="button" title="Quay lại bản này" aria-label={`Quay lại bản ${v.version}`} disabled={locked} onClick={() => onRestore(v.version)}
                        className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground">
                  <RotateCcw className="size-3.5" aria-hidden />
                </button>
              ) : <span className="t12m shrink-0 text-violet">đang xem</span>}
            </li>
          ))}
        </ul>
      </section>
      <section className="flex flex-col gap-1">
        <h3 className="t14m">Tệp đã xuất</h3>
        {doc.exports.length ? (
          <ul className="flex flex-col gap-0.5">
            {doc.exports.map((e) => (
              <li key={e.id}>
                <button type="button" onClick={() => onDownload(e.id)} className="t12 flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left hover:bg-foreground/5">
                  <Download className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="min-w-0 flex-1 truncate">{e.filename}</span>
                  <span className="tnum text-muted-foreground">bản {e.version}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : <p className="t12 text-muted-foreground">Chưa xuất tệp nào.</p>}
      </section>
    </div>
  );
}

/** Khối số liệu: ảnh chụp kết quả truy vấn (kèm nguồn). "Xuất Excel" chạy lại truy vấn gốc bằng quyền người xem lúc này. */
function DataView({ b }: { b: ComposerItem }) {
  const desk = useDesk();
  if (!b.page) return null;
  const src = b.source;
  async function excel() {
    if (!src?.query) return;
    try {
      saveBlob(await api.xlsx(src.query, src.input), `${b.page?.title || src.title || "so-lieu"}.xlsx`);
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Xuất Excel lỗi", true);
    }
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        {b.page.title ? <h4 className="t14m min-w-0 flex-1">{b.page.title}</h4> : <span className="flex-1" />}
        {src?.query ? (
          <button type="button" onClick={() => void excel()} className={cn(actionClass("default"), "h-7 px-2 text-[12px]")}
                  title="Tải bảng gốc ra Excel — chạy lại truy vấn bằng quyền của bạn, số mới nhất">
            <FileSpreadsheet className="size-3.5" aria-hidden /> Xuất Excel
          </button>
        ) : null}
      </div>
      <KpiRow kpis={b.page.kpis} inset />
      {b.page.rows.length ? <div className="max-h-[360px] overflow-auto rounded-xl border border-hairline"><DataTable page={b.page} /></div> : null}
      {b.texts.map((t, i) => (
        <div key={i} className="flex flex-col gap-1">
          {t.title ? <h5 className="t14m">{t.title}</h5> : null}
          <p className="t14 select-text whitespace-pre-line leading-relaxed">{t.text}</p>
        </div>
      ))}
      {b.page.note ? <p className="t12 text-muted-foreground">{b.page.note}</p> : null}
    </div>
  );
}

type Mode = "rewrite" | "insert_after" | "edit" | null;

/** Một khối của bản soạn. Nút thao tác (Viết lại · Chèn số liệu · Sửa tay · Xoá) là THANH NỔI đè lên mép trên bên phải của khối — không chiếm chỗ, không đẩy
 *  nội dung: hiện khi rê chuột, khi bàn phím đang ở trong khối, hoặc khi bấm vào khối (màn cảm ứng); đóng khi rời khối / bấm Esc. Ô nhập lời yêu cầu cũng là
 *  hộp nổi dưới thanh. Sửa tay thì ô chữ thay đúng chỗ chữ đang đứng, "Lưu / Huỷ" nằm ở thanh nổi. */
function BlockRow({ b, color, busy, kind, onRevise, onEdit, onRemove, slots, onSlot, onCover, view, imgClass, onDark }: {
  b: ComposerItem; color: string; busy: boolean; kind: string;
  onRevise: (instruction: string, mode: "rewrite" | "insert_after") => void; onEdit: (text: string) => void; onRemove: () => void;
  /** bố cục (0224): danh sách chỗ đứng + đổi chỗ; `view` thay phần vẽ mặc định (vd ô nổi bật); `imgClass` chỉnh cỡ ảnh theo chỗ */
  slots?: { key: string; label: string }[]; onSlot?: (slot: string) => void; onCover?: () => void; view?: React.ReactNode; imgClass?: string; onDark?: boolean;
}) {
  const [mode, setMode] = useState<Mode>(null);
  const [text, setText] = useState("");
  const [sticky, setSticky] = useState(false);   // bấm vào khối ⇒ thanh ở lại (cảm ứng không có rê chuột)
  if (!["heading", "paragraph", "data", "quote", "image", "placeholder"].includes(b.type)) return null; // khối lạ (server thêm sau) — bỏ qua
  const editable = b.type === "heading" || b.type === "paragraph";
  const close = () => { setMode(null); setSticky(false); };
  const submit = () => {
    if (!text.trim()) return;
    if (mode === "edit") onEdit(text.trim());
    else if (mode === "rewrite" || mode === "insert_after") onRevise(mode === "insert_after" ? `Chèn khối số liệu: ${text.trim()}` : text.trim(), mode);
    close();
  };
  // tiêu đề trên dải màu thương hiệu tối (onDark): kế thừa chữ trắng của dải — tô màu thương hiệu lên nền chính nó thì chìm
  const slideBreak = kind === "slides" && b.type === "heading" && b.level === 1;
  const bar = "h-7 rounded-md px-2 t12m outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring";
  return (
    <div role="group" aria-label={`Khối ${ORIGIN[b.origin] ?? ""}`.trim()} tabIndex={busy ? -1 : 0} data-open={sticky || mode ? "true" : undefined}
         onClick={() => !busy && setSticky(true)}
         onFocus={(e) => { if (!busy && e.target === e.currentTarget) setSticky(true); }}
         onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) close(); }}
         onKeyDown={(e) => { if (e.key === "Escape" && (mode || sticky)) { e.stopPropagation(); close(); e.currentTarget.blur(); } }}
         className={cn("group relative rounded-xl px-2 py-1.5 outline-none hover:bg-foreground/[0.03] focus-visible:bg-foreground/[0.03]", b.flags.length && "bg-bad/5 ring-1 ring-bad/30", slideBreak && "mt-4 border-t border-dashed border-hairline pt-4")}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          {mode === "edit" ? (
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={Math.max(2, Math.ceil(b.text.length / 70))} autoFocus aria-label="Nội dung"
                      className="t14 w-full rounded-lg border border-border bg-surface px-2 py-1 outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          ) : view ? (
            view
          ) : b.type === "heading" ? (
            <h3 className={cn("select-text", b.level === 1 ? "t16" : "t14m")} style={onDark ? undefined : { color }}><Prose text={b.text} /></h3>
          ) : b.type === "paragraph" ? (
            <p className="t14 select-text leading-relaxed"><Prose text={b.text} /></p>
          ) : b.type === "quote" ? (
            <blockquote className="t14 select-text border-l-4 pl-3" style={{ borderColor: color }}>
              “{b.text}”<span className="t12 mt-1 block text-muted-foreground">— {b.cite}</span>
            </blockquote>
          ) : b.type === "placeholder" ? (
            // chỗ giữ ảnh: CHỈ ở màn soạn — không bao giờ ra tệp / trang gửi khách (0224 §8)
            <div className="rounded-lg border border-dashed border-border bg-surface2 px-3 py-4 text-center">
              <p className="t14m">{b.text}</p>
              <p className="t12 mt-1 text-muted-foreground">{b.caption}</p>
            </div>
          ) : b.type === "image" ? (
            b.thumb || (b.document_id && !b.derived) ? (
              <figure className="relative flex flex-col gap-1">
                {/* eslint-disable-next-line @next/next/no-img-element -- ảnh trong kho tài liệu của công ty, qua BFF (cookie phiên); ảnh trích từ PDF / ảnh minh hoạ: bản thu nhỏ nhúng sẵn */}
                <img src={b.thumb || documentFileUrl(b.document_id, null)} alt={b.caption || b.filename || "Hình minh hoạ"} loading="lazy" className={imgClass ?? "max-h-72 w-full rounded-lg object-cover"} />
                {b.illustration ? <span className="t12 absolute bottom-2 right-2 rounded bg-black/60 px-2 py-0.5 text-white">{b.illustration}</span> : null}
                {b.caption ? <figcaption className="t12 text-muted-foreground">{b.caption}</figcaption> : null}
              </figure>
            ) : <p className="t12 rounded-lg bg-surface2 px-3 py-2 text-muted-foreground">Ảnh: {b.filename}{b.caption ? ` — ${b.caption}` : ""}</p>
          ) : <DataView b={b} />}
          {b.flags.map((f) => <p key={f} className="t12 mt-1 text-bad">{f}</p>)}
          {mode === "edit" ? <p className="t12 mt-1 text-muted-foreground">Khối sửa tay thành “Người sửa” — số trong đó do bạn chịu trách nhiệm.</p> : null}
        </div>
        <span className={cn("t12 shrink-0 rounded-full px-2 py-px", onDark ? "bg-white/20 text-white" : b.origin === "ai" ? "bg-violet/12 text-violet" : b.origin === "data" ? "bg-cold/10 text-cold" : "bg-foreground/5 text-muted-foreground")}>
          {ORIGIN[b.origin] ?? b.origin}
        </span>
      </div>
      {busy ? null : (
        // thanh thao tác NẰM DƯỚI khối (không nổi đè chữ — 0231): chỉ hiện khi khối đang mở (bấm / bàn phím vào khối / đang nhập), rê chuột chỉ tô nền khối
        <div role="toolbar" aria-label="Thao tác với khối"
             className="mt-1.5 hidden flex-wrap justify-end gap-0.5 group-data-[open=true]:flex">
          {mode === "edit" ? (
            <>
              <button type="button" disabled={!text.trim()} onClick={submit} className={cn(bar, "bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50")}>Lưu</button>
              <button type="button" onClick={close} className={cn(bar, "hover:bg-accent")}>Huỷ</button>
            </>
          ) : (
            <>
              {(b.type === "placeholder" ? [] : [["rewrite", "Viết lại"], ["insert_after", "Chèn số liệu"], ...(editable ? [["edit", "Sửa tay"]] : [])]).map(([k, label]) => (
                <button key={k} type="button" aria-pressed={mode === k} onClick={() => { setMode(k as Mode); setText(k === "edit" ? b.text : ""); }}
                        className={cn(bar, mode === k ? "bg-primary text-primary-foreground" : "hover:bg-accent")}>{label}</button>
              ))}
              {b.type === "image" && onCover && b.slot !== "cover" ? (
                <button type="button" onClick={onCover} title="Dùng ảnh này làm ảnh bìa (ảnh bìa cũ chuyển xuống lưới ảnh)" className={cn(bar, "hover:bg-accent")}>Đặt làm ảnh bìa</button>
              ) : null}
              {slots?.length && onSlot && b.type !== "placeholder" ? (
                <select value={b.slot} onChange={(e) => onSlot(e.target.value)} aria-label="Chỗ đứng của khối trong bố cục" title="Chỗ đứng trong bố cục"
                        className={cn(bar, "bg-popover text-popover-foreground hover:bg-accent")}>
                  {slots.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
              ) : null}
              <button type="button" onClick={onRemove} className={cn(bar, "text-bad hover:bg-bad/10")}>Xoá</button>
            </>
          )}
        </div>
      )}
      {mode && mode !== "edit" && !busy ? (
        // ô nhập lời yêu cầu: ngay dưới thanh, đẩy nội dung bên dưới xuống (không đè chữ — 0231)
        <div className="mt-1.5 flex w-full flex-col gap-2 rounded-xl border border-border bg-popover p-2 text-popover-foreground">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} autoFocus
                    aria-label={mode === "insert_after" ? "Cần số liệu gì" : "Muốn viết lại thế nào"}
                    placeholder={mode === "insert_after" ? "Vd: bảng hàng còn bán theo nhóm" : "Vd: ngắn hơn, nhấn vào vị trí"}
                    className="t14 rounded-lg border border-border bg-surface px-3 py-2 outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          <div className="flex gap-2">
            <button type="button" disabled={!text.trim()} onClick={submit} className={cn(actionClass("primary"), "h-8 disabled:opacity-50")}>Gửi cho AI</button>
            <button type="button" onClick={close} className={cn(actionClass("default"), "h-8")}>Huỷ</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
