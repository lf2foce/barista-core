"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowUp, BookOpen, Check, ChevronDown, ChevronRight, CircleAlert, Copy, FileText, FolderOpen, History, MessageSquarePlus,
  RotateCcw, Sparkles, Square, Trash2, X,
} from "lucide-react";
import { api, composeStream, type ComposeTurnBody } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { newKey } from "@/lib/idempotency";
import { applyComposeEvent, currentOverflow, groupSteps, liveTurn, overflowActions, pickDocs, retryTarget, startBody, type LiveStep, type LiveTurn } from "@/lib/composer";
import { datetime, text as jsonText } from "@/lib/format";
import type { ComposerData, ComposerKind, ComposerOverflowBlock, ComposerOverflowPage, ComposerTurn, Input, Option } from "@/lib/types";
import { actionClass } from "@/components/primitives";
import { useDesk } from "@/components/desk/desk-context";
import { useFrameActions } from "@/components/desk/frame-context";
import { useFullFrame } from "@/components/desk/full-frame";
import { HeaderGroup, Orb, PANE_HEADER, pillBtn, RailToggle } from "@/components/desk/column";
import { DictateMic } from "@/components/desk/dictate-mic";
import { COMPOSER_PILL, COMPOSER_TEXT, SendStopButton } from "@/components/desk/send-button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { ComposerPaper } from "./composer-paper";

const CONTINUE = "Tiếp tục: làm nốt phần còn thiếu, dùng số liệu đã lấy được.";
const SELECT = "t14 h-9 w-full rounded-lg border border-border bg-surface px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60";
const RING = "outline-none focus-visible:ring-2 focus-visible:ring-ring";

function base64Blob(b64: string, type: string): Blob {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type });
}

async function loadComposer(query: string, sid: string): Promise<ComposerData | null> {
  const page = await api.query(query, { session_id: sid });
  const c = page.kind === "card" ? page.blocks.find((b) => b.type === "composer") : undefined;
  return c?.type === "composer" ? c : null;
}

export type KbRef = { id: string; title: string; shelf: string };

/**
 * Ấn phẩm kiểu Data Analyst Artifact Canvas (phenau_v3):
 * - Trái = hội thoại tự nhiên + thư viện tài liệu gần đây + chọn loại / hồ sơ / kho tài liệu gọn trên ô nhập;
 * - Phải = Artifact Canvas trượt ra khi có ấn phẩm (đầu 56px tối giản + tờ giấy trắng hoặc bộ slide 16:9).
 */
export function ComposerBlock({ block }: { block: ComposerData; onChanged?: () => void }) {
  const desk = useDesk();
  const [doc, setDoc] = useState(block);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [local, setLocal] = useState<{ user: LiveTurn; error: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [kind, setKind] = useState(block.kind || block.default_kind);
  // người tự bấm chọn loại ⇒ gửi đúng loại đó; chưa chọn ⇒ KHÔNG gửi, máy chủ nhận loại từ lời mô tả (web không đoán theo chữ gõ — rà 22/11)
  const [kindLocked, setKindLocked] = useState(false);
  const [kbRef, setKbRef] = useState<KbRef | null>(null);
  const [stopping, setStopping] = useState(false);
  const [draftOpen, setDraftOpen] = useState(block.blocks.length > 0);
  const [view, setView] = useState<"chat" | "draft">(block.blocks.length > 0 ? "draft" : "chat");
  const [pct, setPct] = useState(58);
  const row = useRef<HTMLDivElement>(null);
  const full = useFullFrame();
  const c = doc.commands;
  const viewQuery = c.view ?? "";
  const tabsId = useId();

  const [seen, setSeen] = useState(block);
  if (seen !== block) {
    setSeen(block);
    setDoc(block);
    setLocal(null);
  }

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
    setView(hasDraft ? "draft" : "chat");
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

  useEffect(() => {
    if ((doc.status !== "drafting" && !doc.fitting) || live || !doc.session_id) return;
    const started = Date.now();
    const t = setInterval(() => {
      if (Date.now() - started > 120_000) clearInterval(t);
      else void reload(doc.session_id);
    }, 1500);
    return () => clearInterval(t);
  }, [doc.status, doc.fitting, doc.session_id, live, reload]);

  async function send(text: string, opts: { block?: string; mode?: string; retry?: boolean; kind?: string } = {}) {
    const raw = text.trim();
    if (!raw || live) return;
    const sid = doc.session_id;
    const body: ComposeTurnBody = sid
      ? { session_id: sid, instruction: raw, block: opts.block ?? "", mode: opts.mode ?? "rewrite", retry: !!opts.retry }
      : startBody(raw, opts.kind ?? (kindLocked ? kind : null), kbRef ? [kbRef.id] : []);
    let turn = liveTurn(raw, opts.block ?? "", opts.mode ?? "");
    setLocal(null);
    setLive(turn);
    try {
      for await (const ev of composeStream(body, doc.stream)) {
        turn = applyComposeEvent(turn, ev);
        setLive(turn);
      }
    } catch (e) {
      turn = { ...turn, done: true, error: e instanceof Error ? e.message : "Không soạn được — thử lại" };
    }
    if (!sid && turn.sessionId) setKbRef(null);   // tài liệu đã đi cùng bản soạn (máy chủ giữ cho lượt thử lại); chưa tạo được bản ⇒ giữ để gửi lại
    if (turn.sessionId) await reload(turn.sessionId);
    if (turn.blocks?.length && !turn.error) {
      setDraftOpen(true);
      setView("draft");
    } else if (!turn.sessionId) {
      setLocal({ user: turn, error: turn.error || "Không soạn được — thử lại" });
    }
    setLive(null);
  }

  async function stop() {
    const sid = live?.sessionId;
    if (!sid || stopping || !c.stop) return;
    setStopping(true);
    try {
      const r = await api.command(c.stop, { session_id: sid }, newKey());
      if (r.status !== "ok") desk.show(r.error || "Không dừng được", true);
      else {
        const out = r.output as { stopped?: boolean; reason?: string } | null;
        if (out?.stopped === false && out.reason === "elsewhere") desk.show("Lượt đang chạy ở máy chủ khác — không dừng được từ đây", true);
      }
    } finally {
      setStopping(false);
    }
  }

  function drag(e: React.PointerEvent<HTMLDivElement>) {
    if (!(e.buttons & 1) || !row.current) return;
    const box = row.current.getBoundingClientRect();
    setPct(Math.max(35, Math.min(75, ((box.right - e.clientX) / box.width) * 100)));
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
    setKindLocked(false);
    setKind(doc.default_kind);
    setKbRef(null);
    setDoc({
      ...doc, session_id: "", title: "Ấn phẩm mới", brief: "", version: 0, status: "new", status_label: "", error: "",
      blocks: [], stats: { flagged: 0, placeholders: 0, data: 0 }, layout: { ...doc.layout, template: "", sections: [], html: "" },
      shares: [], versions: [], exports: [], turns: [],
    });
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
  const onView = (v: "chat" | "draft") => (v === "draft" ? showDraft() : setView("chat"));
  const newData = !!live && live.data.length > 0 && !live.blocks;
  const tabs = (where: "chat" | "draft") => (hasDraft ? <ViewTabs id={tabsId} where={where} view={view} onView={onView} fresh={newData} /> : null);
  const docs = <HistoryMenu doc={doc} disabled={!!live} onOpen={open} onNew={fresh} onDelete={remove} />;
  const title = live?.blocks ? live.title || doc.title : doc.session_id ? doc.title : "Ấn phẩm mới";
  const frameActions = useFrameActions();

  return (
    <div ref={row} className={cn("@container relative flex min-h-0 flex-1", full ? "" : "min-h-[min(75vh,680px)] overflow-hidden rounded-xl border border-hairline")}
         style={{ "--draft": `${100 - pct}%` } as React.CSSProperties}>
      <section id={`${tabsId}-panel-chat`} role={hasDraft ? "tabpanel" : undefined}
               className={cn("relative flex min-h-0 min-w-0 flex-1 flex-col bg-background", panes && "@3xl:flex-none @3xl:basis-(--draft)",
                             hasDraft && view === "draft" && "@max-3xl:hidden")} aria-label="Trò chuyện với AI">
        <header className={cn(PANE_HEADER, "px-3")}>
          {full ? <RailToggle /> : null}
          <Orb busy={!!live} size={34} />
          <div className="min-w-0 flex-1">
            <h1 className="t16 truncate">Ấn phẩm</h1>
            <p className="t12 flex items-center gap-1.5 text-muted-foreground">
              <span className={cn("size-[7px] shrink-0 rounded-full", live ? "bg-primary" : "bg-ok")} />
              <span className="truncate">{live ? "đang soạn…" : doc.session_id ? title : "Số liệu đúng quyền · bản in tức thì"}</span>
            </p>
          </div>
          <HeaderGroup>
            {docs}
            {hasDraft && !draftOpen ? (
              <button type="button" className={pillBtn} onClick={() => setDraftOpen(true)} aria-label="Mở bản xem" title="Mở bản xem">
                <FileText className="size-4" />
              </button>
            ) : null}
            <button type="button" className={pillBtn} onClick={fresh} disabled={!!live} aria-label="Ấn phẩm mới" title="Ấn phẩm mới">
              <MessageSquarePlus className="size-4" />
            </button>
          </HeaderGroup>
          {frameActions}
        </header>
        {tabs("chat")}
        <Conversation
          doc={doc} live={live} local={local} showDraft={hasDraft ? showDraft : undefined} draftOpen={draftOpen}
          onRetry={(t, retry) => void send(t.text, { block: t.block, mode: t.mode, retry })}
          onExample={(k) => { setKind(k.key); setKindLocked(true); void send(k.example, { kind: k.key }); }}
          onOpenHistory={(id) => void open(id)}
          onContinue={() => void send(CONTINUE)}
          onFit={(b) => { if (b.action) void send(b.action.input.instruction, { block: b.action.input.block, mode: b.action.input.mode }); }}
        />
        <ChatInput
          doc={doc} hasDraft={hasDraft} kinds={doc.kinds} kind={kind} kindLocked={kindLocked}
          setKind={(k) => { setKind(k); setKindLocked(true); }}
          autoKind={() => { setKind(doc.default_kind); setKindLocked(false); }}
          recipe={recipe} kbRef={kbRef} setKbRef={setKbRef}
          running={!!live} stopping={stopping} canStop={!!live?.sessionId}
          onSend={(t) => void send(t)} onStop={() => void stop()}
        />
      </section>

      {hasDraft ? (
        <>
          {panes ? (
            <div role="separator" aria-orientation="vertical" aria-label="Kéo để đổi bề ngang bản xem (hoặc dùng phím mũi tên trái / phải)" tabIndex={0}
                 aria-valuemin={35} aria-valuemax={75} aria-valuenow={Math.round(pct)}
                 onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)} onPointerMove={drag}
                 onKeyDown={(e) => {
                   if (e.key === "ArrowLeft") setPct((v) => Math.min(75, v + 3));
                   else if (e.key === "ArrowRight") setPct((v) => Math.max(35, v - 3));
                   else return;
                   e.preventDefault();
                 }}
                 className="group relative z-10 -mx-1 hidden w-2 shrink-0 cursor-col-resize justify-center outline-none @3xl:flex">
              <span className="w-px bg-hairline transition-colors group-hover:bg-primary group-focus-visible:w-0.5 group-focus-visible:bg-primary" />
            </div>
          ) : null}
          <section id={`${tabsId}-panel-draft`} role="tabpanel"
                   className={cn("min-h-0 min-w-0 flex-1 flex-col border-l border-hairline bg-canvas", view === "draft" ? "flex" : "hidden", panes ? "@3xl:flex" : "@3xl:hidden")}
                   aria-label="Khung làm việc — ấn phẩm">
            {tabs("draft")}
            <ComposerPaper
              doc={doc} live={live} busy={busy} title={title} docs={docs} frameActions={frameActions}
              onClose={() => { setDraftOpen(false); setView("chat"); }}
              onRevise={(b, instruction, mode) => { setView("chat"); void send(instruction, { block: b, mode }); }}
              onEdit={(b, text) => void act(c.edit, { session_id: doc.session_id, block: b, text }, "Đang lưu…")}
              onRemove={(b) => void act(c.remove, { session_id: doc.session_id, block: b }, "Đang xoá khối…")}
              onRefresh={() => void act(c.refresh, { session_id: doc.session_id }, "Đang cập nhật…")}
              onRestore={(v) => void act(c.restore, { session_id: doc.session_id, version: v }, "Đang quay lại…")}
              onExport={(f) => void exportAs(f)} onDownload={(id) => void download(id)}
              onLayout={(template) => void act(c.set_layout, { session_id: doc.session_id, template }, "Đang đổi mẫu…")}
              onSlot={(b, slot) => void act(c.set_slot, { session_id: doc.session_id, block: b, slot }, "Đang đổi chỗ…")}
              onCover={(b) => void act(c.set_cover, { session_id: doc.session_id, block: b }, "Đang đặt bìa…")}
              onShare={async (recipient, days) => {
                const out = await act(c.share, { session_id: doc.session_id, recipient, days }, "Đang tạo link…");
                return out && typeof out.url === "string" ? out.url : null;
              }}
              onUnshare={(id) => void act(c.unshare, { share_id: id }, "Đang thu hồi…")}
              onDuplicate={async (leadId) => {
                const out = await run(c.duplicate, { session_id: doc.session_id, lead_id: leadId }, "Đang dựng bản mới…");
                if (out && typeof out.session_id === "string") await reload(out.session_id);
              }}
            />
          </section>
        </>
      ) : null}
    </div>
  );
}

// ── Thư viện tài liệu đã tạo (hiện rõ "Đã tạo (N)") ──

function HistoryMenu({ doc, disabled, onOpen, onNew, onDelete }: {
  doc: ComposerData; disabled: boolean; onOpen: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setConfirm(null); }}>
      <PopoverTrigger
        className="t12m inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-foreground outline-none hover:bg-foreground/5 focus-visible:ring-2 focus-visible:ring-ring"
        aria-label="Thư viện ấn phẩm đã tạo"
        title="Xem danh sách các ấn phẩm đã tạo"
      >
        <History className="size-3.5 text-muted-foreground" aria-hidden />
        <span>Đã tạo</span>
        {doc.history.length ? <span className="tnum rounded-full bg-primary/12 px-1.5 py-px text-[11px] text-primary">{doc.history.length}</span> : null}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(23rem,calc(100vw-1.5rem))] gap-1.5 p-2">
        <button type="button" onClick={() => { setOpen(false); onNew(); }} disabled={disabled} className={cn(actionClass("primary"), "h-9 w-full justify-start")}>
          <MessageSquarePlus className="size-4" aria-hidden /> Ấn phẩm mới
        </button>
        <ul className="flex max-h-[min(60vh,420px)] flex-col gap-0.5 overflow-y-auto" aria-label="Ấn phẩm đã tạo">
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

// ── Chọn hồ sơ / công thức: CHỈ kích hoạt khi người dùng chủ động chọn (không tự cướp Enter) ──

function useRecipe(doc: ComposerData, kind: string, disabled: boolean, onBuilt: (sessionId: string) => void) {
  const desk = useDesk();
  const c = doc.commands;
  const recipes = doc.recipes.filter((r) => r.piece === kind);
  const [armed, setArmed] = useState(false);   // chỉ khi người dùng chủ động chọn công thức/hồ sơ thì mới dựng từ hồ sơ
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
  if (seenKey !== key) {
    setSeenKey(key);
    setArmed(false);
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
  const canBuild = available && (!needs || !!subject) && !disabled && !busy;
  const ready = armed && canBuild;   // chỉ kích hoạt chế độ Enter = dựng từ hồ sơ khi người dùng đã chọn rõ
  const subjectLabel = subjects?.find((o) => o.value === subject)?.label ?? "";
  const customerLabel = customers?.find((o) => o.value === customer)?.label ?? "";
  async function build(extra = "") {
    if (!recipe || !c.recipe || !canBuild) return;
    setBusy(true);
    try {
      const r = await api.command(c.recipe, { kind: recipe.key, subject, ...(customer ? { lead_id: customer } : {}), ...(extra.trim() ? { extra: extra.trim() } : {}) }, newKey());
      const out = r.output as { session_id?: string } | null;
      if (r.status !== "ok" || !out?.session_id) desk.show(r.error || `Không dựng được (${r.status})`, true);
      else {
        setArmed(false);
        onBuilt(String(out.session_id));
      }
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Không dựng được", true);
    } finally {
      setBusy(false);
    }
  }
  return {
    available, armed, setArmed, canBuild, ready, busy, recipes, recipe, needs, takesCustomer,
    subject, setSubject: (v: string) => { setSubject(v); setArmed(!!v); }, subjectLabel, subjects,
    customer, setCustomer, customerLabel, customers,
    setPicked: (v: string) => { setPicked(v); setArmed(true); },
    build,
    clear: () => { setArmed(false); setSubject(""); setCustomer(""); },
  };
}
type Recipe = ReturnType<typeof useRecipe>;

/** Thanh "Trò chuyện | Bản xem" (màn hẹp): tablist đúng chuẩn — tab đang chọn nhận Tab, ← → Home End đổi tab. Thanh có ở CẢ hai ngăn (ngăn kia ẩn ở màn hẹp) ⇒
 *  đổi tab xong đưa tiêu điểm sang tab cùng tên của thanh ở ngăn vừa hiện. */
const VIEWS = ["chat", "draft"] as const;
export function ViewTabs({ id, where, view, onView, fresh }: {
  id: string; where: "chat" | "draft"; view: "chat" | "draft"; onView: (v: "chat" | "draft") => void; fresh: boolean;
}) {
  const go = (v: "chat" | "draft") => {
    onView(v);
    requestAnimationFrame(() => document.getElementById(`${id}-${v}-tab-${v}`)?.focus());
  };
  const onKey = (e: React.KeyboardEvent) => {
    const i = VIEWS.indexOf(view);
    const next = e.key === "ArrowRight" ? VIEWS[(i + 1) % VIEWS.length] : e.key === "ArrowLeft" ? VIEWS[(i + VIEWS.length - 1) % VIEWS.length]
      : e.key === "Home" ? VIEWS[0] : e.key === "End" ? VIEWS[VIEWS.length - 1] : null;
    if (!next) return;
    e.preventDefault();
    go(next);
  };
  const tab = (v: "chat" | "draft", label: string) => (
    <button type="button" role="tab" id={`${id}-${where}-tab-${v}`} aria-selected={view === v} aria-controls={`${id}-panel-${v}`} tabIndex={view === v ? 0 : -1}
            onClick={() => go(v)} onKeyDown={onKey}
            className={cn("t14m relative inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-full", RING,
                          view === v ? "bg-surface text-foreground" : "text-muted-foreground hover:text-foreground")}>
      {label}
      {v === "draft" && fresh && view !== "draft" ? <span className="size-1.5 rounded-full bg-primary" aria-label="đang có số liệu mới" /> : null}
    </button>
  );
  return (
    <div role="tablist" aria-label="Trò chuyện hoặc bản soạn" className="mx-3 my-2 flex shrink-0 gap-0.5 rounded-full bg-surface2 p-0.5 @3xl:hidden">
      {tab("chat", "Trò chuyện")}
      {tab("draft", "Bản xem")}
    </div>
  );
}

// ── Hội thoại bên trái ──

function Conversation({ doc, live, local, showDraft, draftOpen, onRetry, onExample, onOpenHistory, onContinue, onFit }: {
  doc: ComposerData; live: LiveTurn | null; local: { user: LiveTurn; error: string } | null;
  showDraft?: () => void; draftOpen: boolean;
  onRetry: (t: { text: string; block: string; mode: string }, retry: boolean) => void;
  onExample: (k: ComposerKind) => void;
  onOpenHistory: (id: string) => void;
  onContinue: () => void;
  onFit: (b: ComposerOverflowBlock) => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  const retry = retryTarget(doc.turns);
  const empty = !doc.turns.length && !live && !local;
  const over = live ? [] : currentOverflow(doc.turns, doc.version);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [doc.turns.length, live, local]);
  return (
    <div className="relative min-h-0 flex-1">
      <div className="h-full overflow-y-auto px-4 py-4" role="log" aria-live="polite" aria-label="Hội thoại với AI soạn">
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-4">
          {empty ? (
            <div className="flex flex-col gap-6 pt-6 @3xl:pt-10">
              <div className="flex flex-col gap-1.5">
                <h2 className="t20">Bạn cần soạn ấn phẩm gì?</h2>
                <p className="t14 text-muted-foreground">
                  Mô tả bằng lời tự nhiên — hoặc chọn tài liệu trong kho / hồ sơ ở ngay trên ô nhập. Bản xem sẽ trượt ra bên cạnh để đọc và xuất Word / PDF ngay.
                </p>
              </div>
              <div className="grid gap-2 @lg:grid-cols-2">
                {doc.kinds.filter((k) => k.example).slice(0, 4).map((k) => (
                  <button key={k.key} type="button" onClick={() => onExample(k)}
                          className="t14 flex items-center justify-between gap-2 rounded-2xl border border-hairline bg-surface px-3.5 py-2.5 text-left outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
                    <span className="line-clamp-2">{k.example}</span>
                    <ArrowUp className="size-3.5 shrink-0 rotate-45 text-muted-foreground" aria-hidden />
                  </button>
                ))}
              </div>
              {doc.history.length ? (
                <div className="flex flex-col gap-2 border-t border-hairline pt-4">
                  <p className="t12m text-muted-foreground">Ấn phẩm gần đây</p>
                  <div className="flex flex-col gap-1">
                    {doc.history.slice(0, 4).map((h) => (
                      <button key={h.id} type="button" onClick={() => onOpenHistory(h.id)}
                              className="flex items-center justify-between gap-3 rounded-xl px-3 py-2 text-left outline-none transition-colors hover:bg-foreground/5 focus-visible:ring-2 focus-visible:ring-ring">
                        <span className="flex min-w-0 items-center gap-2">
                          <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                          <span className="t14 truncate">{h.title}</span>
                        </span>
                        <span className="t12 shrink-0 text-muted-foreground">{h.kind_label} · {datetime(h.at).slice(0, 10)}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
              {doc.note ? <p className="t12 text-muted-foreground">{doc.note}</p> : null}
            </div>
          ) : null}
          {doc.turns.map((t, i) => (
            <TurnBubble key={i} t={t} onRetry={i === doc.turns.length - 1 && retry && !live ? () => onRetry(retry, true) : undefined}
                        onContinue={i === doc.turns.length - 1 && t.partial && !live ? onContinue : undefined}
                        onShowDraft={i === doc.turns.length - 1 && t.version && showDraft ? showDraft : undefined} draftOpen={draftOpen}
                        overflow={i === doc.turns.length - 1 ? over : undefined} onFit={onFit} fitting={!!doc.fitting} />
          ))}
          {doc.status === "drafting" && !live ? <Working status="Em đang soạn — bản xem sẽ tự hiện bên cạnh" steps={[]} /> : null}
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

export function UserBubble({ text, block, mode }: { text: string; block: string; mode: string }) {
  return (
    <div className="flex justify-end pl-12">
      <div className="flex max-w-full flex-col items-end gap-1">
        {block ? <span className="t12 text-muted-foreground">{mode === "insert_after" ? "Chèn sau" : "Viết lại"} khối {block}</span> : null}
        <div className="max-w-full rounded-[18px] bg-cold px-3.5 py-2 text-white">
          <p className="t14 select-text whitespace-pre-wrap break-words">{text}</p>
        </div>
      </div>
    </div>
  );
}

export function CopyButton({ text }: { text: string }) {
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
            <button type="button" onClick={onShowDraft} className={cn(actionClass(onContinue ? "default" : "primary"), "h-8 w-fit px-3", draftOpen && "@3xl:hidden")}>
              <FileText className="size-3.5" aria-hidden /> Mở bản xem
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

export function Failed({ text, steps = [], onRetry }: { text: string; steps?: { title: string; status: string; reason?: string }[]; onRetry?: () => void }) {
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

export function Working({ status, steps }: { status: string; steps: LiveStep[] }) {
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

/** Chọn tài liệu từ Kho tài liệu để gắn làm NGUỒN cho lượt soạn (gửi mã tài liệu, máy chủ kiểm quyền đọc + đưa cho AI). Truy vấn danh sách do khối gửi
 *  (`commands.kb_documents`, đúng quyền kệ của người dùng); không xem được kho ⇒ báo gọn, không vỡ ô nhập. Danh sách lấy trọn một trang (máy chủ trả tới 1.000
 *  tài liệu), lọc theo chữ gõ, chỉ hiện 50 dòng đầu — còn nữa thì nhắc gõ để thu hẹp. */
export function KbPicker({ query, picked, onPick }: { query: string; picked: KbRef | null; onPick: (d: KbRef | null) => void }) {
  const [open, setOpen] = useState(false);
  const [docs, setDocs] = useState<KbRef[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [q, setQ] = useState("");
  useEffect(() => {
    if (!open || docs !== null) return;
    api.query(query, {}).then((page) => {
      setDocs(page.kind === "table" ? page.rows.map((r) => ({
        id: r.id,
        title: jsonText(r.values.title) || jsonText(r.values.name),
        shelf: jsonText(r.values.shelf_name),
      })).filter((d) => d.id && d.title) : []);
    }, () => { setFailed(true); setDocs([]); });
  }, [open, docs, query]);
  const { shown, more } = pickDocs(docs ?? [], q);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className={cn("t12m inline-flex h-7 items-center gap-1 rounded-full border px-2.5", RING,
                                    picked ? "border-primary/40 bg-primary/10 text-primary" : "border-hairline bg-surface text-muted-foreground hover:bg-accent hover:text-foreground")}>
        <BookOpen className="size-3.5" aria-hidden />
        <span className="max-w-[14rem] truncate">{picked ? picked.title : "Kho tài liệu"}</span>
        <ChevronDown className="size-3" aria-hidden />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(22rem,calc(100vw-1.5rem))] gap-2 p-2.5">
        <p className="t12m text-muted-foreground">Tham khảo từ Kho tài liệu (kệ bạn có quyền xem)</p>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm tên tài liệu…" aria-label="Tìm tài liệu trong kho"
               className="t14 h-8 w-full rounded-lg border border-border bg-surface px-2.5 outline-none focus-visible:ring-2 focus-visible:ring-ring" />
        <ul className="flex max-h-56 flex-col gap-0.5 overflow-y-auto" aria-label="Tài liệu trong kho">
          {docs === null ? <li className="t12 px-2 py-2 text-muted-foreground">Đang tải…</li>
            : failed ? <li className="t12 px-2 py-2 text-muted-foreground">Bạn chưa xem được Kho tài liệu — soạn không cần tài liệu, hoặc hỏi quản trị cấp quyền.</li>
            : !shown.length ? <li className="t12 px-2 py-2 text-muted-foreground">Chưa có tài liệu nào khớp.</li>
            : shown.map((d) => (
              <li key={d.id}>
                <button type="button" onClick={() => { onPick(d); setOpen(false); }} aria-current={picked?.id === d.id ? "true" : undefined}
                        className={cn("flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-accent", RING, picked?.id === d.id && "bg-primary/10 text-primary")}>
                  <span className="t14 truncate">{d.title}</span>
                  {d.shelf ? <span className="t12 shrink-0 text-muted-foreground">{d.shelf}</span> : null}
                </button>
              </li>
            ))}
          {more ? <li className="t12 px-2 py-1.5 text-muted-foreground">Còn {more} tài liệu khớp — gõ thêm để thu hẹp.</li> : null}
        </ul>
        {picked ? (
          <button type="button" onClick={() => { onPick(null); setOpen(false); }} className={cn("t12 self-start rounded text-muted-foreground hover:text-foreground", RING)}>Bỏ chọn tài liệu</button>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function ChatInput({ doc, hasDraft, kinds, kind, kindLocked, setKind, autoKind, recipe, kbRef, setKbRef, running, stopping, canStop, onSend, onStop }: {
  doc: ComposerData; hasDraft: boolean; kinds: ComposerData["kinds"]; kind: string; kindLocked: boolean; setKind: (k: string) => void; autoKind: () => void;
  recipe: Recipe; kbRef: KbRef | null; setKbRef: (d: KbRef | null) => void;
  running: boolean; stopping: boolean; canStop: boolean;
  onSend: (text: string) => void; onStop: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [kindOpen, setKindOpen] = useState(false);
  const [recOpen, setRecOpen] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

  const fresh = !doc.session_id;
  const fromRecord = fresh && recipe.ready;
  const has = draft.trim().length > 0 || fromRecord;
  // chưa chọn ⇒ máy chủ nhận loại từ lời mô tả (thiếu `kind` ở luồng soạn) — nói rõ trên ô chọn thay vì hiện một loại mà có thể không phải
  const kindLabel = kindLocked ? kinds.find((k) => k.key === kind)?.label ?? kind : "Loại: tự nhận";
  const submit = () => {
    if (!has || running || recipe.busy) return;
    if (fromRecord) void recipe.build(draft);
    else onSend(draft);
    setDraft("");
  };

  return (
    <div className="flex flex-col gap-1.5 px-3 pt-1 pb-3">
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-2">
        {fresh ? (
          <div className="flex flex-wrap items-center gap-1.5 px-1">
            <Popover open={kindOpen} onOpenChange={setKindOpen}>
              <PopoverTrigger className="t12m inline-flex h-7 items-center gap-1 rounded-full border border-hairline bg-surface px-2.5 text-foreground outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
                <FileText className="size-3.5 text-primary" aria-hidden />
                {kindLabel}
                <ChevronDown className="size-3 text-muted-foreground" aria-hidden />
              </PopoverTrigger>
              <PopoverContent align="start" className="w-64 gap-1 p-1.5">
                <button type="button" onClick={() => { autoKind(); setKindOpen(false); }} aria-current={!kindLocked ? "true" : undefined}
                        className={cn("t14 flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left hover:bg-accent", RING, !kindLocked && "t14m bg-primary/10 text-primary")}>
                  Tự nhận theo lời mô tả
                  {!kindLocked ? <Check className="size-3.5" aria-hidden /> : null}
                </button>
                {kinds.map((k) => {
                  const on = kindLocked && kind === k.key;
                  return (
                    <button key={k.key} type="button" onClick={() => { setKind(k.key); setKindOpen(false); }} aria-current={on ? "true" : undefined}
                            className={cn("t14 flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left hover:bg-accent", RING, on && "t14m bg-primary/10 text-primary")}>
                      {k.label}
                      {on ? <Check className="size-3.5" aria-hidden /> : null}
                    </button>
                  );
                })}
              </PopoverContent>
            </Popover>

            {doc.commands.kb_documents ? <KbPicker query={doc.commands.kb_documents} picked={kbRef} onPick={setKbRef} /> : null}
            {kbRef ? (
              <button type="button" onClick={() => setKbRef(null)} aria-label="Bỏ chọn tài liệu" className={cn("t12 grid size-6 place-items-center rounded-full text-muted-foreground hover:bg-foreground/5 hover:text-foreground", RING)}>
                <X className="size-3" />
              </button>
            ) : null}

            {recipe.available ? (
              <Popover open={recOpen} onOpenChange={setRecOpen}>
                <PopoverTrigger className={cn("t12m inline-flex h-7 items-center gap-1 rounded-full border px-2.5 outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                              recipe.armed ? "border-primary/40 bg-primary/10 text-primary" : "border-hairline bg-surface text-muted-foreground hover:bg-accent hover:text-foreground")}>
                  <FolderOpen className="size-3.5" aria-hidden />
                  <span className="max-w-[14rem] truncate">
                    {recipe.armed ? (recipe.subjectLabel || recipe.recipe?.label || "Từ hồ sơ") : "Từ hồ sơ"}
                    {recipe.armed && recipe.customerLabel ? ` · ${recipe.customerLabel}` : ""}
                  </span>
                  <ChevronDown className="size-3" aria-hidden />
                </PopoverTrigger>
                <PopoverContent align="start" className="w-[min(22rem,calc(100vw-1.5rem))] gap-2.5 p-3">
                  <p className="t12m text-muted-foreground">Dựng nhanh từ dữ liệu hồ sơ có sẵn</p>
                  {recipe.recipes.length > 1 ? (
                    <div className="flex flex-wrap gap-1.5">
                      {recipe.recipes.map((x) => (
                        <button key={x.key} type="button" onClick={() => recipe.setPicked(x.key)}
                                className={cn("t12 rounded-full border px-2.5 py-1", recipe.recipe?.key === x.key ? "border-primary bg-primary text-white" : "border-border bg-surface hover:bg-accent")}>
                          {x.label}
                        </button>
                      ))}
                    </div>
                  ) : recipe.recipe ? (
                    <p className="t14">{recipe.recipe.label}{recipe.recipe.about ? <span className="t12 block text-muted-foreground">{recipe.recipe.about}</span> : null}</p>
                  ) : null}
                  {recipe.needs ? (
                    <label className="t12 flex flex-col gap-1">Hồ sơ
                      <select value={recipe.subject} disabled={recipe.busy || recipe.subjects === null} onChange={(e) => recipe.setSubject(e.target.value)} className={SELECT}>
                        <option value="">{recipe.subjects === null ? "Đang tải…" : recipe.subjects.length ? "Chọn hồ sơ" : "Chưa có hồ sơ nào"}</option>
                        {(recipe.subjects ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    </label>
                  ) : null}
                  {recipe.takesCustomer ? (
                    <label className="t12 flex flex-col gap-1">Cho khách (không bắt buộc)
                      <select value={recipe.customer} disabled={recipe.busy} onChange={(e) => recipe.setCustomer(e.target.value)} className={SELECT}>
                        <option value="">Không riêng khách nào</option>
                        {(recipe.customers ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    </label>
                  ) : null}
                  <div className="flex items-center justify-between gap-2 pt-1">
                    {recipe.armed ? (
                      <button type="button" onClick={() => recipe.clear()} className="t12 text-muted-foreground hover:text-foreground">Bỏ chọn</button>
                    ) : <span />}
                    <button type="button" disabled={!recipe.canBuild} onClick={() => { setRecOpen(false); void recipe.build(draft); setDraft(""); }}
                            className={cn(actionClass("primary"), "h-8 text-[12px]")}>
                      {recipe.busy ? "Đang dựng…" : "Dựng ngay"}
                    </button>
                  </div>
                </PopoverContent>
              </Popover>
            ) : null}

            {recipe.armed ? (
              <button type="button" onClick={() => recipe.clear()} aria-label="Bỏ chọn hồ sơ" className="t12 grid size-6 place-items-center rounded-full text-muted-foreground hover:bg-foreground/5 hover:text-foreground">
                <X className="size-3" />
              </button>
            ) : null}
          </div>
        ) : null}

        <div className={cn(COMPOSER_PILL, "pl-3")}>
          <textarea
            ref={ta}
            value={draft}
            rows={1}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={fromRecord ? "Thêm ý riêng (không bắt buộc), Enter để dựng…" : hasDraft ? "Yêu cầu sửa ấn phẩm…" : "Mô tả tài liệu, slide hoặc ấn phẩm cần soạn…"}
            aria-label={fresh ? "Cần soạn gì" : "Muốn sửa gì"}
            className={COMPOSER_TEXT}
          />
          <DictateMic onText={(text) => { setDraft((d) => (d.trim() ? `${d.trimEnd()} ${text}` : text)); ta.current?.focus(); }} />
          <SendStopButton canSend={has} running={running} canStop={canStop} stopping={stopping} onSend={submit} onStop={onStop} />
        </div>
      </div>
    </div>
  );
}
