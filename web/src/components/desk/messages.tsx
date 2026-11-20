"use client";

import { ChevronLeft, Files, MessageSquarePlus, PanelRight, Send, TriangleAlert, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { badgeText, isDirect, isRoom, preview, roomColor, roomInitials, splitInbox, stamp, talkItemFor, unreadSummary, type TalkItem } from "@/lib/talk";
import type { OpenRef, ThreadBlockData } from "@/lib/types";
import { cn } from "@/lib/utils";
import { IconTile, moduleIcon, moduleTint } from "@/components/icons";
import { Initials, Spinner } from "@/components/primitives";
import { ThreadBlock } from "@/components/blocks/thread";
import { ThreadFiles } from "@/components/blocks/thread-files";
import { CardFrame } from "./canvas";
import { ColumnHeader, headerBtn, Orb, PANE_HEADER } from "./column";
import { useChat } from "./chat-context";
import { useDesk } from "./desk-context";
import { NoticeList } from "./notices";

// Cột "Tin nhắn" kiểu Lark (Rail.swift MessagesColumn): trợ lý + thông báo hệ thống + phiếu chờ duyệt + phòng + trao đổi trên hồ sơ trong
// MỘT danh sách. Mở một luồng ⇒ luồng chiếm cột, hồ sơ / phòng của luồng bung ra khung làm việc bên phải.

export function MessagesColumn() {
  const desk = useDesk();
  const { refreshTalk } = desk;
  // tin mới tới qua kênh hộp tin (desk-context); đọc lại thưa định kỳ chỉ để đỡ khi kênh rớt sau proxy
  useEffect(() => {
    void refreshTalk();
    const t = setInterval(() => void refreshTalk(), 120_000);
    return () => clearInterval(t);
  }, [refreshTalk]);
  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      <InboxList />
    </div>
  );
}

/** Bên phải khi đang chọn một mục ở "Tin nhắn": hộp thông báo, hoặc cuộc trò chuyện cỡ lớn (+ ngăn kiểm tra). */
export function MessagesDetail() {
  const desk = useDesk();
  const pane = desk.messagesPane;
  if (pane.kind === "notices") {
    return (
      <div className="flex h-full min-h-0 flex-col bg-surface">
        <DetailHeader title="Thông báo" subtitle="Hệ thống Barista · việc mới, lead mới, phiếu duyệt" icon={<IconTile icon="bell.fill" tint="#ff9500" size={32} />}>
          {desk.unread > 0 ? (
            <button type="button" className="t12m shrink-0 text-primary hover:underline" onClick={desk.markAllNoticesRead}>
              Đọc hết
            </button>
          ) : null}
        </DetailHeader>
        <NoticeList />
      </div>
    );
  }
  if (pane.kind === "thread") return <ThreadDetail key={pane.item.subject_kind + pane.item.subject_id} item={pane.item} />;
  return null;
}

/** Đầu khung bên phải: hẹp ⇒ có nút quay về danh sách. */
function DetailHeader({ title, subtitle, icon, children }: { title: string; subtitle: string; icon?: React.ReactNode; children?: React.ReactNode }) {
  const desk = useDesk();
  return (
    <header className={cn(PANE_HEADER, "px-3 min-[980px]:px-5")}>
      <button
        type="button"
        onClick={desk.closeTalk}
        className="grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-surface outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring min-[980px]:hidden"
        aria-label="Về danh sách tin nhắn"
        title="Về danh sách tin nhắn"
      >
        <ChevronLeft className="size-4" />
      </button>
      {icon}
      <div className="min-w-0 flex-1">
        <h2 className="t16 truncate">{title}</h2>
        <p className="t12 truncate text-muted-foreground">{subtitle}</p>
      </div>
      {children}
    </header>
  );
}

function InboxList() {
  const desk = useDesk();
  const chat = useChat();
  const firstName = desk.manifest?.user.name.trim().split(/\s+/).pop() ?? "bạn";
  const talk = desk.talk;
  const { directs, rooms, records } = splitInbox(talk?.items ?? []);
  const lastReply = [...chat.messages].reverse().find((m) => m.role === "assistant" && m.text)?.text.replace(/\s+/g, " ");
  const assistantPreview = chat.live ? "đang trả lời…" : (lastReply ?? chat.threads[0]?.title ?? "Hỏi về khách, giao dịch, số liệu — em soạn sẵn để anh/chị bấm");
  const inbox = desk.manifest?.inbox;
  const pane = desk.messagesPane;
  const selectedThread = pane.kind === "thread" ? pane.item.subject_kind + pane.item.subject_id : null;
  // tạo phòng / mở tin riêng xong ⇒ mở luôn cuộc đó (server trả loại + mã đối tượng)
  const openFrom = (out: unknown) => {
    void desk.refreshTalk();
    const o = (typeof out === "object" && out !== null ? out : {}) as Record<string, unknown>;
    if (typeof o.subject_kind === "string" && typeof o.subject_id === "string") desk.openTalk(talkItemFor(o.subject_kind, o.subject_id));
  };

  return (
    <>
      <ColumnHeader title="Tin nhắn" subtitle={unreadSummary(desk.unread + (talk?.unread ?? 0))}>
        {talk?.can_start_direct ? (
          <button
            type="button"
            className={headerBtn}
            aria-label="Nhắn riêng cho một người"
            title="Nhắn riêng — chỉ hai người đọc được"
            onClick={() =>
              desk.openForm(
                { command: "talk.start_direct", title: "Nhắn riêng", style: "primary", bind: {}, preset: {}, when: {}, importable: false },
                { onDone: (_m, out) => openFrom(out) },
              )
            }
          >
            <Send className="size-3.5" />
          </button>
        ) : null}
        {talk ? (
          <button
            type="button"
            className={headerBtn}
            aria-label="Tạo phòng nhắn tin nhóm"
            title="Tạo phòng nhắn tin nhóm"
            onClick={() =>
              desk.openForm(
                { command: "talk.create_room", title: "Tạo phòng nhắn tin", style: "primary", bind: {}, preset: {}, when: {}, importable: false },
                { onDone: (_m, out) => openFrom(out) },
              )
            }
          >
            <MessageSquarePlus className="size-3.5" />
          </button>
        ) : null}
      </ColumnHeader>
      <ul className="min-h-0 flex-1 overflow-y-auto px-1.5 py-2" aria-label="Danh sách tin nhắn">
        <InboxRow
          title={`Trợ lý của ${firstName}`}
          tag={{ text: "Trợ lý", color: "var(--cold)" }}
          preview={assistantPreview}
          time={chat.threads[0] ? stamp(chat.threads[0].updated_at) : ""}
          icon={<Orb size={40} busy={!!chat.live} />}
          onClick={() => desk.select({ kind: "assistant" })}
        />
        <InboxRow
          title="Thông báo"
          tag={{ text: "Hệ thống", color: "var(--warn)" }}
          preview={desk.notices[0]?.title ?? "Chưa có thông báo"}
          time={desk.notices[0] ? stamp(desk.notices[0].created_at) : ""}
          unread={desk.unread}
          selected={pane.kind === "notices"}
          icon={<IconTile icon="bell.fill" tint="#ff9500" size={40} />}
          onClick={() => desk.setMessagesPane({ kind: "notices" })}
        />
        {inbox && desk.inboxCount > 0 ? (
          <InboxRow
            title="Phiếu chờ duyệt"
            tag={{ text: "Hệ thống", color: "var(--warn)" }}
            preview={`${desk.inboxCount} phiếu chờ anh/chị duyệt`}
            time=""
            unread={desk.inboxCount}
            icon={<IconTile icon="checkmark.seal.fill" tint="#34c759" size={40} />}
            onClick={() => {
              desk.setMessagesPane({ kind: "list" });
              desk.openScreen(inbox);
            }}
          />
        ) : null}
        {talk ? (
          <>
            {directs.length ? <SectionTitle>Tin riêng</SectionTitle> : null}
            {directs.map((it) => (
              <InboxRow
                key={it.thread_id}
                title={it.title}
                preview={it.inactive ? "Đã rời công ty — chỉ đọc lại được" : preview(it)}
                time={stamp(it.at)}
                unread={it.unread}
                muted={it.muted}
                selected={selectedThread === it.subject_kind + it.subject_id}
                icon={<Initials name={it.title} size={40} />}
                onClick={() => desk.openTalk(it)}
              />
            ))}
            <SectionTitle>Phòng</SectionTitle>
            {rooms.length === 0 ? <Hint>Chưa ở phòng nào. Bấm nút tạo phòng ở đầu cột để lập phòng đội / dự án.</Hint> : null}
            {rooms.map((it) => (
              <InboxRow key={it.thread_id} title={it.title} preview={preview(it)} time={stamp(it.at)} unread={it.unread} muted={it.muted} mentioned={it.mentioned > 0} selected={selectedThread === it.subject_kind + it.subject_id} icon={<RoomAvatar name={it.title} size={40} />} onClick={() => desk.openTalk(it)} />
            ))}
            <SectionTitle>Trao đổi trên hồ sơ</SectionTitle>
            {records.length === 0 ? <Hint>Chưa có trao đổi nào. Mở một khách / hồ sơ và nhắn trong khối “Trao đổi” — @nhắc tên để đồng nghiệp nhận được.</Hint> : null}
            {records.map((it) => (
              <InboxRow
                key={it.thread_id}
                title={it.title}
                tag={it.kind_label ? { text: it.kind_label, color: "var(--muted-foreground)" } : undefined}
                preview={preview(it)}
                time={stamp(it.at)}
                unread={it.unread}
                muted={it.muted}
                mentioned={it.mentioned > 0}
                selected={selectedThread === it.subject_kind + it.subject_id}
                icon={<IconTile icon={moduleIcon(it.subject_kind)} tint={moduleTint(it.subject_kind)} size={40} />}
                onClick={() => desk.openTalk(it)}
              />
            ))}
          </>
        ) : null}
      </ul>
    </>
  );
}

const SectionTitle = ({ children }: { children: React.ReactNode }) => <li className="t12m px-3 pt-3.5 pb-1 text-muted-foreground" role="presentation">{children}</li>;
const Hint = ({ children }: { children: React.ReactNode }) => <li className="t12 px-3 pb-1 text-muted-foreground">{children}</li>;

/** Một dòng như danh sách chat của Lark: ảnh | tên + nhãn nhỏ + giờ | dòng tin cuối + số chưa đọc / @. */
function InboxRow({
  title,
  tag,
  preview: text,
  time,
  unread = 0,
  muted = false,
  mentioned = false,
  selected = false,
  icon,
  onClick,
}: {
  title: string;
  tag?: { text: string; color: string };
  preview: string;
  time: string;
  unread?: number;
  /** Tắt tiếng: số chưa đọc vẽ xám, không tính lên menu (server đếm). */
  muted?: boolean;
  mentioned?: boolean;
  selected?: boolean;
  icon: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        aria-current={selected ? "true" : undefined}
        className={cn("flex w-full items-center gap-3 rounded-[10px] px-2.5 py-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring", selected ? "bg-primary/12" : "hover:bg-foreground/5")}
      >
        <span className="grid size-10 shrink-0 place-items-center">{icon}</span>
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex items-center gap-1.5">
            <span className={cn("truncate text-[14px] leading-snug", unread > 0 ? "font-medium" : "font-normal")}>{title}</span>
            {tag ? (
              <span className="t12 shrink-0 rounded px-1.5" style={{ color: tag.color, background: `color-mix(in oklab, ${tag.color} 13%, transparent)` }}>
                {tag.text}
              </span>
            ) : null}
            <span className="t12 tnum ml-auto shrink-0 pl-1 text-muted-foreground/80">{time}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="t12 truncate text-muted-foreground">{text || " "}</span>
            <span className="ml-auto flex shrink-0 items-center gap-1">
              {mentioned ? <span className="t12m grid size-[18px] place-items-center rounded-full bg-bad text-white" aria-label="Có người nhắc bạn">@</span> : null}
              {unread > 0 ? (
                <span className={cn("tnum grid h-[18px] min-w-[18px] place-items-center rounded-full px-1.5 text-[11px] font-semibold text-white", mentioned ? "bg-bad" : muted ? "bg-muted-foreground/60" : "bg-primary")} aria-label={`${unread} chưa đọc`}>
                  {badgeText(unread)}
                </span>
              ) : null}
            </span>
          </span>
        </span>
      </button>
    </li>
  );
}

/** Ảnh nhóm: chữ cái đầu của tên phòng trên nền màu cố định theo tên (như nhóm chat của Lark). */
export function RoomAvatar({ name, size = 40 }: { name: string; size?: number }) {
  const c = roomColor(name);
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full font-semibold text-white"
      style={{ width: size, height: size, fontSize: size * 0.36, background: `linear-gradient(135deg, ${c}, color-mix(in oklab, ${c} 70%, transparent))` }}
      aria-hidden
    >
      {roomInitials(name)}
    </span>
  );
}

/**
 * Cuộc trò chuyện cỡ lớn bên phải (kiểu app chat): chat chiếm hết chiều cao, ô nhập ghim đáy. Mở luồng ⇒ hồ sơ / phòng của luồng thành thẻ
 * đang hoạt động của khung làm việc, nhưng NGĂN KIỂM TRA (~440px, một thẻ) đóng sẵn để khung chat rộng — bấm "Hồ sơ" / "Thành viên" hoặc
 * bấm một thẻ đã chia sẻ trong tin để mở.
 */
function ThreadDetail({ item }: { item: TalkItem }) {
  const desk = useDesk();
  const { openTarget, setInspectorOpen } = desk;
  const [state, setState] = useState<{ block: ThreadBlockData; subject: OpenRef | null; title: string; kind: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // ngăn "Tệp" (0128): cùng chỗ với ngăn Thành viên / Hồ sơ — mở cái này thì đóng cái kia; bấm một dòng ⇒ luồng nhảy tới đúng tin
  const [filesOpen, setFilesOpen] = useState(false);
  const [jump, setJump] = useState<{ id: number } | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const page = await api.query("talk.thread", { subject_kind: item.subject_kind, subject_id: item.subject_id });
        if (!alive || page.kind !== "card") return;
        const block = page.blocks.find((b) => b.type === "thread");
        if (!block || block.type !== "thread") throw new Error("Trao đổi không có nội dung");
        const subject = page.open[0] ?? null;
        setState({ block, subject, title: page.title, kind: page.subtitle });
        if (subject) void openTarget(subject.query, subject.input, subject.title ?? page.title, false);
      } catch (e) {
        // vd đã đổi người phụ trách ⇒ không còn thấy hồ sơ
        if (alive) setError(e instanceof Error ? e.message : "Không mở được trao đổi");
      }
    })();
    return () => {
      alive = false;
    };
  }, [item.subject_kind, item.subject_id, openTarget]);

  const room = isRoom(item);
  const direct = isDirect(item);
  const title = item.title || state?.title || "Trao đổi";
  const kindLabel = item.kind_label || state?.kind || "Hồ sơ";
  const active = desk.canvas.find((c) => c.id === desk.activeId);
  const toggleLabel = room ? "Thành viên" : "Hồ sơ";

  const toggleInspector = () => {
    if (desk.inspectorOpen) return setInspectorOpen(false);
    setFilesOpen(false);
    if (state?.subject) void openTarget(state.subject.query, state.subject.input, state.subject.title ?? title, false);
    setInspectorOpen(true);
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      <DetailHeader
        title={title}
        subtitle={room ? "Phòng · chỉ thành viên phòng đọc được" : direct ? "Tin riêng · chỉ hai người đọc được" : `${kindLabel} · chỉ người xem được hồ sơ mới đọc được`}
        icon={room ? <RoomAvatar name={title} size={32} /> : direct ? <Initials name={title} size={32} /> : <IconTile icon={moduleIcon(item.subject_kind)} tint={moduleTint(item.subject_kind)} size={32} />}
      >
        {state?.subject ? (
          <button
            type="button"
            onClick={toggleInspector}
            aria-pressed={desk.inspectorOpen}
            title={desk.inspectorOpen ? `Ẩn ${toggleLabel.toLowerCase()}` : room ? "Thành viên, thẻ đã chia sẻ" : "Xem hồ sơ bên cạnh"}
            className={cn(
              "t14m inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border px-2.5 outline-none focus-visible:ring-2 focus-visible:ring-ring",
              desk.inspectorOpen ? "border-primary/40 bg-primary/12 text-primary" : "border-border bg-surface hover:bg-accent",
            )}
          >
            {room ? <Users className="size-3.5" aria-hidden /> : <PanelRight className="size-3.5" aria-hidden />}
            {toggleLabel}
          </button>
        ) : null}
        {state?.block.files ? (
          <button
            type="button"
            onClick={() => {
              if (!filesOpen) setInspectorOpen(false);
              setFilesOpen(!filesOpen);
            }}
            aria-pressed={filesOpen}
            title={filesOpen ? "Ẩn tệp" : "Ảnh, tài liệu, link đã gửi"}
            className={cn(
              "t14m inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border px-2.5 outline-none focus-visible:ring-2 focus-visible:ring-ring",
              filesOpen ? "border-primary/40 bg-primary/12 text-primary" : "border-border bg-surface hover:bg-accent",
            )}
          >
            <Files className="size-3.5" aria-hidden />
            {state.block.files.title || "Tệp"}
          </button>
        ) : null}
      </DetailHeader>
      <div className="flex min-h-0 flex-1">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {state ? (
            <ThreadBlock
              block={state.block}
              fill
              jump={jump}
              onOpenCard={(q, input, t) => {
                void openTarget(q, input, t, false);
                setFilesOpen(false);
                setInspectorOpen(true);
              }}
            />
          ) : error ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
              <TriangleAlert className="size-7 text-muted-foreground" aria-hidden />
              <p className="t14m">Không mở được trao đổi</p>
              <p className="t12 text-muted-foreground" role="alert">{error}</p>
            </div>
          ) : (
            <div className="grid flex-1 place-items-center">
              <Spinner className="size-6" />
            </div>
          )}
        </div>
        {filesOpen && state?.block.files ? (
          <aside className="flex min-h-0 w-full shrink-0 flex-col border-l border-hairline bg-canvas p-3 max-[979px]:absolute max-[979px]:inset-0 max-[979px]:z-20 min-[980px]:w-[440px]" aria-label="Tệp của trao đổi">
            <ThreadFiles
              files={state.block.files}
              className="flex-1"
              onClose={() => setFilesOpen(false)}
              onJump={(id) => {
                setJump({ id });
                // màn hẹp: ngăn phủ cả cột ⇒ đóng để thấy tin; màn rộng giữ ngăn mở để bấm tiếp
                if (window.matchMedia("(max-width: 979px)").matches) setFilesOpen(false);
              }}
            />
          </aside>
        ) : !filesOpen && desk.inspectorOpen && active ? (
          <aside className="flex min-h-0 w-full shrink-0 flex-col border-l border-hairline bg-canvas p-3 max-[979px]:absolute max-[979px]:inset-0 max-[979px]:z-20 min-[980px]:w-[440px]" aria-label="Ngăn kiểm tra">
            <CardFrame key={active.id} item={active} onClose={() => setInspectorOpen(false)} closeLabel="Ẩn ngăn bên cạnh" />
          </aside>
        ) : null}
      </div>
    </div>
  );
}
