"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, chatStream, decideStream } from "@/lib/api";
import { applyEvent, continueOf, decodeCard, newTurn, opensOnCanvas, type ChatMessage, type LiveTurn } from "@/lib/chat";
import { decidedInTurn, decideStep, NO_ANSWER, startDecide, type DecideOutcome } from "@/lib/decide";
import { parseISO } from "@/lib/format";
import { IMAGE_ONLY_TEXT, type ChatImage } from "@/lib/images";
import type { ChatDocument } from "@/lib/api";
import { DOCUMENT_ONLY_TEXT } from "@/lib/documents";
import { morningOutcome, type MorningOutcome } from "@/lib/morning";
import type { Attention, ChatCard, Input, Step, StreamEvent, ThreadSummary } from "@/lib/types";
import type { Section } from "@/lib/talk";
import { useDesk } from "./desk-context";

type ChatValue = {
  messages: ChatMessage[];
  live: LiveTurn | null;
  threadId: string | null;
  threads: ThreadSummary[];
  suggestions: string[];
  attention: Attention[];
  /** Đề xuất trợ lý soạn sẵn sáng nay (0074 A). */
  morning: ChatCard[];
  decideMorning: (card: ChatCard, decision: "confirm" | "dismiss", input: Input, key: string) => Promise<MorningOutcome>;
  /** ``documents``: tài liệu người thả vào chat và chọn "chỉ dùng trong cuộc trò chuyện này" (ADR 0249) — đi kèm câu hỏi, không vào kho / hồ sơ. */
  send: (text: string, images?: ChatImage[], documents?: ChatDocument[]) => void;
  newThread: () => void;
  openThread: (id: string) => Promise<void>;
  /** Xác nhận / Bỏ một đề xuất của lượt đang dừng (0049). Trả kết quả của chính lệnh cho form ngay ở dòng đầu; phần trợ lý chạy tiếp
   * (nếu đủ quyết định) chảy vào khung chat như một lượt trả lời mới, chạy nền sau khi form đã đóng. */
  decide: (card: ChatCard, decision: "confirm" | "dismiss", input: Input, key: string) => Promise<DecideOutcome>;
  /** Một dòng hệ thống trong khung chat ("✓ Đã xong…", "✕ Đã bỏ đề xuất…"). */
  addSystem: (text: string) => void;
  /** TRÌNH TẠO APP (0098 — kiểu Claude Design): chế độ trò chuyện riêng (lời dặn + công cụ riêng), app xem thử trên khung làm việc. `null` = trợ lý
   *  cá nhân như thường. Vào trình tạo cất cuộc trò chuyện đang có; "Xong" trả lại nguyên vẹn. */
  builder: Builder | null;
  openBuilder: (opts?: { prompt?: string; appId?: string }) => void;
  exitBuilder: () => void;
  /** "Dừng" lượt đang chạy (chat gõ chữ / trình tạo): server huỷ lượt; phần đã làm vẫn lưu. `canStop` = lượt hiện tại dừng được. */
  stop: () => void;
  canStop: boolean;
};

/** `origin`: mở trình tạo từ đâu (vd phân hệ "App của tôi") — thanh bên vẫn sáng mục đó, "Xong" quay lại đúng chỗ (như Claude Design mở dự án riêng). */
export type Builder = { appId: string | null; origin: Section };

const Ctx = createContext<ChatValue | null>(null);
export function useChat(): ChatValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useChat ngoài ChatProvider");
  return v;
}

const mid = () => crypto.randomUUID();

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const desk = useDesk();
  const { openCard, manifest, setShareTarget, show, setConfirmed } = desk;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [attention, setAttention] = useState<Attention[]>([]);
  const [morning, setMorning] = useState<ChatCard[]>([]);
  const busy = useRef(false);
  const threadRef = useRef<string | null>(null);
  const turnKey = useRef<string | null>(null);
  const [canStop, setCanStop] = useState(false);
  const [builder, setBuilder] = useState<Builder | null>(null);
  const builderRef = useRef<Builder | null>(null);
  const saved = useRef<{ messages: ChatMessage[]; threadId: string | null } | null>(null);
  const messagesRef = useRef<ChatMessage[]>([]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  /** Trình tạo vừa mở / sửa app ⇒ tải lại menu (app mới, ghim) rồi đưa ĐÚNG app đó lên khung làm việc, tải lại dữ liệu. */
  const showBuilderApp = useCallback(
    async (appId: string) => {
      let spec = desk.screenByKey(`app-${appId}`);
      if (!spec) {   // app vừa tạo ⇒ chưa có trong menu: tải lại menu (app mới + ghim) rồi tìm
        await desk.reloadManifest();
        spec = desk.screenByKey(`app-${appId}`);
      }
      if (spec) desk.openScreenFresh(spec);
    },
    [desk],
  );

  useEffect(() => {
    if (!manifest) return;
    void api.suggestions().then(setSuggestions).catch(() => null);
    void api.threads().then(setThreads).catch(() => null);
    void api.attention().then(setAttention).catch(() => null);
    void api.morning().then(setMorning).catch(() => null);
  }, [manifest]);

  const decideMorning = useCallback<ChatValue["decideMorning"]>(async (card, decision, input, key) => {
    try {
      const o = morningOutcome(await api.morningDecide(card.id, decision, input, key), decision);
      if (o.done) setMorning((cur) => cur.filter((c) => c.id !== card.id));
      return o;
    } catch (e) {
      return { status: "error", done: false, message: e instanceof Error ? e.message : "Không gửi được quyết định" };
    }
  }, []);

  // một lượt trả lời (chat chữ / ghi chú hiện trường): áp sự kiện vào khung đang trả lời, xong thì chốt thành tin nhắn
  const runTurn = useCallback(
    (events: AsyncGenerator<StreamEvent>) => {
      let turn = newTurn();
      setLive(turn);
      void (async () => {
        let reply = "";
        let cont: string | undefined;
        try {
          for await (const ev of events) {
            if (ev.type === "transcript") {
              setMessages((m) => [...m, { id: mid(), role: "user", text: `Ghi chú hiện trường: ${ev.text ?? ""}`, cards: [], steps: [], at: new Date() }]);
              continue;
            }
            const r = applyEvent(turn, ev);
            turn = r.turn;
            // trình tạo app (0098): mỗi bước dựng xong server phát thẻ app ⇒ chat chỉ giữ thẻ app MỚI NHẤT (không chất 10 thẻ giống nhau)
            if (builderRef.current && r.card?.query === "apps.open") {
              const last = r.card;
              turn = { ...turn, cards: [...turn.cards.filter((c) => c.query !== "apps.open"), last] };
            }
            setLive(turn);
            if (r.threadId) {
              threadRef.current = r.threadId;
              setThreadId(r.threadId);
            }
            // thứ trợ lý vừa đụng tới bung ra khung làm việc — trừ bảng rỗng (vẫn có trong chat để bấm xem)
            const appId = builderRef.current && r.card?.query === "apps.open" ? r.card.input?.app_id : undefined;
            if (typeof appId === "string" && builderRef.current) {
              builderRef.current = { ...builderRef.current, appId };   // trình tạo vừa tạo / sửa app ⇒ các lượt sau làm trên app này
              setBuilder(builderRef.current);
              void showBuilderApp(appId);       // HIỆN DẦN: app lên khung ngay sau từng bước, không đợi hết lượt
            } else if (builderRef.current) {
              // trình tạo: khung bên phải dành cho app — thẻ khác (câu hỏi, mô tả nguồn) chỉ ở trong chat
            } else if (r.card && opensOnCanvas(r.card)) openCard(r.card, false); // có thẻ gọn ⇒ xem ngay trong chat, "Mở" mới ra khung
            if (r.reply !== undefined) reply = r.reply;
            if (r.cont) cont = r.cont;
          }
        } catch (e) {
          if (!reply) reply = `⚠︎ ${e instanceof Error ? e.message : "Trợ lý không trả lời"}`;
        }
        const done = builderRef.current?.appId;   // hết lượt: tải lại menu (tên / ghim có thể vừa đổi) rồi hiện bản cuối
        if (done) void desk.reloadManifest().then(() => showBuilderApp(done));
        setMessages((m) => [...m, { id: mid(), role: "assistant", text: reply || turn.text, cards: turn.cards, steps: turn.steps, at: new Date(), cont }]);
        setLive(null);
        busy.current = false;
        turnKey.current = null;
        setCanStop(false);
        void api.threads().then(setThreads).catch(() => null);
      })();
    },
    [openCard, showBuilderApp, desk],
  );

  const send = useCallback(
    (raw: string, images: ChatImage[] = [], documents: ChatDocument[] = []) => {
      // chỉ gửi ảnh / tài liệu, không gõ gì ⇒ câu mặc định
      const text = raw.trim() || (images.length ? IMAGE_ONLY_TEXT : documents.length ? DOCUMENT_ONLY_TEXT : "");
      if (!text || busy.current) return;
      busy.current = true;
      setMessages((m) => [...m, { id: mid(), role: "user", text, cards: [], steps: [], at: new Date(), images: images.map((i) => i.preview), documents: documents.map((d) => d.filename) }]);
      const b = builderRef.current;
      turnKey.current = crypto.randomUUID();
      setCanStop(true);
      // hỏi từ khối trao đổi (shareTarget) ⇒ gửi kèm loại + mã đối tượng: trợ lý đọc đúng luồng đó bằng quyền người hỏi (0115)
      const st = desk.shareTarget;
      const subject = !b && st ? { kind: st.subjectKind, id: st.subjectId, title: st.title } : null;
      runTurn(chatStream(text, threadRef.current, undefined, images, { ...(b ? { mode: "builder", app_id: b.appId } : {}), turn_key: turnKey.current, subject, documents }));
    },
    [runTurn, desk.shareTarget],
  );

  const newThread = useCallback(() => {
    if (busy.current) return;
    if (builderRef.current) { builderRef.current = null; setBuilder(null); saved.current = null; }   // đang ở trình tạo app ⇒ thoát, sang trò chuyện mới
    setMessages([]);
    threadRef.current = null;
    setThreadId(null);
    setShareTarget(null);
  }, [setShareTarget]);

  const openThread = useCallback(
    async (id: string) => {
      if (busy.current) return;
      if (builderRef.current) { builderRef.current = null; setBuilder(null); saved.current = null; }   // mở cuộc trò chuyện cũ ⇒ thoát trình tạo app
      try {
        const t = await api.thread(id);
        // đề xuất đã quyết trong lượt cũ ⇒ thẻ hiện kết quả, không còn form chờ / nút "Xác nhận cả N"
        for (const turn of t.turns) for (const [cid, msg] of Object.entries(decidedInTurn(turn))) setConfirmed(cid, msg);
        threadRef.current = t.id;
        setThreadId(t.id);
        setMessages(
          t.turns.map((x) => ({
            id: mid(),
            role: (x.role === "user" || x.role === "system" ? x.role : "assistant") as ChatMessage["role"],
            text: x.text,
            cards: ((x.cards ?? []) as ChatCard[]).map(decodeCard),
            steps: (x.steps ?? []) as Step[],
            at: (x.at && parseISO(x.at)) || new Date(),
            attachments: Array.isArray(x.attachments) ? x.attachments.filter((a) => (a as { kind?: unknown })?.kind === "image").length : 0,
            documents: Array.isArray(x.attachments)
              ? x.attachments.flatMap((a) => { const d = a as { kind?: unknown; filename?: unknown }; return d?.kind === "document" && typeof d.filename === "string" ? [d.filename] : []; })
              : [],
            cont: continueOf(x.limit),
          })),
        );
      } catch (e) {
        show(e instanceof Error ? e.message : "Không mở được cuộc trò chuyện", true);
      }
    },
    [show, setConfirmed],
  );

  const openBuilder = useCallback<ChatValue["openBuilder"]>(
    (opts = {}) => {
      if (busy.current) return;
      const cur = builderRef.current;
      if (cur && (opts.appId === undefined || opts.appId === cur.appId)) {   // đang ở trình tạo trên đúng app này ⇒ giữ cuộc trò chuyện
        if (opts.prompt?.trim()) send(opts.prompt);
        return;
      }
      if (!cur) saved.current = { messages: messagesRef.current, threadId: threadRef.current };
      builderRef.current = { appId: opts.appId ?? null, origin: cur?.origin ?? desk.section };
      setBuilder(builderRef.current);
      setMessages([]);
      threadRef.current = null;
      setThreadId(null);
      desk.select({ kind: "assistant" });
      if (opts.appId) void showBuilderApp(opts.appId);
      const prompt = opts.prompt?.trim();
      if (prompt) {
        busy.current = true;
        setMessages([{ id: mid(), role: "user", text: prompt, cards: [], steps: [], at: new Date() }]);
        turnKey.current = crypto.randomUUID();
        setCanStop(true);
        runTurn(chatStream(prompt, null, undefined, [], { mode: "builder", app_id: opts.appId ?? null, turn_key: turnKey.current }));
      }
    },
    [desk, runTurn, send, showBuilderApp],
  );

  const stop = useCallback(() => {
    const key = turnKey.current;
    if (!key) return;
    setCanStop(false);
    void api.stopTurn(key).catch(() => null);   // server huỷ lượt rồi gửi "done" (stopped) ⇒ lượt kết thúc như thường
  }, []);

  const exitBuilder = useCallback(() => {
    if (busy.current) return;
    const origin = builderRef.current?.origin;
    builderRef.current = null;
    setBuilder(null);
    const back = saved.current;
    saved.current = null;
    setMessages(back?.messages ?? []);
    threadRef.current = back?.threadId ?? null;
    setThreadId(back?.threadId ?? null);
    void desk.reloadManifest();
    if (origin) desk.select(origin);   // về đúng chỗ đã mở trình tạo; app vừa làm vẫn ở khung bên cạnh
  }, [desk]);

  const addSystem = useCallback((text: string) => {
    setMessages((m) => [...m, { id: mid(), role: "system", text, cards: [], steps: [], at: new Date() }]);
  }, []);

  const decide = useCallback<ChatValue["decide"]>(
    (card, decision, input, key) =>
      new Promise<DecideOutcome>((resolve) => {
        let answered = false;
        const answer = (o: DecideOutcome) => {
          if (!answered) {
            answered = true;
            resolve(o);
          }
        };
        void (async () => {
          let st = startDecide();
          let owns = false; // lượt chạy tiếp đang chiếm khung "đang trả lời" (không chen khi người đang hỏi câu khác)
          let reply: string | undefined;
          try {
            for await (const ev of decideStream(card.id, decision, input, key)) {
              const r = decideStep(st, ev);
              st = r.state;
              if (r.outcome) answer(r.outcome);
              if (r.startTurn && !busy.current) {
                busy.current = true;
                owns = true;
                setLive(st.turn);
              }
              if (owns && st.phase !== "first") {
                setLive(st.turn);
                if (r.threadId) {
                  threadRef.current = r.threadId;
                  setThreadId(r.threadId);
                }
                if (r.card && opensOnCanvas(r.card)) openCard(r.card, false); // có thẻ gọn ⇒ xem ngay trong chat, "Mở" mới ra khung
                if (r.reply !== undefined) reply = r.reply;
              } else if (r.card) openCard(r.card, false); // trợ lý không chạy tiếp: thẻ kết quả (vd link gửi khách) vẫn mở
            }
          } catch (e) {
            answer({ status: "error", done: false, message: e instanceof Error ? e.message : "Không gửi được quyết định", replayed: false });
            if (owns && reply === undefined) reply = `⚠︎ ${e instanceof Error ? e.message : "Trợ lý ngừng giữa chừng"}`;
          }
          answer(NO_ANSWER);
          if (owns) {
            const t = st.turn;
            setMessages((m) => [...m, { id: mid(), role: "assistant", text: reply || t.text, cards: t.cards, steps: t.steps, at: new Date() }]);
            setLive(null);
            busy.current = false;
            void api.threads().then(setThreads).catch(() => null);
          }
        })();
      }),
    [openCard],
  );

  return (
    <Ctx.Provider value={{ messages, live, threadId, threads, suggestions, attention, morning, decideMorning, send, newThread, openThread,
                           decide, addSystem, builder, openBuilder, exitBuilder, stop, canStop }}>
      {children}
    </Ctx.Provider>
  );
}
