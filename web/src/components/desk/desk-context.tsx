"use client";

import { settledOf, type Settled } from "@/lib/brief";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, auth, listenSignals, sessionGone } from "@/lib/api";
import { undoOfOutput } from "@/lib/autonomy";
import { droppedScreenKeys, fixedInput, menuChanged } from "@/lib/form";
import { newKey } from "@/lib/idempotency";
import { leaveSession, takePrimedDesk } from "@/lib/session";
import type { OpenRecord } from "@/lib/documents";
import { decodeInbox, noticeIdsForSubject, talkAvailable, talkItemFor, type Section, type TalkInbox, type TalkItem } from "@/lib/talk";
import type { ActionSpec, ChatCard, DeskPage, Input, Json, Manifest, Notice, Row, ScreenSpec, SessionInfo, UndoSpec } from "@/lib/types";
import { useCallHub } from "@/components/call/call-provider";

// Trạng thái dùng chung của một người × một công ty (Desk.swift): manifest, khung làm việc (thẻ + màn), hộp thông báo, hộp thoại.
// Phần chat ở chat-context.tsx (cập nhật từng mảnh chữ — tách riêng để khung làm việc không vẽ lại theo).

export type CanvasItem = ({ id: string; kind: "card"; card: ChatCard } | { id: string; kind: "screen"; spec: ScreenSpec }) & { rev?: number };
export type FormRequest = { id: number; action: ActionSpec; row: Row | null; initial?: Input; onDone?: (message: string, output?: Json) => void };
export type PageRequest = { id: number; title: string; query: string; input: Input };
export type ImportRequest = { id: number; action: ActionSpec; onDone?: () => void };
export type ToastState = { id: number; text: string; isError: boolean; undo?: UndoSpec };

/** Mục đang chọn ở cột "Tin nhắn" (kiểu app chat: cột luôn là danh sách, bên phải hiện thứ đang chọn): không chọn ⇒ bên phải là
 * khung làm việc; "notices" ⇒ hộp thông báo; "thread" ⇒ cuộc trò chuyện cỡ lớn (+ ngăn kiểm tra tuỳ chọn). */
export type MessagesPane = { kind: "list" } | { kind: "notices" } | { kind: "thread"; item: TalkItem };

const RAIL_KEY = "barista-rail-hidden";
function readRailHidden(): boolean {
  try {
    return localStorage.getItem(RAIL_KEY) === "1";
  } catch {
    return false;
  }
}

export const cardItemId = (c: ChatCard) => `card:${c.id}`;
export const screenItemId = (s: ScreenSpec) => `screen:${s.query}:${s.key}`;

type DeskValue = {
  manifest: Manifest | null;
  loadError: string | null;
  session: SessionInfo | null;
  reloadManifest: () => Promise<void>;
  /** Màn theo khoá (vd app tự ghép "app-<mã>") — đọc manifest mới nhất. */
  screenByKey: (key: string) => ScreenSpec | undefined;
  /** Mở màn lên khung làm việc và TẢI LẠI nếu đang mở (trình tạo app vừa sửa xong — 0098). */
  openScreenFresh: (spec: ScreenSpec) => void;
  /** Tải lại TẠI CHỖ thẻ đang mở (nút ⟳ trên tiêu đề thẻ). */
  refreshItem: (id: string) => void;
  /** Phóng to khung làm việc: ẩn thanh bên + cột trái, khung chiếm trọn chiều ngang (Esc thu về). Chỉ phiên, không lưu. */
  canvasMax: boolean;
  toggleCanvasMax: () => void;
  // khung làm việc
  canvas: CanvasItem[];
  activeId: string | null;
  setActiveId: (id: string) => void;
  openItem: (item: CanvasItem, show?: boolean) => void;
  openScreen: (spec: ScreenSpec) => void;
  openCard: (card: ChatCard, show?: boolean) => void;
  closeItem: (id: string) => void;
  clearCanvas: () => void;
  screenFor: (query: string) => ScreenSpec | undefined;
  /** `reveal` (mặc định true): cửa sổ hẹp thì chuyển sang khung làm việc; false ⇒ chỉ mở thẻ, giữ ngăn đang xem (vd mở luồng ở cột). */
  openTarget: (query: string, input: Input, title?: string, reveal?: boolean) => Promise<void>;
  quickScreens: ScreenSpec[];
  // thanh bên trái + cột theo mục (Rail.swift)
  section: Section;
  select: (s: Section) => void;
  railHidden: boolean;
  toggleRail: () => void;
  messagesPane: MessagesPane;
  setMessagesPane: (p: MessagesPane) => void;
  talk: TalkInbox | null;
  refreshTalk: () => Promise<void>;
  openTalk: (item: TalkItem) => void;
  closeTalk: () => void;
  /** subject_id của luồng đang mở ở cột "Tin nhắn" — khối "Trao đổi" cùng luồng trên thẻ bên phải thu lại. */
  talkOpenSubject: string | null;
  /** Ngăn kiểm tra cạnh cuộc trò chuyện (thẻ đang hoạt động của khung làm việc) — mặc định đóng để khung chat rộng. */
  inspectorOpen: boolean;
  setInspectorOpen: (v: boolean) => void;
  // một ngăn (cửa sổ hẹp)
  narrowShowsCanvas: boolean;
  setNarrowShowsCanvas: (v: boolean) => void;
  // hộp thoại
  forms: FormRequest[];
  openForm: (action: ActionSpec, opts?: { row?: Row | null; initial?: Input; onDone?: (message: string, output?: Json) => void }) => void;
  closeForm: (id: number) => void;
  pages: PageRequest[];
  openPage: (query: string, input: Input, title?: string) => void;
  closePage: (id: number) => void;
  imports: ImportRequest[];
  openImport: (action: ActionSpec, onDone?: () => void) => void;
  closeImport: (id: number) => void;
  // thông báo
  toast: ToastState | null;
  show: (text: string, isError?: boolean, undo?: UndoSpec) => void;
  inboxCount: number;
  /** Tăng mỗi khi một lệnh chạy xong ở bất kỳ đâu (``refreshInbox``) — bàn việc tải lại theo số này. */
  changes: number;
  /** Bàn việc (0094): một lần tải dùng chung cho cột "Việc" và số trên nút ở thanh trái. */
  deskPage: DeskPage | null;
  deskFailed: boolean;
  refreshDesk: () => Promise<void>;
  refreshInbox: () => Promise<void>;
  notices: Notice[];
  unread: number;
  refreshNotices: () => Promise<void>;
  openNotice: (n: Notice) => void;
  markAllNoticesRead: () => void;
  // chat ↔ khung
  askPrompt: { text: string; n: number } | null;
  ask: (prompt: string) => void;
  shareTarget: { subjectKind: string; subjectId: string; title: string } | null;
  setShareTarget: (t: DeskValue["shareTarget"]) => void;
  /** Hồ sơ nhận tệp đang mở trên khung (ADR 0249) — khối "Tệp" của thẻ báo lên khi hiện, gỡ khi đóng; tài liệu thả vào chat mặc định gắn vào đây. */
  openRecord: OpenRecord | null;
  setOpenRecord: (r: OpenRecord | null, owner?: string) => void;
  /** Tăng mỗi lần một tệp vừa gắn vào hồ sơ từ ngoài thẻ (thả vào chat) — thẻ đang mở tải lại. */
  recordFilesRev: number;
  bumpRecordFiles: () => void;
  confirmed: Record<string, string>;
  setConfirmed: (id: string, message: string, approvalId?: string) => void;
  /** Thẻ chờ duyệt đã quyết chưa — SERVER nói (`state`), chữ vừa bấm chỉ lấp khoảng trước khi tải lại (ADR 0253). */
  settled: (card: ChatCard) => Settled | null;
  proposalEdits: Record<string, Input>;
  setProposalEdit: (id: string, input: Input) => void;
  paletteOpen: boolean;
  setPaletteOpen: (v: boolean) => void;
  logout: () => Promise<void>;
  switchTenant: (id: string) => Promise<void>;
};

const Ctx = createContext<DeskValue | null>(null);
export function useDesk(): DeskValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useDesk ngoài DeskProvider");
  return v;
}

/** Màn ghim theo vai (server quyết); thiếu ⇒ hai màn đầu của mỗi phân hệ, tối đa 5 (Desk.quickScreens). */
export function pinnedScreens(manifest: Manifest | null): ScreenSpec[] {
  const spaces = manifest?.workspaces ?? [];
  if (manifest?.pins?.length) {
    return manifest.pins.flatMap((p) => {
      const s = spaces.find((w) => w.key === p.workspace)?.screens.find((x) => x.key === p.key);
      return s ? [s] : [];
    });
  }
  return spaces.flatMap((w) => w.screens.filter((s) => s.group !== "Nhập liệu").slice(0, 2)).slice(0, 5);
}

let seq = 1;
const MAX_OPEN = 12;

export function DeskProvider({ children }: { children: React.ReactNode }) {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [canvas, setCanvas] = useState<CanvasItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [narrowShowsCanvas, setNarrowShowsCanvas] = useState(false);
  const [forms, setForms] = useState<FormRequest[]>([]);
  const [pages, setPages] = useState<PageRequest[]>([]);
  const [imports, setImports] = useState<ImportRequest[]>([]);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [inboxCount, setInboxCount] = useState(0);
  const [changes, setChanges] = useState(0);
  const [deskPage, setDeskPage] = useState<DeskPage | null>(null);
  const [deskFailed, setDeskFailed] = useState(false);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [unread, setUnread] = useState(0);
  const [askPrompt, setAskPrompt] = useState<{ text: string; n: number } | null>(null);
  const [shareTarget, setShareTarget] = useState<DeskValue["shareTarget"]>(null);
  const [openRecord, setOpenRecordState] = useState<OpenRecord | null>(null);
  const [recordFilesRev, setRecordFilesRev] = useState(0);
  // khối gỡ chỉ được gỡ CHÍNH hồ sơ nó báo (thẻ khác vừa mở đã thay thì giữ)
  const setOpenRecord = useCallback((r: OpenRecord | null, owner?: string) =>
    setOpenRecordState((cur) => (r ? r : owner && cur && cur.subjectId !== owner ? cur : null)), []);
  const bumpRecordFiles = useCallback(() => setRecordFilesRev((n) => n + 1), []);
  const [confirmed, setConfirmedMap] = useState<Record<string, string>>({});
  const [confirmedApproval, setConfirmedApproval] = useState<Record<string, string>>({});
  const [proposalEdits, setProposalEdits] = useState<Record<string, Input>>({});
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [section, setSection] = useState<Section>({ kind: "assistant" });
  const [messagesPane, setMessagesPane] = useState<MessagesPane>({ kind: "list" });
  // khung chỉ vẽ sau khi tải manifest ở trình duyệt ⇒ đọc localStorage ngay khi khởi tạo không lệch với bản dựng ở server
  const [railHidden, setRailHidden] = useState(() => typeof window !== "undefined" && readRailHidden());
  const [talk, setTalk] = useState<TalkInbox | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const noticesRef = useRef<Notice[]>([]);
  const talkRef = useRef<TalkInbox | null>(null);
  const manifestRef = useRef<Manifest | null>(null);
  const lastNotice = useRef(0);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback((text: string, isError = false, undo?: UndoSpec) => {
    const id = seq++;
    setToast({ id, text, isError, ...(undo ? { undo } : {}) });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast((t) => (t?.id === id ? null : t)), undo ? 8000 : 3500);   // có nút Hoàn tác ⇒ ở lâu hơn một chút
  }, []);

  const openItem = useCallback((item: CanvasItem, showCanvas = false) => {
    if (showCanvas) setNarrowShowsCanvas(true);
    setCanvas((cur) => (cur.some((c) => c.id === item.id) ? cur : [item, ...cur].slice(0, MAX_OPEN)));
    setActiveId(item.id);
  }, []);
  const openScreen = useCallback((spec: ScreenSpec) => openItem({ id: screenItemId(spec), kind: "screen", spec }, true), [openItem]);
  const openScreenFresh = useCallback((spec: ScreenSpec) => {
    // đang mở ⇒ CHỈ tăng số làm mới (màn tải lại TẠI CHỖ, giữ nội dung + vị trí cuộn — không dựng lại khung, không nháy); chưa mở ⇒ mở
    const id = screenItemId(spec);
    setNarrowShowsCanvas(true);
    setCanvas((cur) =>
      cur.some((c) => c.id === id)
        ? cur.map((c) => (c.id === id ? { ...c, spec, rev: (c.rev ?? 0) + 1 } as CanvasItem : c))
        : [{ id, kind: "screen", spec, rev: 0 } as CanvasItem, ...cur].slice(0, MAX_OPEN),
    );
    setActiveId(id);
  }, []);
  const refreshItem = useCallback((id: string) => setCanvas((cur) => cur.map((c) => (c.id === id ? ({ ...c, rev: (c.rev ?? 0) + 1 } as CanvasItem) : c))), []);
  const [canvasMax, setCanvasMax] = useState(false);
  const toggleCanvasMax = useCallback(() => setCanvasMax((m) => !m), []);
  useEffect(() => {
    if (!canvasMax) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setCanvasMax(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canvasMax]);
  const screenByKey = useCallback((key: string) => manifestRef.current?.workspaces.flatMap((w) => w.screens).find((s) => s.key === key), []);
  const openCard = useCallback((card: ChatCard, showCanvas = false) => openItem({ id: cardItemId(card), kind: "card", card }, showCanvas), [openItem]);
  const closeItem = useCallback((id: string) => {
    setCanvas((cur) => {
      const next = cur.filter((c) => c.id !== id);
      setActiveId((a) => (a === id ? (next[0]?.id ?? null) : a));
      return next;
    });
  }, []);
  const clearCanvas = useCallback(() => {
    setCanvas([]);
    setActiveId(null);
  }, []);

  // màn việc chỉ ở bàn việc (0094) vẫn là màn mở được — tìm cả ở đó
  const screenFor = useCallback(
    (query: string) => [...(manifestRef.current?.workspaces.flatMap((w) => w.screens) ?? []), ...(manifestRef.current?.desk ?? []), ...(manifestRef.current?.account ?? [])].find((s) => s.query === query),
    [],
  );

  const quickScreens = useMemo(() => pinnedScreens(manifest), [manifest]);

  const refreshInbox = useCallback(async () => {
    setChanges((n) => n + 1); // mọi đường chạy lệnh xong đều gọi đây ⇒ bàn việc biết để tải lại (0094)
    const inbox = manifestRef.current?.inbox;
    if (!inbox) return;
    try {
      const p = await api.query(inbox.query);
      if (p.kind === "table") setInboxCount(Number(p.kpis[0]?.value ?? 0) || 0);
    } catch {
      /* đếm lại lần sau */
    }
  }, []);

  const refreshNotices = useCallback(async () => {
    try {
      const inbox = await api.notices();
      const fresh = inbox.items.filter((n) => n.id > lastNotice.current && !n.read);
      if (lastNotice.current > 0 && fresh[0]) show(fresh.length > 1 ? `${fresh[0].title} (+${fresh.length - 1})` : fresh[0].title);
      lastNotice.current = Math.max(lastNotice.current, ...inbox.items.map((n) => n.id), 0);
      noticesRef.current = inbox.items;
      setNotices(inbox.items);
      setUnread(inbox.unread);
    } catch {
      /* giữ hộp cũ */
    }
  }, [show]);

  const openTarget = useCallback(
    async (query: string, input: Input, title = "", reveal = true) => {
      const m = manifestRef.current;
      if (Object.keys(input).length === 0) {
        const spec = screenFor(query) ?? (m?.inbox?.query === query ? m.inbox : undefined);
        if (spec) return reveal ? openScreen(spec) : openItem({ id: screenItemId(spec), kind: "screen", spec });
      }
      try {
        const page = await api.query(query, input);
        const key = Object.keys(input).sort().map((k) => `${k}=${String(input[k])}`).join("&");
        openCard({ id: `open:${query}?${key}`, kind: "page", title: page.title || title, query, input, page }, reveal);
      } catch (e) {
        show(e instanceof Error ? e.message : "Không mở được", true);
      }
    },
    [openCard, openItem, openScreen, screenFor, show],
  );

  /** Đọc lại hộp trao đổi. Gói Trao đổi tắt / không có quyền ⇒ giữ null, cột "Tin nhắn" chỉ còn trợ lý + thông báo. */
  const refreshTalk = useCallback(async () => {
    if (!talkAvailable(manifestRef.current)) {   // gói tắt / không quyền: không gọi để nhận 404 / 403 mỗi lần có tín hiệu
      talkRef.current = null;
      setTalk(null);
      return;
    }
    try {
      const t = decodeInbox(await api.queryRaw<unknown>("talk.inbox"));
      talkRef.current = t;
      setTalk(t);
    } catch {
      /* gói tắt / không quyền */
    }
  }, []);

  /** Mở một luồng ở cột "Tin nhắn"; thông báo chưa đọc trỏ vào chính luồng này coi như đã đọc — một việc không đếm hai nơi. */
  const openTalk = useCallback(
    (item: TalkItem) => {
      setSection({ kind: "messages" });
      setMessagesPane({ kind: "thread", item });
      setInspectorOpen(false);
      setNarrowShowsCanvas(false);
      const ids = noticeIdsForSubject(noticesRef.current, item.subject_id);
      if (ids.length) void api.markNoticesRead(ids).then(refreshNotices);
    },
    [refreshNotices],
  );
  const closeTalk = useCallback(() => {
    setMessagesPane({ kind: "list" });
    void refreshTalk();
  }, [refreshTalk]);

  const openNotice = useCallback(
    (n: Notice) => {
      if (!n.read) void api.markNoticesRead([n.id]).then(refreshNotices);
      const o = n.open;
      // nhắc tên / trao đổi mới ⇒ mở đúng luồng ở cột "Tin nhắn" (hồ sơ tự bung ra bên phải), như bấm dòng đó trong danh sách
      if (o?.query === "talk.thread" && typeof o.input.subject_id === "string" && typeof o.input.subject_kind === "string") {
        const sid = o.input.subject_id;
        const known = talkRef.current?.items.find((i) => i.subject_id === sid);
        openTalk(known ?? talkItemFor(o.input.subject_kind, sid));
        return;
      }
      // thứ khác ⇒ mở trên khung làm việc, bỏ chọn ở "Tin nhắn" để bên phải hiện lại khung làm việc
      setMessagesPane({ kind: "list" });
      if (o) void openTarget(o.query, o.input, n.title);
    },
    [openTarget, refreshNotices, openTalk],
  );

  const select = useCallback((s: Section) => {
    setSection((cur) => {
      if (s.kind === "messages" && cur.kind === "messages") setMessagesPane({ kind: "list" }); // bấm lại ⇒ về danh sách
      return s;
    });
    setNarrowShowsCanvas(false);
    // phân hệ chỉ có MỘT màn ⇒ không có danh sách để chọn: mở thẳng màn đó (khung dùng trọn chỗ — Shell / useSolo)
    if (s.kind === "workspace") {
      const only = manifestRef.current?.workspaces.find((w) => w.key === s.key)?.screens;
      if (only?.length === 1) openScreen(only[0]);
    }
  }, [openScreen]);

  const toggleRail = useCallback(() => {
    setRailHidden((h) => {
      try {
        localStorage.setItem(RAIL_KEY, h ? "0" : "1");
      } catch {
        /* không lưu được — vẫn đổi trong phiên */
      }
      return !h;
    });
  }, []);

  const refreshDesk = useCallback(async () => {
    try {
      setDeskPage(await api.desk());
      setDeskFailed(false);
    } catch {
      setDeskFailed(true);
    }
  }, []);
  // mở app (refreshInbox lần đầu) và mỗi khi một lệnh chạy xong ⇒ bàn việc + số trên nút "Việc" mới
  useEffect(() => {
    if (!changes) return;
    let alive = true;
    api
      .desk()
      .then((p) => {
        if (alive) {
          setDeskPage(p);
          setDeskFailed(false);
        }
      })
      .catch(() => alive && setDeskFailed(true));
    return () => {
      alive = false;
    };
  }, [changes]);

  const talkOpenSubject = section.kind === "messages" && messagesPane.kind === "thread" ? messagesPane.item.subject_id : null;
  const markAllNoticesRead = useCallback(() => void api.markNoticesRead([]).then(refreshNotices), [refreshNotices]);

  const reloadManifest = useCallback(async () => {
    try {
      const [m, s] = await Promise.all([api.manifest(), auth.session().catch(() => null)]);
      const gone = droppedScreenKeys(manifestRef.current, m);
      if (gone.size) {   // màn vừa rời menu (app xoá, dashboard gỡ) ⇒ đóng tab của nó, không để tab báo "Không thấy app"
        setCanvas((cur) => {
          const next = cur.filter((c) => !(c.kind === "screen" && gone.has(c.spec.key)));
          setActiveId((a) => (a && !next.some((c) => c.id === a) ? (next[0]?.id ?? null) : a));
          return next;
        });
      }
      manifestRef.current = m;
      setManifest(m);
      setSession(s);
      setLoadError(null);
    } catch (e) {
      // phiên hết hạn / không còn là thành viên (theo MÃ core gửi, không theo chữ) ⇒ "Thử lại" vô ích: xoá phiên rồi mới về trang đăng nhập;
      // xoá không được ⇒ ở lại màn lỗi có nút "Đăng nhập lại" (không điều hướng vòng lặp khi cookie còn)
      if (sessionGone(e)) {
        if (await leaveSession(auth.logout, (path) => { window.location.href = path; })) return;
        setLoadError("Phiên đăng nhập không còn dùng được — bấm “Đăng nhập lại”.");
        return;
      }
      setLoadError(e instanceof Error ? e.message : "Không tải được");
    }
  }, []);

  // tải lần đầu: manifest → màn ghim đầu của vai (không để khung làm việc trống chờ người hỏi). Vừa đăng nhập dev ⇒ dùng luôn manifest + phiên
  // mà bước đăng nhập đã đọc (một lần, ngắn hạn — lib/session.ts), không hỏi lại core. Hộp thông báo không cần manifest ⇒ hỏi song song.
  useEffect(() => {
    let alive = true;
    void refreshNotices();
    (async () => {
      const primed = takePrimedDesk();
      if (primed) {
        manifestRef.current = primed.manifest;
        setManifest(primed.manifest);
        setSession(primed.session);
      } else await reloadManifest();
      if (!alive || !manifestRef.current) return;
      // mở app là thấy việc của mình ngay (màn ghim đầu của vai) — không để khung làm việc trống chờ người hỏi
      const home = pinnedScreens(manifestRef.current)[0];
      if (home) setCanvas((cur) => (cur.length ? cur : [{ id: screenItemId(home), kind: "screen", spec: home }]));
      setActiveId((a) => a ?? (home ? screenItemId(home) : null));
      void refreshInbox();
      void refreshTalk();
    })();
    return () => {
      alive = false;
    };
  }, [reloadManifest, refreshInbox, refreshNotices, refreshTalk]);

  // SSE "hộp thông báo có thay đổi" — không mang nội dung, đọc lại qua quyền người dùng
  useEffect(() => {
    if (!manifest) return;
    // tin trao đổi mới thường kèm thông báo (nhắc tên / người phụ trách) ⇒ đọc lại cả hộp trao đổi
    const changed = () => {
      void refreshNotices();
      void refreshTalk();
    };
    return listenSignals("/v1/notifications/stream", changed, changed);
  }, [manifest, refreshNotices, refreshTalk]);

  // SSE hộp tin (0115): luồng mình theo dõi có tin / sửa / thu hồi, hoặc mình đọc ở máy khác ⇒ đọc lại hộp. Chỉ mang MÃ luồng — server đã kiểm
  // lại quyền đọc từng luồng trước khi báo. Gói Trao đổi tắt / không quyền ⇒ không nghe.
  const talkOn = talk !== null;
  useEffect(() => {
    if (!manifest || !talkOn) return;
    const changed = () => void refreshTalk();
    return listenSignals("/v1/talk/stream", changed, changed);
  }, [manifest, talkOn, refreshTalk]);

  const openForm = useCallback<DeskValue["openForm"]>((action, opts = {}) => {
    // LUẬT XÁC NHẬN MỘT CHỖ (ADR 0253): lệnh ĐẢO ĐƯỢC và nút không còn ô nào ⇒ chạy ngay khi chạm, báo kết quả (kèm Hoàn tác nếu có) — không hộp xác nhận rỗng
    if (action.instant && !action.import_only) {
      void (async () => {
        try {
          const r = await api.command(action.command, { ...fixedInput(action, opts.row ?? null), ...(opts.initial ?? {}) }, newKey(), action.route, action.basis);
          if (r.status === "ok") {
            show(r.replayed ? "Đã gửi trước đó — không chạy lại" : `Đã xong: ${action.title}`, false, undoOfOutput(r.output) ?? undefined);
            opts.onDone?.(`Đã xong: ${action.title}`, r.output);
            if (menuChanged(r.output)) void reloadManifest();
          } else if (r.status === "pending_approval") show("Đã gửi duyệt");
          else show(r.error || r.status, true);
        } catch (e) {
          show(e instanceof Error ? e.message : "Không gửi được", true);
        }
      })();
      return;
    }
    // lệnh nhập cả bảng (vd nhập kế hoạch từ Excel) không có form: nút mở thẳng màn Nhập từ Excel, dù nút nằm trên thẻ / màn / hộp thoại nào
    if (action.import_only) {
      const done = opts.onDone;
      setImports((i) => [...i, { id: seq++, action, onDone: done ? () => done("") : undefined }]);
      return;
    }
    setForms((f) => [...f, { id: seq++, action, row: opts.row ?? null, initial: opts.initial, onDone: opts.onDone }]);
  }, [show, reloadManifest]);
  const closeForm = useCallback((id: number) => setForms((f) => f.filter((x) => x.id !== id)), []);
  const openPage = useCallback((query: string, input: Input, title = "") => setPages((p) => [...p, { id: seq++, title, query, input }]), []);
  const closePage = useCallback((id: number) => setPages((p) => p.filter((x) => x.id !== id)), []);
  const openImport = useCallback((action: ActionSpec, onDone?: () => void) => setImports((i) => [...i, { id: seq++, action, onDone }]), []);
  const closeImport = useCallback((id: number) => setImports((i) => i.filter((x) => x.id !== id)), []);

  const ask = useCallback((text: string) => {
    setAskPrompt((p) => ({ text, n: (p?.n ?? 0) + 1 }));
    setSection({ kind: "assistant" });
    setNarrowShowsCanvas(false);
  }, []);

  // đang gọi trong bảng nổi: rời TRƯỚC khi đổi phiên — sau đó "tạm biệt" lúc đóng trang sẽ đi bằng phiên đã đổi / không còn, người kia và số phút bị treo
  const callHub = useCallHub();
  const logout = useCallback(async () => {
    await callHub?.leaveWithin();
    await auth.logout().catch(() => null);
    // đăng nhập lại cần dựng lại toàn bộ trạng thái ⇒ điều hướng cả trang, không dùng router
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login";
  }, [callHub]);
  const switchTenant = useCallback(async (id: string) => {
    await callHub?.leaveWithin();
    await auth.tenant(id);
    window.location.reload();
  }, [callHub]);

  const value: DeskValue = {
    manifest, loadError, session, reloadManifest,
    canvas, activeId, setActiveId, openItem, openScreen, openCard, closeItem, clearCanvas, screenFor, openTarget, quickScreens, screenByKey, openScreenFresh, refreshItem, canvasMax: canvasMax && canvas.length > 0, toggleCanvasMax,
    section, select, railHidden, toggleRail, messagesPane, setMessagesPane, talk, refreshTalk, openTalk, closeTalk, talkOpenSubject,
    inspectorOpen, setInspectorOpen,
    narrowShowsCanvas, setNarrowShowsCanvas,
    forms, openForm, closeForm, pages, openPage, closePage, imports, openImport, closeImport,
    toast, show, inboxCount, changes, deskPage, deskFailed, refreshDesk, refreshInbox, notices, unread, refreshNotices, openNotice, markAllNoticesRead,
    askPrompt, ask, shareTarget, setShareTarget, openRecord, setOpenRecord, recordFilesRev, bumpRecordFiles,
    confirmed,
    setConfirmed: (id, message, approvalId) => {
      setConfirmedMap((c) => ({ ...c, [id]: message }));
      if (approvalId) setConfirmedApproval((c) => ({ ...c, [id]: approvalId }));
    },
    settled: (card) => settledOf(card, confirmed[card.id] === undefined ? undefined : { message: confirmed[card.id], approvalId: confirmedApproval[card.id] }),
    proposalEdits, setProposalEdit: (id, input) => setProposalEdits((p) => ({ ...p, [id]: input })),
    paletteOpen, setPaletteOpen, logout, switchTenant,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
