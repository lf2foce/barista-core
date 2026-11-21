"use client";

import { ArrowUp, Brain, Check, ChevronRight, CircleAlert, FileText, History, ImageIcon, ImagePlus, MessageSquarePlus, Play, Share2, Sparkles, TriangleAlert, X } from "lucide-react";
import { api } from "@/lib/api";
import { confirmAll, waitingProposals } from "@/lib/confirm-all";
import { newKey } from "@/lib/idempotency";
import { addImages, IMAGE_MAX, imageFilesOf, prepareImage, type ChatImage } from "@/lib/images";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { clock } from "@/lib/format";
import { asideCards, resultCards, stepDone, type ChatMessage, type LiveTurn } from "@/lib/chat";
import { ProposalRow, ResultRow } from "./chat-cards";
import { CompactCard } from "./compact-card";
import { isNearBottom } from "@/lib/scroll";
import { CHAT_DOC_MAX, splitDrop } from "@/lib/documents";
import type { ChatDocument } from "@/lib/api";
import { withoutDocumentIds } from "@/lib/documents";
import { DocumentDropDialog } from "./document-drop";
import type { ActionSpec, Attention, ChatCard } from "@/lib/types";
import { cardToneVar } from "@/lib/format";
import { cn } from "@/lib/utils";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { IconTile, moduleTint } from "@/components/icons";
import { Markdown } from "@/components/markdown";
import { actionClass } from "@/components/primitives";
import { HeaderGroup, Orb, PANE_HEADER, pillBtn, RailToggle } from "./column";
import { COMPOSER_PILL, COMPOSER_TEXT, SendStopButton } from "./send-button";
import { useChat } from "./chat-context";
import { useDesk } from "./desk-context";
import { DictateMic } from "./dictate-mic";

/** Ảnh chờ gửi kèm câu hỏi (0050) — dán, kéo thả vào cột, hoặc nút chọn ảnh. Nén ở trình duyệt; server đọc trong lượt rồi bỏ. */
function usePendingImages() {
  const desk = useDesk();
  const [images, setImages] = useState<ChatImage[]>([]);
  const [preparing, setPreparing] = useState(0);
  const add = async (files: File[]) => {
    const picked = files.filter((f) => f.type.startsWith("image/") || /\.(heic|heif)$/i.test(f.name));
    if (!picked.length) return;
    const room = IMAGE_MAX - images.length;
    if (room <= 0) return desk.show(`Tối đa ${IMAGE_MAX} ảnh mỗi câu hỏi`, true);
    if (picked.length > room) desk.show(`Tối đa ${IMAGE_MAX} ảnh mỗi câu hỏi — bỏ bớt ${picked.length - room} ảnh`, true);
    setPreparing((n) => n + Math.min(room, picked.length));
    for (const f of picked.slice(0, room)) {
      try {
        const img = await prepareImage(f);
        setImages((cur) => addImages(cur, [img]).images);
      } catch (e) {
        desk.show(e instanceof Error ? e.message : "Ảnh không đọc được", true);
      } finally {
        setPreparing((n) => n - 1);
      }
    }
  };
  const remove = (i: number) => setImages((cur) => cur.filter((_, j) => j !== i));
  const clear = () => setImages([]);
  return { images, preparing, add, remove, clear };
}

export function ChatColumn() {
  const pending = usePendingImages();
  const [dragging, setDragging] = useState(false);
  // tài liệu PDF / Word thả vào ⇒ hộp hỏi dùng vào việc gì (components/desk/document-drop.tsx — ADR 0249): gắn hồ sơ đang mở / chỉ cuộc trò chuyện này /
  // đăng lên kho tri thức (chọn rõ). Không bao giờ tự vào kho.
  const [dropped, setDropped] = useState<File[]>([]);
  const [docs, setDocs] = useState<ChatDocument[]>([]);
  const hasFiles = (e: React.DragEvent) => Array.from(e.dataTransfer.types).includes("Files");
  return (
    <div
      className="relative flex h-full min-h-0 flex-col bg-background"
      onDragOver={(e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        if (!dragging) setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={(e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        setDragging(false);
        const { documents, others } = splitDrop(Array.from(e.dataTransfer.files));
        if (others.length) void pending.add(others);
        if (documents.length) setDropped(documents);
      }}
    >
      <ChatHeader />
      <Conversation />
      <Composer pending={pending} docs={docs} setDocs={setDocs} />
      {dropped.length ? (
        <DocumentDropDialog files={dropped} onClose={() => setDropped([])}
                            onKeep={(d) => setDocs((cur) => [...cur, ...d].slice(0, CHAT_DOC_MAX))} />
      ) : null}
      {dragging ? (
        <div className="pointer-events-none absolute inset-2 z-20 grid place-items-center rounded-2xl border-2 border-dashed border-primary bg-primary/10" aria-hidden>
          <span className="t14m rounded-full bg-surface px-4 py-2 text-primary shadow-sm">Thả ảnh (trợ lý đọc, tối đa {IMAGE_MAX}) hoặc tài liệu PDF / Word (hỏi dùng vào việc gì)</span>
        </div>
      ) : null}
    </div>
  );
}

function ChatHeader() {
  const desk = useDesk();
  const chat = useChat();
  const m = desk.manifest;
  const first = m?.user.name.trim().split(/\s+/).pop() ?? "bạn";
  const subtitle = m ? [m.user.role_label, m.user.team && m.user.team !== m.tenant.name ? m.user.team : "", m.tenant.name].filter(Boolean).join(" · ") : "";   // tên đội trùng tên công ty ⇒ không lặp
  const appTitle = chat.builder?.appId ? desk.screenByKey(`app-${chat.builder.appId}`)?.title : undefined;
  return (
    <header className={cn(PANE_HEADER, "px-3")}>
      <RailToggle />
      <Orb busy={!!chat.live} size={36} />
      <div className="min-w-0 flex-1">
        <h1 className="t16 truncate">
          {chat.builder ? `Trình tạo app${appTitle ? ` · ${appTitle}` : ""}` : `Trợ lý của ${first}`}
        </h1>
        <p className="t12 flex items-center gap-1.5 text-muted-foreground">
          <span className={cn("size-[7px] shrink-0 rounded-full", desk.loadError ? "bg-muted-foreground/50" : chat.builder ? "bg-primary" : "bg-ok")} />
          <span className="truncate">
            {chat.live ? "đang làm…" : chat.builder ? (chat.builder.appId ? "Đang sửa app — xem bên cạnh" : "App mới — em hỏi vài câu rồi dựng") : subtitle}
          </span>
        </p>
      </div>
      {/* trình tạo app (0098): không nút "Xong" — người dùng tự điều hướng; cuộc trò chuyện mới / mở cuộc cũ / bấm "Trợ lý" là thoát trình tạo */}
      <HeaderGroup>
      <button type="button" className={pillBtn} onClick={chat.newThread} aria-label="Cuộc trò chuyện mới" title="Cuộc trò chuyện mới">
        <MessageSquarePlus className="size-4" />
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger className={pillBtn} aria-label="Cuộc trò chuyện trước" title="Cuộc trò chuyện trước">
          <History className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="max-h-96 w-72">
          <DropdownMenuGroup>
            <DropdownMenuLabel>{chat.threads.length ? "Cuộc trò chuyện trước" : "Chưa có cuộc trò chuyện nào"}</DropdownMenuLabel>
            {chat.threads.slice(0, 20).map((t) => (
              <DropdownMenuItem key={t.id} onClick={() => void chat.openThread(t.id)}>
                <span className="line-clamp-2">{t.title || "(không tên)"}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      </HeaderGroup>
    </header>
  );
}

/** Hội thoại: bám đáy khi đang ở sát đáy (kiểu tail -f); người dùng cuộn lên đọc thì để yên. Gửi câu mới ⇒ luôn về đáy. */
function Conversation() {
  const chat = useChat();
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [away, setAway] = useState(false);
  const lastUser = chat.messages.filter((m) => m.role === "user").length;

  useLayoutEffect(() => {
    stick.current = true;
  }, [lastUser, chat.threadId]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [chat.messages, chat.live, lastUser]);

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={ref}
        className="h-full overflow-y-auto px-4 py-3.5"
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = isNearBottom(el.scrollTop, el.clientHeight, el.scrollHeight);
          setAway(!stick.current);
        }}
        role="log"
        aria-live="polite"
        aria-label="Hội thoại với trợ lý"
      >
        <div className="flex flex-col gap-3.5">
          {chat.messages.length === 0 && !chat.live ? (chat.builder ? <BuilderIntro /> : <Welcome />) : null}
          {chat.messages.map((m) => (
            <Bubble key={m.id} message={m} />
          ))}
          {chat.live ? <LiveBubble turn={chat.live} /> : null}
        </div>
      </div>
      {away && chat.live ? (
        <button
          type="button"
          onClick={() => {
            const el = ref.current;
            if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
          }}
          className="t12m absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-border bg-surface px-3 py-1.5 shadow-md"
        >
          Xuống cuối
        </button>
      ) : null}
    </div>
  );
}

/** Câu mặc định khi server chưa có `apps.builder_ideas` (bản cũ) hoặc gọi lỗi. */
export const BUILDER_IDEAS = ["Màn điều hành công nợ: còn phải thu, theo tháng đến hạn, danh sách quá hạn có nút nhắc",
                              "Việc hôm nay của đội tôi: khách cần gọi, hồ sơ kẹt, việc trễ hạn",
                              "Doanh thu và chi phí theo tháng, so với cùng kỳ"];
export const BUILDER_EDITS = ["Thêm biểu đồ xu hướng theo tháng cho số chính",
                              "Thêm bộ lọc ở đầu trang (kỳ, nhóm hay dùng nhất)",
                              "Thêm bảng các dòng cần xử lý ngay (vd quá hạn) dưới biểu đồ"];

/** Mở trình tạo app (0098): nói app để làm gì — trình tạo hỏi lại vài câu, gợi ý cách làm hay hơn rồi mới dựng (kiểu Claude Design).
 *  Ý tưởng do server gửi (`apps.builder_ideas`: hợp vai — dựng từ tập dữ liệu người này đọc được; đang sửa app ⇒ gợi ý sửa) — app Mac / iPhone dùng chung. */
function BuilderIntro() {
  const chat = useChat();
  const appId = chat.builder?.appId ?? null;
  // gắn với app đang mở: đổi app ⇒ ý tưởng cũ không còn hiện (chờ bản mới), không phải đặt lại state trong effect
  const [got, setGot] = useState<{ appId: string | null; ideas: string[] } | null>(null);
  const ideas = got && got.appId === appId ? got.ideas : null;
  useEffect(() => {
    let alive = true;
    const fallback = appId ? BUILDER_EDITS : BUILDER_IDEAS;
    api.queryRaw<{ ideas?: { text?: unknown }[] }>("apps.builder_ideas", appId ? { app_id: appId } : {}).then(
      (r) => {
        const list = (r?.ideas ?? []).map((i) => (typeof i?.text === "string" ? i.text : "")).filter(Boolean);
        if (alive) setGot({ appId, ideas: list.length ? list : fallback });
      },
      () => alive && setGot({ appId, ideas: fallback }),
    );
    return () => {
      alive = false;
    };
  }, [appId]);
  return (
    <div className="flex flex-col gap-3.5 pt-2">
      <h2 className="t20">{appId ? "Muốn sửa gì ở app này?" : "Anh/chị muốn app làm gì?"}</h2>
      <p className="t14 text-muted-foreground">
        {appId
          ? "Nói phần muốn đổi — em chỉ sửa đúng chỗ đó, app bên cạnh cập nhật ngay."
          : "Kể ngắn app để xem gì, làm gì. Em hỏi lại vài câu và gợi ý cách làm hay hơn, rồi dựng từng phần — app hiện ngay bên cạnh, đúng dữ liệu và quyền của anh/chị."}
      </p>
      <div className="flex flex-col gap-2" aria-busy={ideas === null}>
        {(ideas ?? []).map((t) => (
          <button key={t} type="button" onClick={() => chat.send(t)} className="t14 rounded-[14px] bg-surface2 px-3.5 py-2.5 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}

function Welcome() {
  const desk = useDesk();
  const chat = useChat();
  const first = desk.manifest?.user.name.trim().split(/\s+/).pop() ?? "bạn";
  return (
    <div className="flex flex-col gap-3.5 pt-2">
      <h2 className="t20">Chào {first} 👋</h2>
      {chat.morning.length ? (
        // thẻ đề xuất sáng ở BÀN VIỆC (nhóm "Cần tôi quyết", 0094) — lời chào chỉ dẫn sang, không làm chỗ thứ hai
        <button type="button" onClick={() => desk.select({ kind: "desk" })} className="flex items-center gap-2.5 rounded-[14px] bg-primary/[0.07] px-3.5 py-2.5 text-left outline-none hover:bg-primary/[0.12] focus-visible:ring-2 focus-visible:ring-ring">
          <Sparkles className="size-3.5 shrink-0 text-primary" aria-hidden />
          <span className="t14m flex-1">Em soạn sẵn {chat.morning.length} đề xuất sáng nay — xem ở Việc</span>
          <ChevronRight className="size-3 shrink-0 text-muted-foreground/70" aria-hidden />
        </button>
      ) : null}
      {chat.attention.length === 0 ? (
        <p className="t14 flex items-center gap-1.5 text-ok"><Check className="size-4" aria-hidden />Hôm nay chưa có gì gấp</p>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="t12m text-muted-foreground">Hôm nay em thấy</p>
          {chat.attention.map((a) => <AttentionRow key={a.query + a.title} item={a} />)}
        </div>
      )}
      <div className="flex flex-col gap-2">
        <p className="t12m text-muted-foreground">Hoặc hỏi em</p>
        {chat.suggestions.map((s) => (
          <button key={s} type="button" onClick={() => chat.send(s)} className="t14 flex items-center justify-between gap-2 rounded-[18px] bg-incoming px-3.5 py-2.5 text-left outline-none hover:brightness-95 focus-visible:ring-2 focus-visible:ring-ring">
            {s}
            <ArrowUp className="size-3 shrink-0 rotate-45 text-muted-foreground" aria-hidden />
          </button>
        ))}
      </div>
      <p className="t12 text-muted-foreground">Việc cần ghi, em soạn sẵn để anh/chị bấm xác nhận — em không tự làm thay.</p>
    </div>
  );
}

function AttentionRow({ item }: { item: Attention }) {
  const desk = useDesk();
  const chat = useChat();
  const open = () => {
    const spec = desk.screenFor(item.query);
    if (spec) desk.openScreen(spec);
    else if (item.ask) chat.send(item.ask);
  };
  return (
    <div className="flex items-start gap-2.5 rounded-[14px] bg-incoming px-3.5 py-2.5">
      <span className="mt-1.5 size-2 shrink-0 rounded-full" style={{ background: cardToneVar(item.tone) }} />
      <button type="button" onClick={open} className="flex min-w-0 flex-1 flex-col gap-0.5 text-left outline-none focus-visible:underline">
        <span className="t14m">{item.title}</span>
        <span className="t12 text-muted-foreground">{item.detail}</span>
      </button>
      {item.ask ? (
        <button type="button" onClick={() => chat.send(item.ask!)} aria-label="Nhờ em xử lý" title="Nhờ em xử lý" className="mt-0.5 text-primary">
          <Sparkles className="size-3.5" />
        </button>
      ) : null}
      <ChevronRight className="mt-1 size-3 shrink-0 text-muted-foreground/70" aria-hidden />
    </div>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const desk = useDesk();
  const chat = useChat();
  const [showSteps, setShowSteps] = useState(false);
  const [confirmingAll, setConfirmingAll] = useState(false);
  if (message.role === "user") {
    const imgs = message.images ?? [];
    return (
      <div className="flex justify-end pl-12">
        <div className="flex max-w-full flex-col items-end gap-1">
          {imgs.length ? (
            <div className="flex flex-wrap justify-end gap-1.5">
              {imgs.map((src, i) => (
                // eslint-disable-next-line @next/next/no-img-element -- ảnh cục bộ của phiên này (blob:), server không lưu
                <img key={src} src={src} alt={`Ảnh đã gửi ${i + 1}`} className="max-h-40 max-w-[160px] rounded-[14px] border border-border object-cover" />
              ))}
            </div>
          ) : null}
          {!imgs.length && message.attachments ? (
            <span className="t12 inline-flex items-center gap-1 rounded-full bg-surface2 px-2 py-0.5 text-muted-foreground" title="Ảnh chỉ được đọc trong lượt đó — không lưu lại">
              <ImageIcon className="size-3" aria-hidden />
              {message.attachments} ảnh (không lưu lại)
            </span>
          ) : null}
          {message.documents?.length ? (
            <div className="flex flex-wrap justify-end gap-1.5">
              {message.documents.map((n) => (
                <span key={n} className="t12 inline-flex items-center gap-1 rounded-full bg-surface2 px-2 py-0.5 text-muted-foreground" title="Chỉ dùng trong cuộc trò chuyện này — không lưu tệp">
                  <FileText className="size-3" aria-hidden />{n}
                </span>
              ))}
            </div>
          ) : null}
        <div className="max-w-full rounded-[18px] bg-cold px-3 pt-1.5 pb-1 text-white">
          <p className="t14 select-text whitespace-pre-wrap break-words">{withoutDocumentIds(message.text)}</p>
          <p className="text-right text-[10px] text-white/80">{clock(message.at)}</p>
        </div>
        </div>
      </div>
    );
  }
  if (message.role === "system") {
    return <p className="t12 mx-auto rounded-lg bg-surface2 px-2.5 py-1 text-muted-foreground">{message.text}</p>;
  }
  const target = desk.shareTarget;
  const results = resultCards(message.cards);
  // "kể lại buổi gặp" thường ra nhiều thẻ cùng lượt ⇒ một nút xác nhận hết; trợ lý chạy tiếp sau thẻ cuối (0050)
  const waiting = waitingProposals(message.cards, desk.confirmed);
  const runConfirmAll = async () => {
    setConfirmingAll(true);
    try {
      const r = await confirmAll(
        waiting,
        (card, input, key) => chat.decide(card, "confirm", input, key),
        (card) => desk.proposalEdits[card.id] ?? card.input ?? {},
        newKey,
        (card, o) => {
          desk.setConfirmed(card.id, o.message, o.approvalId);
          chat.addSystem(`✓ ${o.message}`);
        },
      );
      if (r.failed) {
        desk.show(r.failed.outcome.message || r.failed.outcome.status, true);
        desk.openCard(r.failed.card, true); // mở thẻ lỗi cho người sửa
      }
      void desk.refreshInbox();
    } finally {
      setConfirmingAll(false);
    }
  };
  const share = (): ActionSpec => {
    const last = [...message.cards].reverse().find((c) => c.kind !== "proposal" && c.query);
    return {
      command: "talk.post",
      title: `Chia sẻ vào trao đổi “${target!.title}”`,
      style: "primary",
      bind: {},
      preset: {
        subject_kind: target!.subjectKind,
        subject_id: target!.subjectId,
        ...(last ? { card: { query: last.query!, input: last.input ?? {}, title: last.title } } : {}),
      },
      when: {},
      importable: false,
    };
  };
  return (
    <div className="flex flex-col gap-2 px-1">
      {message.steps.length ? (
        <>
          <button type="button" onClick={() => setShowSteps((s) => !s)} aria-expanded={showSteps} className="t12m flex w-fit items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-1 outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <Check className="size-3.5 text-ok" aria-hidden />
            Đã làm {message.steps.length} bước
            <ChevronRight className={cn("size-3 text-muted-foreground transition-transform", showSteps && "rotate-90")} aria-hidden />
          </button>
          {showSteps ? <StepList steps={message.steps} cards={message.cards} /> : null}
        </>
      ) : null}
      <Markdown text={message.text} />
      {message.cont && chat.messages[chat.messages.length - 1]?.id === message.id && !chat.live ? (
        // lượt dừng vì chạm trần bước: làm tiếp từ chỗ dừng (cuộc trò chuyện giữ nguyên lịch sử) — câu gửi do lõi soạn
        <button type="button" className={cn(actionClass("primary"), "h-7 w-fit text-[13px]")} onClick={() => chat.send(message.cont!)}>
          <Play className="size-3.5" aria-hidden />
          Tiếp tục
        </button>
      ) : null}
      {results.map((c) =>
        // thẻ gọn ngay trong câu trả lời (0094 đợt 2) — có bản gọn server cắt sẵn thì xem / làm luôn ở đây, "Mở" mới ra khung đầy đủ
        c.kind === "result" ? (
          <ResultRow key={c.id} card={c} />
        ) : c.kind === "proposal" && c.resumable && !c.source ? (
          <ProposalRow key={c.id} card={c} />
        ) : c.preview && c.query ? (
          <CompactCard key={c.id} title={c.title} query={c.query} input={c.input ?? {}} initial={c.preview} onOpen={() => desk.openCard(c, true)} />
        ) : (
          <PreviewRow key={c.id} card={c} />
        ),
      )}
      {asideCards(message.cards).map((c) => <AsideRow key={c.id} card={c} />)}
      {waiting.length >= 2 ? (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void runConfirmAll()} disabled={confirmingAll} className={cn(actionClass("primary"), "h-7 text-[13px]")}>
            <Check className="size-3.5" aria-hidden />
            {confirmingAll ? "Đang xác nhận…" : `Xác nhận cả ${waiting.length} đề xuất`}
          </button>
          <span className="t12 text-muted-foreground">Lần lượt từng thẻ; thẻ nào lỗi sẽ mở ra để sửa.</span>
        </div>
      ) : null}
      {target && message.text ? (
        <button
          type="button"
          className="t12m inline-flex w-fit items-center gap-1.5 rounded-lg border border-border bg-surface px-2 py-1 hover:bg-accent"
          title="Câu trả lời này chỉ mình bạn thấy — bấm để gửi vào trao đổi của hồ sơ (bạn xem lại chữ trước khi gửi)"
          onClick={() => desk.openForm(share(), { initial: { body: message.text } })}
        >
          <Share2 className="size-3.5" aria-hidden />
          Chia sẻ vào trao đổi “{target.title}”
        </button>
      ) : null}
      <time className="text-[10px] text-muted-foreground">{clock(message.at)}</time>
    </div>
  );
}

function StepList({ steps, cards }: { steps: ChatMessage["steps"]; cards: ChatCard[] }) {
  const desk = useDesk();
  return (
    <ul className="flex flex-col gap-2 rounded-[10px] bg-foreground/5 p-2.5">
      {steps.map((s) => {
        const card = cards.find((c) => c.id === s.card_id && c.kind !== "result"); // thẻ "đã tự làm" nằm ngay trong câu trả lời, không có trang để mở
        const done = stepDone(s);
        const detail = s.error || s.summary;
        const row = (
          <>
            <span className="mt-0.5 shrink-0">
              {done === null ? <span className="block size-3.5 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary" /> : done ? <Check className="size-3.5 text-ok" /> : <TriangleAlert className="size-3.5 text-warn" />}
            </span>
            <span className="flex min-w-0 flex-1 flex-col text-left">
              <span className="t12m">{s.title}</span>
              {detail ? <span className="t12 line-clamp-3 text-muted-foreground">{detail}</span> : null}
            </span>
          </>
        );
        return (
          <li key={s.n}>
            {card ? (
              <button type="button" className="flex w-full items-start gap-2 outline-none focus-visible:underline" title="Mở kết quả bước này trên khung làm việc" onClick={() => desk.openCard(card, true)}>
                {row}
                <ArrowUp className="mt-0.5 size-3.5 shrink-0 rotate-45 text-primary" aria-hidden />
              </button>
            ) : (
              <div className="flex items-start gap-2">{row}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Một thẻ trợ lý đã tạo — như "xem trước liên kết"; bấm để đưa lên khung làm việc. */
function PreviewRow({ card }: { card: ChatCard }) {
  const desk = useDesk();
  const proposal = card.kind === "proposal";
  const done = desk.settled(card)?.label;
  const op = card.query ?? card.command ?? "";
  const sub = done
    ? done
    : proposal
      ? "Chưa ghi gì — bấm để xem lại và xác nhận"
      : card.page?.kind === "table"
        ? `${card.subtitle ? card.subtitle + " · " : ""}${card.page.rows.length} dòng`
        : card.subtitle || "Mở trên khung làm việc";
  return (
    <button type="button" onClick={() => desk.openCard(card, true)} className="flex items-center gap-3 rounded-[14px] border border-border bg-surface px-2.5 py-2 text-left outline-none hover:bg-surface2 focus-visible:ring-2 focus-visible:ring-ring">
      <IconTile icon={proposal ? "sparkles" : "list.bullet.rectangle"} tint={proposal ? "#af52de" : moduleTint(op)} size={34} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="t14m truncate">{proposal ? `Đề xuất: ${card.title}` : card.title}</span>
        <span className={cn("t12 truncate", done ? "text-ok" : "text-muted-foreground")}>{sub}</span>
      </span>
      <span className="grid size-[26px] shrink-0 place-items-center rounded-full bg-foreground/5"><ArrowUp className="size-3 rotate-45" aria-hidden /></span>
    </button>
  );
}

/**
 * Gợi ý BÊN LỀ (vd ghi nhớ): trợ lý đã trả lời xong, không chờ — một dòng nhỏ, bấm Ghi nhớ / Thôi ngay tại chỗ (qua `proposals/decide`: chạy
 * bằng quyền người, giữ nguồn là lượt này, "Thôi" thành tín hiệu bỏ). Bấm chữ ⇒ mở thẻ để sửa trước khi ghi.
 */
function AsideRow({ card }: { card: ChatCard }) {
  const desk = useDesk();
  const chat = useChat();
  const [busy, setBusy] = useState<"confirm" | "dismiss" | null>(null);
  const done = desk.settled(card)?.label;
  const decide = async (d: "confirm" | "dismiss") => {
    setBusy(d);
    try {
      const o = await chat.decide(card, d, d === "confirm" ? desk.proposalEdits[card.id] ?? card.input ?? {} : {}, newKey());
      if (o.done) desk.setConfirmed(card.id, d === "confirm" ? o.message || "Đã xong" : "Đã bỏ qua");
      else {
        desk.show(o.message || o.status, true);
        desk.openCard(card, true); // lỗi (vd khách đã chuyển người) ⇒ mở thẻ để xem / sửa
      }
    } finally {
      setBusy(null);
    }
  };
  const settled = done !== undefined;
  const dropped = done === "Đã bỏ qua" || done === "Đã bỏ đề xuất"; // mở lại cuộc trò chuyện cũ: server ghi "Đã bỏ đề xuất"
  const verb = card.verb || "Đồng ý";
  return (
    <div className={cn("flex items-center gap-2.5 rounded-[12px] border border-dashed border-border px-2.5 py-1.5", settled && "opacity-70")}>
      <Brain className="size-4 shrink-0" style={{ color: "#ff2d55" }} aria-hidden />
      <button type="button" onClick={() => desk.openCard(card, true)} className="t12 min-w-0 flex-1 truncate text-left outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring" title={card.title}>
        {card.title}
      </button>
      {settled ? (
        <span className={cn("t12m shrink-0", dropped ? "text-muted-foreground" : "text-ok")}>{dropped ? "Đã bỏ qua" : `✓ Đã ${verb.toLowerCase()}`}</span>
      ) : (
        <span className="flex shrink-0 items-center gap-1.5">
          <button type="button" disabled={busy !== null} onClick={() => void decide("confirm")} className={cn(actionClass("primary"), "h-6 px-2 text-[12px]")}>
            {busy === "confirm" ? "Đang làm…" : verb}
          </button>
          <button type="button" disabled={busy !== null} onClick={() => void decide("dismiss")} className="t12m h-6 rounded-md px-2 text-muted-foreground hover:bg-accent disabled:opacity-50">
            Thôi
          </button>
        </span>
      )}
    </div>
  );
}

function LiveBubble({ turn }: { turn: LiveTurn }) {
  return (
    <div className="flex flex-col gap-2 px-1">
      <p className="flex items-center gap-2">
        <Sparkles className="size-3.5 text-primary" aria-hidden />
        <span className="t12m animate-[barista-shimmer_1.6s_linear_infinite] bg-[linear-gradient(90deg,var(--muted-foreground)_40%,var(--foreground)_50%,var(--muted-foreground)_60%)] bg-[length:200%_100%] bg-clip-text text-transparent">{turn.status}</span>
      </p>
      {turn.steps.length ? (
        <ul className="flex flex-col gap-1">
          {turn.steps.map((s) => {
            const done = stepDone(s);
            const d = s.error || s.summary;
            return (
              <li key={s.n} className="t12 flex items-center gap-1.5">
                {done === null ? <span className="block size-3 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary" /> : done ? <Check className="size-3.5 text-primary" /> : <CircleAlert className="size-3.5 text-warn" />}
                <span className="truncate">{s.title}</span>
                {d ? <span className="truncate text-muted-foreground">· {d}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {turn.text ? <Markdown text={turn.text} /> : <span className="flex gap-1 py-1.5" aria-label="Đang soạn">{[0, 1, 2].map((i) => <span key={i} className="size-1.5 rounded-full bg-muted-foreground animate-[barista-dot_1.2s_ease-in-out_infinite]" style={{ animationDelay: `${i * 0.2}s` }} />)}</span>}
    </div>
  );
}

function Composer({ pending, docs, setDocs }: {
  pending: ReturnType<typeof usePendingImages>; docs: ChatDocument[]; setDocs: (d: ChatDocument[]) => void;
}) {
  const desk = useDesk();
  const chat = useChat();
  const [draft, setDraft] = useState("");
  const ta = useRef<HTMLTextAreaElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const withImages = pending.images.length > 0;
  const has = draft.trim().length > 0 || withImages || docs.length > 0;
  const busy = !!chat.live || pending.preparing > 0;

  // "Hỏi trợ lý…" từ thẻ ⇒ câu mở đầu vào ô, con trỏ ở cuối
  const n = desk.askPrompt?.n ?? 0;
  const [seenAsk, setSeenAsk] = useState(0);
  if (n !== seenAsk) {
    setSeenAsk(n);
    if (desk.askPrompt) setDraft(desk.askPrompt.text);
  }
  useEffect(() => {
    const el = ta.current;
    if (n > 0 && el) {
      el.focus();
      requestAnimationFrame(() => el.setSelectionRange(el.value.length, el.value.length));
    }
  }, [n]);

  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
  }, [draft]);

  const submit = () => {
    if (!has || busy) return;
    chat.send(draft, pending.images, docs);
    setDraft("");
    pending.clear();
    setDocs([]);
  };

  return (
    // ô nhập NỔI trên nền màn (kiểu ChatGPT, anh Anh 18/11): không dải nền / đường kẻ riêng; ảnh · chữ · mic · gửi nằm trong MỘT viên
    <div className="flex flex-col gap-1.5 px-3 pt-1 pb-3">
      {chat.messages.length > 0 && !has && !busy && chat.suggestions.length ? (
        <div className="no-scrollbar flex gap-1.5 overflow-x-auto">
          {chat.suggestions.map((s) => (
            <button key={s} type="button" onClick={() => chat.send(s)} className="t12 shrink-0 rounded-full border border-border bg-surface px-2.5 py-1.5 whitespace-nowrap hover:bg-accent">
              {s}
            </button>
          ))}
        </div>
      ) : null}
      {docs.length ? (
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap gap-1.5 pt-1.5" aria-label="Tài liệu gửi kèm">
            {docs.map((d, i) => (
              <span key={d.filename + i} className="t12 inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-1">
                <FileText className="size-3.5 text-primary" aria-hidden />
                <span className="max-w-[200px] truncate">{d.filename}</span>
                <button type="button" onClick={() => setDocs(docs.filter((_, j) => j !== i))} aria-label={`Bỏ ${d.filename}`} title="Bỏ tài liệu"
                        className="grid size-4 place-items-center rounded-full text-muted-foreground hover:bg-accent">
                  <X className="size-3" strokeWidth={3} />
                </button>
              </span>
            ))}
          </div>
          <p className="t12 text-muted-foreground">Trợ lý đọc để trả lời trong cuộc trò chuyện này — tệp không lưu, không vào kho tri thức</p>
        </div>
      ) : null}
      {withImages || pending.preparing > 0 ? (
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap gap-2 pt-1.5" aria-label="Ảnh gửi kèm">
            {pending.images.map((img, i) => (
              <span key={img.preview} className="relative">
                {/* eslint-disable-next-line @next/next/no-img-element -- ảnh xem trước cục bộ (blob:), không qua tối ưu ảnh của Next */}
                <img src={img.preview} alt={`Ảnh gửi kèm ${i + 1}`} className="size-16 rounded-xl border border-border object-cover" />
                <button
                  type="button"
                  onClick={() => pending.remove(i)}
                  className="absolute -top-1.5 -right-1.5 grid size-5 place-items-center rounded-full bg-foreground/80 text-background shadow"
                  aria-label={`Bỏ ảnh ${i + 1}`}
                  title="Bỏ ảnh"
                >
                  <X className="size-3" strokeWidth={3} />
                </button>
              </span>
            ))}
            {Array.from({ length: pending.preparing }).map((_, i) => (
              <span key={`p${i}`} className="grid size-16 place-items-center rounded-xl border border-dashed border-border" aria-label="Đang nén ảnh">
                <span className="block size-4 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary" />
              </span>
            ))}
          </div>
          <p className="t12 text-muted-foreground">Trợ lý đọc ảnh rồi soạn đề xuất — ảnh không được lưu lại</p>
        </div>
      ) : null}
      <div className={COMPOSER_PILL}>
        <input
          ref={file}
          type="file"
          accept="image/*,.heic,.heif"
          multiple
          className="sr-only"
          aria-label="Chọn ảnh gửi trợ lý"
          onChange={(e) => {
            void pending.add(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => file.current?.click()}
          disabled={pending.images.length >= IMAGE_MAX}
          className="grid size-9 shrink-0 place-items-center rounded-full text-muted-foreground outline-none hover:bg-foreground/[0.08] hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40"
          aria-label="Gửi ảnh cho trợ lý (đoạn chat, phiếu, giấy tờ)"
          title="Gửi ảnh cho trợ lý — dán hoặc kéo thả ảnh cũng được"
        >
          <ImagePlus className="size-[18px]" />
        </button>
        <textarea
          ref={ta}
          value={draft}
          rows={1}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onPaste={(e) => {
            // chỉ chặn khi bảng nháp có FILE ảnh — dán chữ / ô Excel vẫn như thường
            const imgs = imageFilesOf(Array.from(e.clipboardData.items));
            if (!imgs.length) return;
            e.preventDefault();
            void pending.add(imgs);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={withImages ? "Nhờ trợ lý làm gì với ảnh này…" : "Hỏi trợ lý…"}
          aria-label="Hỏi trợ lý"
          className={COMPOSER_TEXT}
        />
        {/* mic ĐỌC CHÍNH TẢ trong ô nhập (kiểu ChatGPT): nói xong chữ hiện vào ô — người xem / sửa rồi tự gửi */}
        <DictateMic onText={(text) => { setDraft((d) => (d.trim() ? `${d.trimEnd()} ${text}` : text)); ta.current?.focus(); }} />
        {/* đang trả lời ⇒ nút gửi thành "Dừng": huỷ lượt ở server (phần đã làm vẫn lưu, lệnh đã chạy không hoàn tác) — cùng nút với Soạn với AI */}
        <SendStopButton canSend={has} running={!!chat.live || busy} canStop={!!chat.live && chat.canStop} onSend={submit} onStop={chat.stop} />
      </div>
    </div>
  );
}
