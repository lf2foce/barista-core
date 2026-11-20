"use client";

import { ArrowDown, ArrowUp, AtSign, Bell, BellOff, CircleCheck, CornerUpLeft, FileText, Files as FilesIcon, ListPlus, MessagesSquare, MoreHorizontal, Paperclip, Pencil, Pin, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, ApiError, listenSignals } from "@/lib/api";
import { newKey } from "@/lib/idempotency";
import * as fmt from "@/lib/format";
import { fileSize, isDirect, isRoom, mergeOlder, splitMentions, stamp, talkFileUrl } from "@/lib/talk";
import type { ActionSpec, Input, Option, ThreadBlockData, ThreadMsg } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { actionClass, ErrorLine, Initials } from "@/components/primitives";
import { useCallHub } from "@/components/call/call-provider";
import { useDesk } from "@/components/desk/desk-context";
import { ThreadFiles } from "./thread-files";

/**
 * Khối "Trao đổi" (quyết định 0046, 0115, Talk.swift): tin nhắn gắn vào một hồ sơ / khách, một phòng nhóm hoặc tin riêng 1–1. Server quyết ai đọc
 * được và nút nào hiện; web chỉ vẽ. @nhắc chỉ nhắc được người vốn xem được hồ sơ — server từ chối kèm lý do. "Hỏi trợ lý" / "Tóm tắt" là hỏi
 * RIÊNG (quyền mình); muốn cả luồng thấy thì bấm "Chia sẻ vào trao đổi" dưới câu trả lời. Tin mới tới qua SSE /v1/talk/{thread}/stream (không mang
 * nội dung ⇒ đọc lại qua quyền người dùng); mất quyền đọc ⇒ server báo `gone`, khối thôi hiện tin.
 *
 * Hai cách vẽ: khối nằm trong thẻ (mặc định) và `fill` — luồng chiếm cả cột "Tin nhắn" (tin cuộn, ô nhập ghim đáy).
 */
export function ThreadBlock({
  block,
  fill = false,
  onOpenCard,
  jump,
}: {
  block: ThreadBlockData;
  fill?: boolean;
  /** Bấm thẻ đã chia sẻ trong tin — mặc định mở hộp thoại; khung trò chuyện cỡ lớn mở vào ngăn kiểm tra bên cạnh. */
  onOpenCard?: (query: string, input: Input, title?: string) => void;
  /** Mở luồng quanh một tin (bấm một dòng ở ngăn "Tệp" bên cạnh — 0128); đối tượng mới mỗi lần bấm. */
  jump?: { id: number } | null;
}) {
  const th = useThread(block, onOpenCard);
  const { jumpTo } = th;
  useEffect(() => {
    if (jump) jumpTo(jump.id);
  }, [jump, jumpTo]);
  if (th.gone) return <Gone fill={fill} />;
  return fill ? <Filled th={th} /> : <Boxed th={th} />;
}

type ThreadState = ReturnType<typeof useThread>;

function useThread(block: ThreadBlockData, onOpenCard?: (query: string, input: Input, title?: string) => void) {
  const desk = useDesk();
  const { refreshTalk } = desk;
  const [data, setData] = useState<ThreadBlockData | null>(null);
  const [older, setOlder] = useState<ThreadMsg[]>([]);
  const [olderMore, setOlderMore] = useState<boolean | null>(null);
  const [gone, setGone] = useState(false);
  const [seenBlock, setSeenBlock] = useState(block);
  if (seenBlock !== block) {
    // trang tải lại ⇒ bỏ bản làm mới cục bộ, dùng dữ kiện mới của server
    setSeenBlock(block);
    setData(null);
    setOlder([]);
    setOlderMore(null);
    setGone(false);
  }
  const [draft, setDraft] = useState("");
  const [mentions, setMentions] = useState<Option[]>([]);
  const [replyTo, setReplyTo] = useState<ThreadMsg | null>(null);
  const [editing, setEditing] = useState<ThreadMsg | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sendKey = useRef(newKey());
  const marked = useRef(0);
  // mở từ kết quả tìm (around): tải lại vẫn giữ cửa sổ quanh tin đó tới khi người bấm "Về tin mới nhất"
  const around = useRef<number | null>(block.focus_id);
  const t = data ?? block;
  const messages = mergeOlder(older, t.messages);
  const firstUnread = messages.find((m) => !m.mine && !m.recalled && m.id > t.last_read)?.id;
  const hasMore = olderMore ?? t.has_more;

  const fetchBlock = useCallback(
    async (extra: Input = {}): Promise<ThreadBlockData | null> => {
      try {
        const p = await api.query("talk.thread", { subject_kind: block.subject_kind, subject_id: block.subject_id, ...extra });
        if (p.kind !== "card") return null;
        const fresh = p.blocks.find((b) => b.type === "thread");
        return fresh && fresh.type === "thread" ? fresh : null;
      } catch (e) {
        // mất quyền xem hồ sơ / bị mời khỏi phòng ⇒ thôi hiện tin cũ đang có trên màn
        if (e instanceof ApiError && e.status === 404) setGone(true);
        return null;
      }
    },
    [block.subject_kind, block.subject_id],
  );

  const refresh = useCallback(async () => {
    const fresh = await fetchBlock(around.current ? { around: around.current } : {});
    if (fresh) setData(fresh);
  }, [fetchBlock]);

  // gone ⇒ thôi hiện tin (mất quyền thật); phiên hết hạn ⇒ listenSignals đưa về đăng nhập; lỗi tạm thời ⇒ nối lại, tin đang hiện giữ nguyên
  useEffect(() => {
    if (!t.thread_id) return;
    return listenSignals(`/v1/talk/${t.thread_id}/stream`, () => void refresh(), () => void refresh(), () => setGone(true));
  }, [t.thread_id, refresh]);

  // quay lại tab này (vd sau cuộc gọi mở ở thẻ khác): đọc lại để nút Gọi / Tham gia và thẻ cuộc gọi khớp trạng thái thật
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refresh]);

  // cuộc gọi chạy trong bảng nổi ngay trên trang này: vào / nối được / kết thúc ⇒ đọc lại để thẻ cuộc gọi và nút Gọi / Tham gia khớp trạng thái thật
  const callHub = useCallHub();
  useEffect(() => {
    if (!callHub) return;
    let last = callHub.getSnapshot().phase;
    return callHub.subscribe(() => {
      const p = callHub.getSnapshot().phase;
      if (p === last) return;
      last = p;
      void refresh();
    });
  }, [callHub, refresh]);

  const lastId = t.messages[t.messages.length - 1]?.id;
  useEffect(() => {
    if (!t.thread_id || !lastId || lastId <= t.last_read || lastId <= marked.current || t.has_newer) return;
    marked.current = lastId;
    void api
      .command("talk.mark_read", { thread_id: t.thread_id, upto: lastId }, newKey())
      .then(() => refreshTalk()) // số chưa đọc ở cột "Tin nhắn" / thanh bên trái
      .catch(() => null);
  }, [t.thread_id, lastId, t.last_read, t.has_newer, refreshTalk]);

  async function loadOlder() {
    const first = messages[0]?.id;
    if (!first) return;
    const page = await fetchBlock({ before: first });
    if (!page) return;
    setOlder((o) => mergeOlder(page.messages, o));
    setOlderMore(page.has_more);
  }

  function jumpLatest() {
    around.current = null;
    setOlder([]);
    setOlderMore(null);
    void refresh();
  }

  /** Mở cửa sổ tin quanh một tin (từ tab "Tệp" — 0128): tải lại vẫn giữ cửa sổ đó tới khi bấm "Về tin mới nhất". */
  const jumpTo = useCallback(
    (id: number) => {
      around.current = id;
      setOlder([]);
      setOlderMore(null);
      void refresh();
    },
    [refresh],
  );

  const done = async (r: { status: string; error: string }) => {
    if (r.status === "ok") {
      setError(null);
      sendKey.current = newKey();
      await refresh();
      void refreshTalk();
      return true;
    }
    setError(r.error || r.status);
    return false;
  };

  async function send() {
    const body = draft.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      // cùng khoá khi bấm lại lúc mạng chập ⇒ không thành hai tin
      const r = editing
        ? await api.command("talk.edit_message", { message_id: editing.id, body }, sendKey.current)
        : await api.command(
            "talk.post",
            { subject_kind: t.subject_kind, subject_id: t.subject_id, body, mentions: mentions.map((m) => m.value), ...(replyTo ? { reply_to: replyTo.id } : {}) },
            sendKey.current,
          );
      if (await done(r)) {
        setDraft("");
        setMentions([]);
        setReplyTo(null);
        setEditing(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không gửi được");
    } finally {
      setSending(false);
    }
  }

  async function sendFile(file: File) {
    if (sending) return;
    if (t.file_max && file.size > t.file_max) {
      setError(`Tệp quá ${Math.round(t.file_max / (1024 * 1024))} MB`);
      return;
    }
    setSending(true);
    try {
      // chữ đang gõ thành chú thích của tệp; server kiểm loại theo nội dung
      const r = await api.talkUpload(file, { subject_kind: t.subject_kind, subject_id: t.subject_id, body: draft.trim(), reply_to: replyTo?.id ?? null }, sendKey.current);
      if (await done(r)) {
        setDraft("");
        setReplyTo(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không gửi được tệp");
    } finally {
      setSending(false);
    }
  }

  /** Thao tác nhỏ trên một tin (cảm xúc, ghim, thu hồi, tắt tiếng) — một lệnh, khoá chống lặp riêng, xong thì đọc lại luồng. */
  async function run(command: string, input: Input) {
    try {
      const r = await api.command(command, input, newKey());
      if (r.status !== "ok") setError(r.error || r.status);
      else await refresh();
      void refreshTalk();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không thực hiện được");
    }
  }

  const react = (m: ThreadMsg, emoji: string) => run("talk.react", { message_id: m.id, emoji, on: !m.reactions.some((r) => r.emoji === emoji && r.mine) });
  const pin = (m: ThreadMsg) => run("talk.pin", { message_id: m.id, pinned: !m.pinned });
  const recall = (m: ThreadMsg) => {
    if (window.confirm("Thu hồi tin này? Mọi người sẽ thấy “Tin đã được thu hồi”; nội dung gốc vẫn được lưu lại.")) void run("talk.recall_message", { message_id: m.id });
  };
  const mute = () => (t.thread_id ? run("talk.mute", { thread_id: t.thread_id, muted: !t.muted }) : undefined);
  const startEdit = (m: ThreadMsg) => {
    setReplyTo(null);
    setEditing(m);
    setDraft(m.body);
    sendKey.current = newKey();
  };
  const cancel = () => {
    if (editing) setDraft("");
    setEditing(null);
    setReplyTo(null);
  };
  const history = (m: ThreadMsg) => m.history && desk.openPage(m.history.query, m.history.input, "Lịch sử tin nhắn");

  const askPrivately = (prompt?: string) => {
    desk.setShareTarget({ subjectKind: t.subject_kind, subjectId: t.subject_id, title: t.subject_title });
    desk.ask(prompt ?? `Về ${t.subject_title}: `);
  };

  /** Nút trên thẻ sống (0089): form điền sẵn, chạy bằng quyền người bấm; xong ⇒ tải lại + báo máy khác đang mở luồng tính lại thẻ. */
  function act(a: ActionSpec) {
    desk.openForm(a, {
      initial: a.preset,
      onDone: (msg) => {
        desk.show(msg);
        void refresh();
        if (t.thread_id) void api.command("talk.cards_changed", { thread_id: t.thread_id }, newKey()).catch(() => undefined);
      },
    });
  }

  return {
    t, messages, gone, hasMore, firstUnread, lastId, draft, setDraft, mentions, setMentions, replyTo, setReplyTo, editing, sending, error,
    send, sendFile, loadOlder, jumpLatest, jumpTo, react, pin, recall, mute, startEdit, cancel, history, askPrivately, act, openCard: onOpenCard ?? desk.openPage,
  };
}

function Body({ text }: { text: string }) {
  return (
    <>
      {splitMentions(text).map((p, i) =>
        p.mention ? (
          <span key={i} className="font-medium text-primary">
            {p.text}
          </span>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}

const Divider = () => (
  <div className="t12m my-1.5 flex w-full items-center gap-2 text-bad" role="separator">
    <span className="h-px flex-1 bg-bad/40" />
    Tin mới
    <span className="h-px flex-1 bg-bad/40" />
  </div>
);

function Gone({ fill }: { fill: boolean }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-1 px-6 text-center", fill ? "flex-1" : "rounded-xl bg-surface2 p-4")} role="status">
      <p className="t14m">Bạn không còn xem được trao đổi này</p>
      <p className="t12 text-muted-foreground">Có thể hồ sơ đã đổi người phụ trách hoặc anh/chị đã rời phòng.</p>
    </div>
  );
}

function CardRef({ m, open, act }: { m: ThreadMsg; open: (q: string, input: Input, title?: string) => void; act: (a: ActionSpec) => void }) {
  if (!m.card) return null;
  const c = m.card;
  const status = c.live ? (c.live.gone ? "Bạn không xem được thẻ này" : c.live.subtitle) : "";
  // tên thẻ theo người xem (vd "A gọi cho bạn") do server quyết; không có thì tên trung tính lưu lúc chia sẻ
  const title = (!c.live?.gone && c.live?.headline) || c.title;
  return (
    <div className="mt-1 flex flex-col items-start gap-1.5">
      <button type="button" className="flex flex-col items-start rounded-lg border border-border bg-surface px-2 py-1 text-left hover:bg-accent" title="Mở thẻ — số liệu chạy lại theo quyền của bạn" onClick={() => open(c.query, c.input, title)}>
        <span className="t12m">{title}</span>
        {status ? <span className="t12 text-muted-foreground">{status}</span> : null}
      </button>
      {c.live?.actions.length ? (
        <div className="flex gap-1.5">
          {c.live.actions.map((a) => (
            <button key={a.command + a.title} type="button" className={cn(actionClass(a.style), "h-7 text-[13px]")} onClick={() => act(a)}>{a.title}</button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Tệp trong tin: ảnh hiện ngay (bấm mở cỡ thật), loại khác là thẻ tên + cỡ, bấm tải. Đường tải qua BFF — server kiểm quyền mỗi lần. */
function Files({ m, mine }: { m: ThreadMsg; mine: boolean }) {
  if (!m.attachments.length) return null;
  return (
    <div className={cn("mt-1 flex flex-col gap-1", mine ? "items-end" : "items-start")}>
      {m.attachments.map((f) =>
        f.is_image ? (
          <a key={f.id} href={talkFileUrl(f.id)} target="_blank" rel="noreferrer" title={`${f.filename} · ${fileSize(f.size_bytes)}`}>
            {/* eslint-disable-next-line @next/next/no-img-element -- ảnh qua BFF có cookie, không qua bộ tối ưu ảnh */}
            <img src={talkFileUrl(f.id)} alt={f.filename} className="max-h-60 max-w-[280px] rounded-xl border border-border object-cover" loading="lazy" />
          </a>
        ) : (
          <a key={f.id} href={talkFileUrl(f.id)} target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 hover:bg-accent" title="Tải tệp">
            <FileText className="size-4 shrink-0 text-primary" aria-hidden />
            <span className="flex min-w-0 flex-col">
              <span className="t12m max-w-[220px] truncate">{f.filename}</span>
              <span className="t12 text-muted-foreground">{fileSize(f.size_bytes)}</span>
            </span>
          </a>
        ),
      )}
    </div>
  );
}

function Reply({ m }: { m: ThreadMsg }) {
  if (!m.reply) return null;
  return (
    <div className="t12 mb-0.5 max-w-full truncate border-l-2 border-primary/50 pl-2 text-muted-foreground">
      <span className="font-medium">{m.reply.author}</span>: {m.reply.body}
    </div>
  );
}

function Reactions({ th, m }: { th: ThreadState; m: ThreadMsg }) {
  if (!m.reactions.length) return null;
  return (
    <div className="mt-0.5 flex flex-wrap gap-1">
      {m.reactions.map((r) => (
        <button
          key={r.emoji}
          type="button"
          disabled={!th.t.can_post}
          onClick={() => void th.react(m, r.emoji)}
          title={r.names.join(", ")}
          aria-pressed={r.mine}
          className={cn("t12 inline-flex h-6 items-center gap-1 rounded-full border px-2", r.mine ? "border-primary/50 bg-primary/12 text-primary" : "border-border bg-surface hover:bg-accent")}
        >
          <span aria-hidden>{r.emoji}</span>
          <span className="tnum">{r.count}</span>
        </button>
      ))}
    </div>
  );
}

/** Nút "…" của một tin: trả lời, thả cảm xúc, ghim, sửa / thu hồi (tin của mình), lịch sử, nút gói khác góp cho tin (vd "Tạo việc") — chỉ hiện việc
 *  server cho phép. */
function MessageMenu({ th, m }: { th: ThreadState; m: ThreadMsg }) {
  const write = th.t.can_post && !m.recalled;
  if (!write && !m.history && !m.actions.length) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="grid size-6 shrink-0 place-items-center rounded-md text-muted-foreground opacity-0 outline-none group-hover:opacity-100 hover:bg-foreground/10 focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring data-[popup-open]:opacity-100"
        aria-label="Thao tác với tin này"
        title="Thao tác"
      >
        <MoreHorizontal className="size-4" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-52">
        {write ? (
          <DropdownMenuGroup>
            <DropdownMenuLabel>Cảm xúc</DropdownMenuLabel>
            <div className="flex gap-1 px-1.5 pb-1">
              {th.t.reaction_choices.map((e) => (
                <DropdownMenuItem key={e} className="justify-center px-2 text-[16px]" onClick={() => void th.react(m, e)} aria-label={`Thả ${e}`}>
                  {e}
                </DropdownMenuItem>
              ))}
            </div>
          </DropdownMenuGroup>
        ) : null}
        <DropdownMenuGroup>
          {write ? (
            <DropdownMenuItem onClick={() => { th.setReplyTo(m); }}>
              <CornerUpLeft className="size-4" /> Trả lời
            </DropdownMenuItem>
          ) : null}
          {write ? (
            <DropdownMenuItem onClick={() => void th.pin(m)}>
              <Pin className="size-4" /> {m.pinned ? "Bỏ ghim" : "Ghim tin"}
            </DropdownMenuItem>
          ) : null}
          {m.can_edit ? (
            <DropdownMenuItem onClick={() => th.startEdit(m)}>
              <Pencil className="size-4" /> Sửa tin
            </DropdownMenuItem>
          ) : null}
          {m.history ? <DropdownMenuItem onClick={() => th.history(m)}>Xem lịch sử</DropdownMenuItem> : null}
        </DropdownMenuGroup>
        {m.actions.length ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              {m.actions.map((a) => (
                <DropdownMenuItem key={a.command + a.title} onClick={() => th.act(a)}>
                  <ListPlus className="size-4" /> {a.title}
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          </>
        ) : null}
        {m.can_edit ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem variant="destructive" onClick={() => th.recall(m)}>
                Thu hồi tin
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Dấu nhỏ dưới tin (vd "Đã tạo việc · <việc> · <trạng thái>") — server chỉ gửi cho người thấy thứ dấu dẫn tới; bấm mở thẻ đó. */
function Marks({ th, m }: { th: ThreadState; m: ThreadMsg }) {
  if (!m.marks.length) return null;
  return (
    <div className={cn("mt-0.5 flex max-w-full flex-wrap gap-1", m.mine && "justify-end")}>
      {m.marks.map((k, i) => (
        <button
          key={i}
          type="button"
          disabled={!k.open}
          onClick={() => k.open && th.openCard(k.open.query, k.open.input, k.detail || k.title)}
          title={k.detail || k.title}
          className={cn(
            "t12 inline-flex h-6 max-w-[280px] items-center gap-1 rounded-full border px-2 hover:bg-accent disabled:hover:bg-transparent",
            k.tone === "ok" ? "border-ok/40 text-ok" : "border-primary/40 text-primary",
          )}
        >
          <CircleCheck className="size-3 shrink-0" aria-hidden />
          <span className="truncate">{k.title}{k.detail ? ` · ${k.detail}` : ""}</span>
        </button>
      ))}
    </div>
  );
}

function Stamp({ m, fill }: { m: ThreadMsg; fill: boolean }) {
  return (
    <span className="mt-0.5 flex items-center gap-1 text-[10px] text-muted-foreground/80">
      {m.pinned ? <Pin className="size-2.5" aria-label="Đã ghim" /> : null}
      <time dateTime={m.at} title={fmt.datetime(m.at)}>
        {fill ? stamp(m.at) : fmt.datetime(m.at)}
      </time>
      {m.edited ? <span>· đã sửa</span> : null}
    </span>
  );
}

/** Bong bóng tin: trả lời trích dẫn, chữ (tin thu hồi vẽ nhạt), tệp, thẻ, cảm xúc, giờ. */
function Bubble({ th, m, incoming }: { th: ThreadState; m: ThreadMsg; incoming: string }) {
  return (
    <>
      <Reply m={m} />
      <div className={cn("flex items-start gap-1", m.mine && "flex-row-reverse")}>
        {m.body || m.recalled ? (
          <p
            id={`msg-${m.id}`}
            className={cn(
              "t14 max-w-full select-text whitespace-pre-wrap break-words rounded-[14px] px-2.5 py-1.5",
              m.recalled ? "border border-dashed border-border bg-transparent text-muted-foreground" : m.mine ? "bg-primary/15" : incoming,
              m.mentions_me && !m.recalled && "ring-[1.5px] ring-warn/70",
              th.t.focus_id === m.id && "ring-2 ring-primary/60",
            )}
          >
            {m.recalled ? m.body : <Body text={m.body} />}
          </p>
        ) : (
          <span id={`msg-${m.id}`} />
        )}
        <MessageMenu th={th} m={m} />
      </div>
      <Files m={m} mine={m.mine} />
      <CardRef m={m} open={th.openCard} act={th.act} />
      <Marks th={th} m={m} />
      <Reactions th={th} m={m} />
    </>
  );
}

/** Nút gói khác góp lên khối (vd "Gọi" của gói Gọi video) — server đã lọc theo quyền; bấm = form điền sẵn như nút trên thẻ sống. */
function BlockActions({ th }: { th: ThreadState }) {
  if (!th.t.actions.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {th.t.actions.map((a) => (
        <button key={a.command + a.title} type="button" className={cn(actionClass(a.style), "h-7 text-[13px]")} onClick={() => th.act(a)}>
          {a.title}
        </button>
      ))}
    </div>
  );
}

/** Nút "Tệp" (0128) của khối nằm trong thẻ: server khai `files` khi luồng đã có tin; bấm ⇒ thay danh sách tin bằng tab Tệp. */
function FilesButton({ th, open, onClick }: { th: ThreadState; open: boolean; onClick: () => void }) {
  if (!th.t.files) return null;
  return (
    <button type="button" onClick={onClick} aria-pressed={open} title="Ảnh, tài liệu, link đã gửi"
            className={cn("t12m inline-flex h-7 items-center gap-1 rounded-lg border px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          open ? "border-primary/40 bg-primary/12 text-primary" : "border-border bg-surface hover:bg-accent")}>
      <FilesIcon className="size-3.5" aria-hidden /> {th.t.files.title || "Tệp"}
    </button>
  );
}

/** "Tóm tắt" (0115): câu hỏi do server soạn, gửi cho trợ lý RIÊNG của người bấm — trả lời không vào luồng chung. */
function AssistMenu({ th }: { th: ThreadState }) {
  if (!th.t.assist.length || !th.messages.length) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="t12m inline-flex h-7 items-center gap-1 rounded-lg border border-border bg-surface px-2 outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring" title="Nhờ trợ lý tóm tắt — chỉ anh/chị thấy">
        <Sparkles className="size-3.5 text-primary" aria-hidden /> Tóm tắt
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-60">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Trợ lý trả lời riêng anh/chị</DropdownMenuLabel>
          {th.t.assist.map((a) => (
            <DropdownMenuItem key={a.title} onClick={() => th.askPrivately(a.prompt)}>
              {a.title}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function MuteButton({ th }: { th: ThreadState }) {
  if (!th.t.thread_id || !th.t.can_post) return null;
  const label = th.t.muted ? "Bật lại thông báo" : "Tắt thông báo trao đổi này";
  return (
    <button type="button" onClick={() => void th.mute()} aria-pressed={th.t.muted} aria-label={label} title={label} className="grid size-7 place-items-center rounded-lg border border-border bg-surface outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
      {th.t.muted ? <BellOff className="size-3.5 text-muted-foreground" /> : <Bell className="size-3.5" />}
    </button>
  );
}

/** Dải tin đã ghim (đầu luồng): tin ghim mới nhất + menu xem hết; bấm ⇒ cuộn tới tin nếu đang có trên màn. */
function Pinned({ th }: { th: ThreadState }) {
  const p = th.t.pinned;
  if (!p.length) return null;
  const go = (id: number) => document.getElementById(`msg-${id}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  return (
    <div className="flex items-center gap-2 border-b border-hairline bg-surface px-3 py-1.5">
      <Pin className="size-3.5 shrink-0 text-primary" aria-hidden />
      <button type="button" className="t12 min-w-0 flex-1 truncate text-left hover:underline" onClick={() => go(p[0].id)} title={`${p[0].pinned_by} ghim`}>
        <span className="font-medium">{p[0].author}:</span> {p[0].body}
      </button>
      {p.length > 1 ? (
        <DropdownMenu>
          <DropdownMenuTrigger className="t12m shrink-0 text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring">{p.length} tin ghim</DropdownMenuTrigger>
          <DropdownMenuContent className="w-80">
            <DropdownMenuGroup>
              <DropdownMenuLabel>Tin đã ghim</DropdownMenuLabel>
              {p.map((x) => (
                <DropdownMenuItem key={x.id} onClick={() => go(x.id)}>
                  <span className="truncate">
                    <span className="font-medium">{x.author}:</span> {x.body}
                  </span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
    </div>
  );
}

const EMPTY = "Chưa có trao đổi nào. Hỏi đồng nghiệp ngay trên hồ sơ này — @nhắc tên để họ nhận thông báo.";

function OlderButton({ th }: { th: ThreadState }) {
  if (!th.hasMore) return null;
  return (
    <button type="button" className="t12m mx-auto mb-2 block rounded-full border border-border bg-surface px-3 py-1 hover:bg-accent" onClick={() => void th.loadOlder()}>
      Xem tin cũ hơn
    </button>
  );
}

function Boxed({ th }: { th: ThreadState }) {
  const { t, messages, firstUnread, lastId } = th;
  const [showFiles, setShowFiles] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const focus = t.focus_id ? document.getElementById(`msg-${t.focus_id}`) : null;
    if (focus) focus.scrollIntoView?.({ block: "nearest" });
    else bottom.current?.scrollIntoView?.({ block: "nearest" });
  }, [lastId, t.focus_id, showFiles]);
  return (
    <section className="flex flex-col gap-2.5 rounded-xl bg-surface2 p-3" aria-label={t.title}>
      <div className="flex items-center gap-1.5">
        <MessagesSquare className="size-4 text-primary" aria-hidden />
        <h3 className="t16">{t.title}</h3>
        {messages.length ? <span className="t12 text-muted-foreground">{messages.length}</span> : null}
        <span className="flex-1" />
        <AssistMenu th={th} />
        <FilesButton th={th} open={showFiles} onClick={() => setShowFiles((v) => !v)} />
        <BlockActions th={th} />
      </div>
      {showFiles && t.files ? (
        <ThreadFiles files={t.files} className="max-h-96" onClose={() => setShowFiles(false)} onJump={(id) => {
          setShowFiles(false);
          th.jumpTo(id);
        }} />
      ) : null}
      {showFiles ? null : <>
      {messages.length === 0 ? <p className="t12 text-muted-foreground">{EMPTY}</p> : null}
      <ul className="flex max-h-80 flex-col gap-2 overflow-y-auto pr-1">
        <li className="list-none">
          <OlderButton th={th} />
        </li>
        {messages.map((m) => (
          <li key={m.id} className={cn("group flex flex-col", m.mine ? "items-end" : "items-start")}>
            {m.id === firstUnread ? <Divider /> : null}
            {!m.mine ? <span className="t12m text-muted-foreground">{m.author}</span> : null}
            <div className={cn("flex max-w-[85%] flex-col", m.mine ? "items-end" : "items-start")}>
              <Bubble th={th} m={m} incoming="bg-surface" />
              <Stamp m={m} fill={false} />
            </div>
          </li>
        ))}
        <div ref={bottom} />
      </ul>
      </>}
      {t.notice ? <p className="t12 text-muted-foreground">{t.notice}</p> : null}
      {t.can_post ? <Composer th={th} /> : null}
      {th.error ? <ErrorLine>{th.error}</ErrorLine> : null}
    </section>
  );
}

/** Luồng chiếm cả cột: tin liền nhau của cùng một người gộp thành một lượt nói — tên + ảnh chỉ ở tin đầu (như iMessage / Lark). */
function Filled({ th }: { th: ThreadState }) {
  const { t, messages, firstUnread, lastId } = th;
  const scroller = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const focus = t.focus_id ? document.getElementById(`msg-${t.focus_id}`) : null;
    if (focus) focus.scrollIntoView({ block: "center" });
    else el.scrollTop = el.scrollHeight;
  }, [lastId, t.focus_id]);
  const direct = isDirect(t);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-end gap-1.5 border-b border-hairline px-3 py-1.5">
        <AssistMenu th={th} />
        <MuteButton th={th} />
        <BlockActions th={th} />
      </div>
      <Pinned th={th} />
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-4 py-3.5" role="log" aria-label={`Trao đổi: ${t.subject_title}`}>
        <OlderButton th={th} />
        {messages.length === 0 ? (
          <p className="t12 text-muted-foreground">{isRoom(t) ? "Chưa có tin nào trong phòng — gửi lời chào đầu tiên." : direct ? "Chưa có tin nào — chỉ hai người đọc được cuộc này." : EMPTY}</p>
        ) : null}
        <ul className="flex flex-col gap-1">
          {messages.map((m, i) => {
            const prev = messages[i - 1];
            const first = i === 0 || prev.author !== m.author || m.id === firstUnread || !!m.reply;
            return (
              <li key={m.id} className={cn("group flex flex-col", first && i > 0 && "pt-2")}>
                {m.id === firstUnread ? <Divider /> : null}
                <div className={cn("flex items-start gap-2", m.mine ? "justify-end pl-10" : "pr-10")}>
                  {!m.mine && !direct ? <span className="w-7 shrink-0 pt-[18px]">{first ? <Initials name={m.author} size={28} /> : null}</span> : null}
                  <div className={cn("flex min-w-0 max-w-[640px] flex-col", m.mine ? "items-end" : "items-start")}>
                    {!m.mine && first && !direct ? <span className="t12m pb-0.5 text-muted-foreground">{m.author}</span> : null}
                    <Bubble th={th} m={m} incoming="bg-incoming" />
                    <Stamp m={m} fill />
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
        {t.has_newer ? (
          <button type="button" className="t12m mx-auto mt-2 flex items-center gap-1 rounded-full border border-border bg-surface px-3 py-1 hover:bg-accent" onClick={th.jumpLatest}>
            <ArrowDown className="size-3.5" aria-hidden /> Về tin mới nhất
          </button>
        ) : null}
      </div>
      <div className="flex flex-col gap-1.5 border-t border-hairline bg-bar px-3 py-2.5">
        {th.error ? <ErrorLine>{th.error}</ErrorLine> : null}
        {t.notice ? <p className="t12 text-muted-foreground">{t.notice}</p> : null}
        {t.can_post ? <Composer th={th} pill /> : !t.notice ? <p className="t12 text-muted-foreground">Bạn chỉ xem được trao đổi này.</p> : null}
      </div>
    </div>
  );
}

const circle = "grid size-8 shrink-0 place-items-center rounded-full bg-foreground/[0.06] text-muted-foreground outline-none hover:bg-foreground/10 focus-visible:ring-2 focus-visible:ring-ring";
const square = "inline-flex size-8 shrink-0 items-center justify-center rounded-lg border border-border bg-surface hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring";

/** Ô nhập cùng kiểu ô hỏi trợ lý: ô bo tròn, nút tròn — một ngôn ngữ cho mọi chỗ gõ tin. */
function Composer({ th, pill = false }: { th: ThreadState; pill?: boolean }) {
  const { t, draft, setDraft, mentions, setMentions, sending, replyTo, editing } = th;
  const room = isRoom(t);
  const direct = isDirect(t);
  const empty = !draft.trim();
  const people = t.people.filter((p) => !mentions.some((m) => m.value === p.value));
  const fileInput = useRef<HTMLInputElement>(null);
  const context = editing ? { label: "Đang sửa tin", text: editing.body } : replyTo ? { label: `Trả lời ${replyTo.author}`, text: replyTo.body } : null;
  return (
    <div className="flex flex-col gap-1.5">
      {context ? (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-surface px-2 py-1">
          {editing ? <Pencil className="size-3.5 shrink-0 text-primary" aria-hidden /> : <CornerUpLeft className="size-3.5 shrink-0 text-primary" aria-hidden />}
          <span className="t12 min-w-0 flex-1 truncate">
            <span className="font-medium">{context.label}:</span> {context.text}
          </span>
          <button type="button" className="grid size-6 place-items-center rounded-md hover:bg-foreground/10" onClick={th.cancel} aria-label="Bỏ" title="Bỏ">
            <X className="size-3.5" />
          </button>
        </div>
      ) : null}
      {mentions.length ? (
        <div className="flex flex-wrap gap-1.5">
          {mentions.map((p) => (
            <button key={p.value} type="button" className="t12m inline-flex items-center gap-1 rounded-lg border border-border bg-surface px-2 py-1 hover:bg-accent" onClick={() => setMentions((ms) => ms.filter((x) => x.value !== p.value))} aria-label={`Bỏ nhắc ${p.label}`}>
              @{p.label}
              <X className="size-3" aria-hidden />
            </button>
          ))}
        </div>
      ) : null}
      <div className="flex items-end gap-2">
        {!direct && !editing ? (
          <DropdownMenu>
            <DropdownMenuTrigger
              className={pill ? circle : square}
              aria-label={room ? "Nhắc tên — người trong phòng" : "Nhắc tên — chỉ nhắc được người xem được hồ sơ này"}
              title={room ? "Nhắc tên — người trong phòng" : "Nhắc tên — chỉ nhắc được người xem được hồ sơ này"}
            >
              <AtSign className="size-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent className="max-h-72 w-60">
              <DropdownMenuGroup>
                <DropdownMenuLabel>{room ? "Người trong phòng" : "Người xem được hồ sơ"}</DropdownMenuLabel>
                {people.length === 0 ? <DropdownMenuItem disabled>Không còn ai để nhắc</DropdownMenuItem> : null}
                {people.map((p) => (
                  <DropdownMenuItem key={p.value} onClick={() => setMentions((ms) => [...ms, p])}>
                    {p.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
        {t.can_attach && !editing ? (
          <>
            <button
              type="button"
              className={pill ? circle : square}
              onClick={() => fileInput.current?.click()}
              disabled={sending}
              aria-label="Gửi tệp"
              title={`Gửi tệp — ${t.file_kinds}${t.file_max ? `, tối đa ${Math.round(t.file_max / (1024 * 1024))} MB` : ""}. Chữ đang gõ thành chú thích.`}
            >
              <Paperclip className="size-4" />
            </button>
            <input
              ref={fileInput}
              type="file"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) void th.sendFile(f);
              }}
            />
          </>
        ) : null}
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void th.send();
            } else if (e.key === "Escape" && (editing || replyTo)) th.cancel();
          }}
          onPaste={(e) => {
            const f = Array.from(e.clipboardData.files)[0];
            if (f && t.can_attach && !editing) {
              e.preventDefault();
              void th.sendFile(f);
            }
          }}
          rows={1}
          placeholder={editing ? "Sửa tin…" : room ? "Nhắn vào phòng…" : direct ? `Nhắn cho ${t.subject_title}…` : "Nhắn trên hồ sơ này…"}
          aria-label="Nội dung tin nhắn"
          className={cn(
            "t14 max-h-32 min-h-8 flex-1 resize-none border border-input bg-field outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30",
            pill ? "rounded-[18px] px-3 py-[6px]" : "rounded-lg px-2.5 py-1.5",
          )}
        />
        <button
          type="button"
          onClick={() => void th.send()}
          disabled={sending || empty}
          className={cn("grid size-8 shrink-0 place-items-center text-white transition-colors", pill ? "rounded-full" : "rounded-lg", empty ? "bg-muted-foreground/30" : "bg-primary hover:bg-primary/90")}
          aria-label={editing ? "Lưu tin đã sửa (Enter)" : "Gửi tin (Enter)"}
          title={editing ? "Lưu (Enter)" : "Gửi (Enter)"}
        >
          <ArrowUp className="size-4" strokeWidth={2.5} />
        </button>
        <button
          type="button"
          className={pill ? circle : square}
          aria-label="Hỏi trợ lý riêng về trao đổi này"
          title="Hỏi trợ lý riêng — muốn mọi người cùng thấy thì bấm chia sẻ dưới câu trả lời"
          onClick={() => th.askPrivately()}
        >
          <Sparkles className="size-4" />
        </button>
      </div>
    </div>
  );
}
