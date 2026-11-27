"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, Check, ChevronDown, FileText, History, MessageSquarePlus, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { applyAll } from "@/components/blocks/word-apply";
import { collect, SourcesLine } from "@/components/blocks/word-assist";
import { ReviewList } from "@/components/blocks/word-review";
import { loadWord, WordEditor, type WordHandle } from "@/components/blocks/word-doc";
import { CopyButton, Failed, KbMultiPicker, UserBubble, Working, type KbRef } from "@/components/blocks/composer";
import { useDesk } from "@/components/desk/desk-context";
import { ScreensMenu } from "@/components/desk/screens-menu";
import { useFrameActions } from "@/components/desk/frame-context";
import { useFullFrame } from "@/components/desk/full-frame";
import { useFullScreen, useSolo } from "@/components/desk/solo";
import { HeaderGroup, Orb, PANE_HEADER, pillBtn, RailToggle } from "@/components/desk/column";
import { DictateMic } from "@/components/desk/dictate-mic";
import { COMPOSER_PILL, COMPOSER_TEXT, SendStopButton } from "@/components/desk/send-button";
import { actionClass } from "@/components/primitives";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { api } from "@/lib/api";
import { datetime } from "@/lib/format";
import { newKey } from "@/lib/idempotency";
import type { StudioData } from "@/lib/types";
import { cn } from "@/lib/utils";
import { decodeAssist, writtenSummary, type AssistResult, type AssistSource, type WordData } from "@/lib/word";

/** ``review``: lượt RÀ SOÁT — các chỗ trợ lý thấy sai, người Nhận / Bỏ từng chỗ ngay dưới câu trả lời (chỉ lượt rà gần nhất còn bấm được). */
type Msg = { role: "user" | "agent"; text: string; note?: string; error?: boolean; review?: Pick<AssistResult, "edits" | "checks">; sources?: AssistSource[] };
type DocRef = WordData["documents"][number];

const RING = "outline-none focus-visible:ring-2 focus-visible:ring-ring";
const chatMsg = (t: WordData["chat"][number]): Msg => ({ role: t.role, text: t.text, ...(t.note ? { note: t.note } : {}), ...(t.sources ? { sources: t.sources } : {}) });

/** Chờ trình soạn vừa mở xong (trang giấy đã vẽ, tay cầm đã gắn) — tối đa ~3 giây. */
async function ready(handle: React.RefObject<WordHandle | null>): Promise<WordHandle | null> {
  for (let i = 0; i < 60; i++) {
    const h = handle.current;
    if (h?.sheet()?.querySelector("[data-seg]")) return h;
    await new Promise((r) => setTimeout(r, 50));
  }
  return handle.current;
}

/**
 * MÀN "SOẠN" (0251 lát 2) — CÙNG KHUNG với Soạn với AI (Ấn phẩm): agent bên trái (đầu khung, lời chào + ví dụ, hàng chip, ô nhập viên), văn bản bên phải
 * trượt ra khi có (kéo / bấm tab ở màn hẹp). Câu đầu tiên tạo văn bản (`doc.create` server gửi), agent viết thẳng vào trang giấy (`doc.assist` — model chỉ
 * đề xuất chữ cho các đoạn, web áp lên trang) rồi LƯU THÀNH PHIÊN BẢN (hoàn tác = khôi phục phiên bản cũ). Bôi đen một đoạn rồi nhắn ⇒ sửa đúng đoạn đó.
 */
/** ``initial``: văn bản mở sẵn (mở từ danh sách "File Word đã mở", vừa tải lên…) — vào thẳng màn này với văn bản đó và cuộc trò chuyện đã lưu của nó. */
export function StudioBlock({ block, initial }: { block: StudioData; initial?: WordData }) {
  const desk = useDesk();
  const full = useFullFrame();
  const fullScreen = useFullScreen();
  const solo = useSolo();
  const frameActions = useFrameActions();
  const tabsId = useId();
  const [word, setWord] = useState<WordData | null>(initial ?? null);
  const [msgs, setMsgs] = useState<Msg[]>(() => (initial?.chat ?? []).map(chatMsg));
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<"chat" | "draft">(initial ? "draft" : "chat");
  const [agentHidden, setAgentHidden] = useState(false);   // màn rộng: ẩn cột agent ⇒ văn bản chiếm trọn bề ngang (như tắt khung trợ lý ở trình sửa cũ)
  const [docs, setDocs] = useState<DocRef[]>([]);
  const [fresh, setFresh] = useState(false);
  const handle = useRef<WordHandle | null>(null);
  const [form, setForm] = useState("");
  const [sources, setSources] = useState<KbRef[]>([]);   // tài liệu kho làm nguồn — gửi kèm MỌI lượt nhắn tới khi bỏ chọn (tối đa 3)   // khung thể thức người chọn ở chip "Loại"; "" ⇒ máy chủ nhận theo lời mô tả
  const [pct, setPct] = useState(40);     // bề ngang ngăn trò chuyện (%) — kéo đường chia như Soạn với AI
  const row = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const say = (m: Msg) => setMsgs((x) => [...x, m]);
  const hasDraft = !!word;

  const refreshDocs = useCallback(async () => {
    if (!block.doc.home) return;
    const d = await loadWord(block.doc.home, {}).catch(() => null);
    if (d) setDocs(d.documents);
  }, [block.doc.home]);
  useEffect(() => {
    let alive = true;   // tải danh sách lúc mở (cập nhật trạng thái sau khi tải xong, không đồng bộ trong effect)
    if (block.doc.home) void loadWord(block.doc.home, {}).then((d) => { if (alive && d) setDocs(d.documents); }).catch(() => null);
    return () => { alive = false; };
  }, [block.doc.home]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.length, busy]);

  async function openDoc(id: string, title?: string, restore = true): Promise<WordData | null> {
    const data = await loadWord(block.doc.view, { document_id: id });
    if (!data) say({ role: "agent", text: `Không mở được “${title ?? "văn bản"}”`, error: true });
    setWord(data);
    if (data) {
      setView("draft");
      setFresh(false);
      if (restore) setMsgs(data.chat.map(chatMsg));   // cuộc trò chuyện đã lưu của văn bản này
    }
    return data;
  }

  async function createDoc(title: string): Promise<WordData | null> {
    // khung thể thức: người chọn ⇒ đúng khung đó; chưa chọn ⇒ máy chủ nhận theo lời mô tả (code chọn, AI chỉ viết chữ vào khung)
    const r = await api.command(block.doc.create, { title: title.slice(0, 80), form, brief: title.slice(0, 2000) }, newKey());
    const id = (r.output as { document_id?: unknown } | null)?.document_id;
    if (r.status !== "ok" || typeof id !== "string") {
      say({ role: "agent", text: r.error || "Không tạo được văn bản mới", error: true });
      return null;
    }
    const data = await openDoc(id, title, false);   // văn bản vừa tạo: giữ câu vừa gửi trên khung (chưa có gì đã lưu)
    void refreshDocs();
    return data;
  }

  async function send(raw: string) {
    const text = raw.trim();
    if (!text || busy) return;
    setBusy(true);
    say({ role: "user", text });
    try {
      const doc = word ?? (await createDoc(text));
      if (!doc) return;
      setView("chat");
      const h = await ready(handle);
      const root = h?.sheet();
      if (!h || !root) {
        say({ role: "agent", text: "Trình soạn chưa sẵn sàng — thử lại", error: true });
        return;
      }
      const { paras, scope } = collect(root, h.range());
      const r = await api.command(block.doc.assist, { document_id: doc.documentId, instruction: text, paragraphs_json: JSON.stringify(paras), scope,
                                                     sources_json: JSON.stringify(sources.map((x) => x.id)) }, newKey());
      if (r.status !== "ok") {
        say({ role: "agent", text: r.error || "Agent chưa trả lời được", error: true });
        return;
      }
      const result = decodeAssist(r.output);
      const n = applyAll(root, result);
      if (n) {
        h.markDirty();
        await h.save();
        setFresh(true);
        void refreshDocs();
      }
      say({
        role: "agent",
        text: result.reply || (n ? "Đã viết vào văn bản." : "Không có gì cần đổi."),
        // nói rõ: trợ lý đã VIẾT THẲNG vào văn bản và tự lưu thành một phiên bản — muốn bản trước thì mở danh sách phiên bản
        note: n ? `${writtenSummary(result)}${scope === "selection" ? " ở phần đang chọn" : ""} · đã tự lưu thành phiên bản mới — muốn quay lại bản trước: nút đồng hồ ở đầu văn bản` : undefined,
        sources: result.sources,
      });
    } catch (e) {
      say({ role: "agent", text: e instanceof Error ? e.message : "Agent chưa trả lời được", error: true });
    } finally {
      setBusy(false);
    }
  }

  const getSheet = useCallback(() => handle.current?.sheet() ?? null, []);

  /** RÀ SOÁT văn bản đang mở (cả văn bản, hoặc đoạn đang bôi đen): KHÔNG tự ghi — trợ lý chỉ chỉ chỗ sai; người nhận chỗ nào thì chữ đổi trên trang, rồi Lưu. */
  async function review() {
    if (!word || busy) return;
    setBusy(true);
    setView("chat");   // màn hẹp đang ở "Bản xem" ⇒ sang trò chuyện để thấy tiến độ + thẻ nhận / bỏ (vệt gạch vẫn ở trang khi quay lại)
    setAgentHidden(false);   // màn rộng đang ẩn agent ⇒ hiện lại: thẻ rà soát nằm ở cột agent
    // tích tài liệu tham chiếu ⇒ rà soát kiêm ĐỐI CHIẾU: nói rõ ngay ở lời nhắn (cùng chữ máy chủ lưu vào cuộc trò chuyện)
    say({ role: "user", text: sources.length ? `Rà soát văn bản · đối chiếu ${sources.length} tài liệu` : "Rà soát văn bản" });
    try {
      const h = await ready(handle);
      const root = h?.sheet();
      if (!h || !root) {
        say({ role: "agent", text: "Trình soạn chưa sẵn sàng — thử lại", error: true });
        return;
      }
      const { paras, scope } = collect(root, h.range());
      const r = await api.command(block.doc.assist, { document_id: word.documentId, instruction: "", paragraphs_json: JSON.stringify(paras), scope, mode: "review",
                                                     sources_json: JSON.stringify(sources.map((x) => x.id)) }, newKey());
      if (r.status !== "ok") {
        say({ role: "agent", text: r.error || "Agent chưa rà soát được", error: true });
        return;
      }
      const result = decodeAssist(r.output);
      const n = result.edits.length + result.checks.length;
      say({ role: "agent", text: result.reply || (n ? `Có ${n} chỗ cần xem.` : "Không thấy lỗi cần sửa."),
            note: n ? `Rà soát ${scope === "selection" ? "đoạn đang chọn" : "cả văn bản"}${sources.length ? ` · đối chiếu ${sources.length} tài liệu` : ""} · chỗ nào Nhận thì chữ đổi trên văn bản, rồi Lưu` : undefined,
            review: { edits: result.edits, checks: result.checks }, sources: result.sources });
    } catch (e) {
      say({ role: "agent", text: e instanceof Error ? e.message : "Agent chưa rà soát được", error: true });
    } finally {
      setBusy(false);
    }
  }
  const lastReview = msgs.findLastIndex((m) => !!m.review);

  function fresher() {
    if (busy) return;
    setWord(null);
    setMsgs([]);
    setView("chat");
  }

  const drag = (e: React.PointerEvent) => {
    if (!e.currentTarget.hasPointerCapture(e.pointerId) || !row.current) return;
    const r = row.current.getBoundingClientRect();
    setPct(Math.min(65, Math.max(25, ((e.clientX - r.left) / r.width) * 100)));
  };
  const empty = msgs.length === 0 && !busy;

  return (
    <div ref={row} className={cn("@container relative flex min-h-0 flex-1", full ? "" : "min-h-[min(75vh,680px)] overflow-hidden rounded-xl border border-hairline")}
         style={{ "--chat": `${pct}%` } as React.CSSProperties}>
      <section id={`${tabsId}-panel-chat`} role="region"
               className={cn("relative flex min-h-0 min-w-0 flex-1 flex-col bg-background", hasDraft && "@3xl:flex-none @3xl:basis-(--chat)", hasDraft && view === "draft" && "@max-3xl:hidden",
                         hasDraft && agentHidden && "@3xl:hidden")}
               aria-label="Trò chuyện với agent soạn thảo">
        <header className={cn(PANE_HEADER, "px-3")}>
          {/* màn trọn khung trong phân hệ nhiều màn: màn rộng đã có cột danh sách (kèm nút ẩn thanh bên) ⇒ không lặp nút */}
          {full ? <span className={cn("contents", fullScreen && !solo && !desk.canvasMax && "min-[1280px]:hidden")}><RailToggle /></span> : null}
          <Orb busy={busy} size={34} />
          <div className="min-w-0 flex-1">
            <ScreensMenu title="Soạn thảo" />
            <p className="t12 flex items-center gap-1.5 text-muted-foreground">
              <span className={cn("size-[7px] shrink-0 rounded-full", busy ? "bg-primary" : "bg-ok")} />
              <span className="truncate">{busy ? "đang viết…" : word ? word.title : "Agent viết thẳng vào văn bản · lưu từng phiên bản"}</span>
            </p>
          </div>
          {hasDraft ? <ViewSwitch view={view} onView={setView} fresh={fresh} /> : null}
          <HeaderGroup>
            <DocsMenu docs={docs} currentId={word?.documentId ?? ""} disabled={busy} onOpen={(d) => void openDoc(d.id, d.title)} onNew={fresher} />
            <button type="button" className={pillBtn} onClick={fresher} disabled={busy} aria-label="Văn bản mới" title="Văn bản mới">
              <MessageSquarePlus className="size-4" />
            </button>
          </HeaderGroup>
          {frameActions}
        </header>

        <div className="relative min-h-0 flex-1">
          <div className="h-full overflow-y-auto px-4 py-4" role="log" aria-live="polite" aria-label="Hội thoại với agent soạn thảo">
            <div className="mx-auto flex w-full max-w-2xl flex-col gap-4">
              {empty && word ? (
                // đang mở một văn bản (chưa nhắn gì): KHÔNG mời "soạn mới" — câu nhắn lúc này sửa chính văn bản này
                <div className="flex flex-col gap-3 pt-6 @3xl:pt-10">
                  <h2 className="t20">Sửa “{word.title}”</h2>
                  <p className="t14 text-muted-foreground">Nhắn agent việc cần sửa — cả văn bản, hoặc bôi đen một đoạn bên phải để sửa riêng đoạn đó. Muốn soát lỗi: bấm “Rà soát” ở đầu văn bản. Mỗi lần sửa lưu thành một phiên bản, khôi phục được.</p>
                  <div className="flex flex-wrap gap-1.5">
                    {["Viết trang trọng hơn", "Viết ngắn gọn hơn", "Điền các chỗ còn “……” nếu đã rõ"].map((x) => (
                      <button key={x} type="button" onClick={() => void send(x)}
                              className={cn("t12 rounded-full border border-hairline bg-surface px-2.5 py-1.5 hover:bg-accent", RING)}>{x}</button>
                    ))}
                  </div>
                </div>
              ) : empty ? (
                <div className="flex flex-col gap-6 pt-6 @3xl:pt-10">
                  <div className="flex flex-col gap-1.5">
                    <h2 className="t20">{block.title}</h2>
                    <p className="t14 text-muted-foreground">{block.subtitle}</p>
                  </div>
                  <div className="grid gap-2 @lg:grid-cols-2">
                    {block.examples.slice(0, 4).map((x) => (
                      <button key={x} type="button" onClick={() => void send(x)}
                              className={cn("t14 flex items-center justify-between gap-2 rounded-2xl border border-hairline bg-surface px-3.5 py-2.5 text-left transition-colors hover:bg-accent", RING)}>
                        <span className="line-clamp-2">{x}</span>
                        <ArrowUp className="size-3.5 shrink-0 rotate-45 text-muted-foreground" aria-hidden />
                      </button>
                    ))}
                  </div>
                  {docs.length ? (
                    <div className="flex flex-col gap-2 border-t border-hairline pt-4">
                      <p className="t12m text-muted-foreground">Văn bản gần đây</p>
                      <div className="flex flex-col gap-1">
                        {docs.slice(0, 4).map((d) => (
                          <button key={d.id} type="button" onClick={() => void openDoc(d.id, d.title)}
                                  className={cn("flex items-center justify-between gap-3 rounded-xl px-3 py-2 text-left transition-colors hover:bg-foreground/5", RING)}>
                            <span className="flex min-w-0 items-center gap-2">
                              <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                              <span className="t14 truncate">{d.title}</span>
                            </span>
                            <span className="t12 shrink-0 text-muted-foreground">Phiên bản {d.version} · {datetime(d.updatedAt).slice(0, 10)}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
              {msgs.map((m, i) =>
                m.role === "user" ? <UserBubble key={i} text={m.text} block="" mode="" />
                : m.error ? <Failed key={i} text={m.text} />
                : (
                  <div key={i} className="group flex flex-col gap-2 px-1">
                    <p className="t14 flex items-start gap-2 select-text">
                      <Check className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden />
                      <span className="whitespace-pre-wrap">{m.text}</span>
                    </p>
                    {m.note ? <p className="t12 ml-6 text-muted-foreground">{m.note}</p> : null}
                    {m.sources?.length ? <SourcesLine sources={m.sources} className="ml-6" /> : null}
                    {m.review && m.review.edits.length + m.review.checks.length > 0 ? (
                      i === lastReview
                        ? <div className="ml-6"><ReviewList edits={m.review.edits} checks={m.review.checks} sheet={getSheet} onChanged={() => handle.current?.markDirty()} /></div>
                        : <p className="t12 ml-6 text-muted-foreground">Lượt rà soát cũ — rà lại để xem trên văn bản hiện tại</p>
                    ) : null}
                    <div className="ml-6 flex items-center gap-1"><CopyButton text={m.text} /></div>
                  </div>
                ))}
              {busy ? <Working status="Agent đang viết — văn bản sẽ tự cập nhật bên cạnh" steps={[]} /> : null}
              <div ref={end} />
            </div>
          </div>
        </div>
        <StudioInput block={block} hasDraft={hasDraft} busy={busy} form={form} setForm={setForm} sources={sources} setSources={setSources} onSend={(t) => void send(t)} />
      </section>

      {hasDraft && word ? (
        <>
          {/* đường chia kéo được (chuột / phím mũi tên) — như Soạn với AI */}
          <div role="separator" aria-orientation="vertical" aria-label="Kéo để đổi bề ngang ngăn trò chuyện (hoặc dùng phím mũi tên trái / phải)" tabIndex={0}
               aria-valuemin={25} aria-valuemax={65} aria-valuenow={Math.round(pct)}
               onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)} onPointerMove={drag}
               onKeyDown={(e) => {
                 if (e.key === "ArrowLeft") setPct((v) => Math.max(25, v - 3));
                 else if (e.key === "ArrowRight") setPct((v) => Math.min(65, v + 3));
                 else return;
                 e.preventDefault();
               }}
               className={cn("group relative z-10 -mx-1 hidden w-2 shrink-0 cursor-col-resize justify-center outline-none", !agentHidden && "@3xl:flex")}>
            <span className="w-px bg-hairline transition-colors group-hover:bg-primary group-focus-visible:w-0.5 group-focus-visible:bg-primary" />
          </div>
          <section id={`${tabsId}-panel-draft`} role="region"
                   className={cn("min-h-0 min-w-0 flex-1 flex-col bg-canvas", view === "draft" ? "flex" : "hidden", "@3xl:flex")}
                   aria-label="Khung làm việc — văn bản">
            <WordEditor key={word.documentId} initial={word} notify={desk.show} handleRef={handle} onReview={() => void review()} reviewRefs={sources.length}
                        maximize={full ? { on: desk.canvasMax, toggle: desk.toggleCanvasMax } : undefined}
                        lead={<>
                          <ViewSwitch view={view} onView={setView} fresh={fresh} />
                          {/* màn rộng: ẩn / hiện cột agent — chỉ còn văn bản khi cần tập trung sửa tay; màn hẹp đã một ngăn một lúc (nút trên) */}
                          <button type="button" onClick={() => setAgentHidden((x) => !x)} aria-pressed={agentHidden}
                                  aria-label={agentHidden ? "Hiện agent" : "Ẩn agent"} title={agentHidden ? "Hiện cột agent" : "Ẩn cột agent — văn bản chiếm trọn bề ngang"}
                                  className={cn("hidden size-8 shrink-0 place-items-center rounded-full border border-border bg-surface text-foreground/80 hover:bg-accent hover:text-foreground @3xl:grid", RING)}>
                            {agentHidden ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
                          </button>
                        </>} />
          </section>
        </>
      ) : null}
    </div>
  );
}

/** Màn HẸP (một ngăn một lúc): chọn "Trò chuyện / Bản xem" — một nút màu nổi đầu ngăn (thay hàng tab chiếm trọn một dòng). Màn rộng hai ngăn cạnh nhau ⇒ ẩn. */
function ViewSwitch({ view, onView, fresh }: { view: "chat" | "draft"; onView: (v: "chat" | "draft") => void; fresh: boolean }) {
  const [open, setOpen] = useState(false);
  const label = view === "draft" ? "Bản xem" : "Agent";
  return (
    <span className="@3xl:hidden">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger className={cn("t12m relative inline-flex h-8 shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-primary px-3 text-primary-foreground hover:bg-primary/90", RING)}
                        aria-label={`Đang xem: ${label} — đổi`}>
          {label}
          <ChevronDown className="size-3.5" aria-hidden />
          {fresh && view !== "draft" ? <span className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-warn ring-2 ring-background" aria-label="bản xem vừa cập nhật" /> : null}
        </PopoverTrigger>
        <PopoverContent align="start" className="w-44 gap-0.5 p-1.5">
          {([["chat", "Agent"], ["draft", "Bản xem"]] as const).map(([v, t]) => (
            <button key={v} type="button" aria-current={view === v ? "true" : undefined} onClick={() => { onView(v); setOpen(false); }}
                    className={cn("t14 flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left hover:bg-accent", RING, view === v && "t14m bg-primary/10 text-primary")}>
              {t}
              {view === v ? <Check className="size-3.5" aria-hidden /> : null}
            </button>
          ))}
        </PopoverContent>
      </Popover>
    </span>
  );
}

/** "Đã tạo": các văn bản Word của người này — cùng dáng nút + danh sách với thư viện ấn phẩm. */
function DocsMenu({ docs, currentId, disabled, onOpen, onNew }: { docs: DocRef[]; currentId: string; disabled: boolean; onOpen: (d: DocRef) => void; onNew: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className={cn("t12m inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-foreground hover:bg-foreground/5", RING)}
                      aria-label="Văn bản đã tạo" title="Xem danh sách các văn bản đã tạo">
        <History className="size-3.5 text-muted-foreground" aria-hidden />
        <span>Đã tạo</span>
        {docs.length ? <span className="tnum rounded-full bg-primary/12 px-1.5 py-px text-[11px] text-primary">{docs.length}</span> : null}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(23rem,calc(100vw-1.5rem))] gap-1.5 p-2">
        <button type="button" onClick={() => { setOpen(false); onNew(); }} disabled={disabled} className={cn(actionClass("primary"), "h-9 w-full justify-start")}>
          <MessageSquarePlus className="size-4" aria-hidden /> Văn bản mới
        </button>
        <ul className="flex max-h-[min(60vh,420px)] flex-col gap-0.5 overflow-y-auto" aria-label="Văn bản đã tạo">
          {docs.length ? null : <li className="t12 px-2 py-2 text-muted-foreground">Chưa có văn bản nào.</li>}
          {docs.map((d) => {
            const current = d.id === currentId;
            return (
              <li key={d.id} className={cn("rounded-lg", current ? "bg-primary/12" : "hover:bg-foreground/5")}>
                <button type="button" onClick={() => { setOpen(false); onOpen(d); }} disabled={disabled} aria-current={current ? "true" : undefined}
                        className={cn("flex w-full min-w-0 flex-col rounded-lg px-2 py-1.5 text-left", RING)}>
                  <span className={cn("t14 truncate", current && "t14m")}>{d.title}</span>
                  <span className="t12 truncate text-muted-foreground">Phiên bản {d.version} · {datetime(d.updatedAt).slice(0, 16)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

/** Ô nhập — cùng hàng chip + ô viên (mic · gửi) với Soạn với AI. Chip "Loại" nói đang soạn loại nào và mở các cách soạn khác. */
function StudioInput({ block, hasDraft, busy, form, setForm, sources, setSources, onSend }: {
  block: StudioData; hasDraft: boolean; busy: boolean; form: string; setForm: (f: string) => void;
  sources: KbRef[]; setSources: (s: KbRef[]) => void; onSend: (t: string) => void;
}) {
  const desk = useDesk();
  const [draft, setDraft] = useState("");
  const [kindOpen, setKindOpen] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);
  // chưa chọn ⇒ máy chủ nhận khung theo lời mô tả ("Auto") — không hiện một loại có thể không phải
  const current = form ? block.forms.find((f) => f.key === form)?.label ?? form : "Auto";
  const has = draft.trim().length > 0;
  const submit = () => {
    if (!has || busy) return;
    onSend(draft);
    setDraft("");
  };
  return (
    <div className="flex flex-col gap-1.5 px-3 pt-1 pb-3">
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-2">
        <div className="flex flex-wrap items-center gap-1.5 px-1">
        {/* đã mở văn bản ⇒ không chip "Rà soát" ở đây: nút nằm ở đầu ngăn văn bản — rà đúng văn bản đang mở, không lẫn với tài liệu tham chiếu */}
        {hasDraft ? null : <>
          <Popover open={kindOpen} onOpenChange={setKindOpen}>
            <PopoverTrigger title="Loại văn bản" aria-label={`Loại văn bản: ${current}`}
                            className={cn("t12m inline-flex h-7 items-center gap-1 rounded-full border border-hairline bg-surface px-2.5 text-foreground hover:bg-accent", RING)}>
              <FileText className="size-3.5 text-primary" aria-hidden />
              {current}
              <ChevronDown className="size-3 text-muted-foreground" aria-hidden />
            </PopoverTrigger>
            <PopoverContent align="start" className="w-72 gap-1 p-1.5">
              {[{ key: "", label: "Auto — nhận theo lời mô tả" }, ...block.forms].map((f) => {
                const on = form === f.key;
                return (
                  <button key={f.key || "_auto"} type="button" aria-current={on ? "true" : undefined} onClick={() => { setForm(f.key); setKindOpen(false); }}
                          className={cn("t14 flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left hover:bg-accent", RING, on && "t14m bg-primary/10 text-primary")}>
                    {f.label}
                    {on ? <Check className="size-3.5" aria-hidden /> : null}
                  </button>
                );
              })}
              {block.kinds.some((k) => k.open) ? <p className="t12m border-t border-hairline px-2.5 pt-2 pb-0.5 text-muted-foreground">Cách soạn khác</p> : null}
              {block.kinds.filter((k) => k.open).map((k) => (
                <button key={k.label} type="button" onClick={() => { setKindOpen(false); if (k.open) void desk.openTarget(k.open.query, k.open.input, k.label, true); }}
                        className={cn("t14 flex w-full flex-col rounded-lg px-2.5 py-1.5 text-left hover:bg-accent", RING)}>
                  {k.label}
                  {k.subtitle ? <span className="t12 text-muted-foreground">{k.subtitle}</span> : null}
                </button>
              ))}
            </PopoverContent>
          </Popover>
        </>}
          {/* nguồn cho agent: tài liệu kho (đúng quyền kệ) — một nút gọn, mở ra tích / bỏ từng cái; giữ qua các lượt nhắn */}
          {block.doc.kb_documents ? <KbMultiPicker query={block.doc.kb_documents} picked={sources} max={3} onChange={setSources} /> : null}
        </div>
        <div className={cn(COMPOSER_PILL, "pl-3")}>
          <textarea ref={ta} value={draft} rows={1} autoFocus onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }}
                    placeholder={hasDraft ? "Yêu cầu sửa văn bản…" : "Mô tả văn bản cần soạn…"}
                    aria-label={hasDraft ? "Muốn sửa gì" : "Cần soạn gì"} className={COMPOSER_TEXT} />
          <DictateMic onText={(text) => { setDraft((d) => (d.trim() ? `${d.trimEnd()} ${text}` : text)); ta.current?.focus(); }} />
          <SendStopButton canSend={has} running={busy} canStop={false} stopping={false} onSend={submit} onStop={() => undefined} />
        </div>
      </div>
    </div>
  );
}
