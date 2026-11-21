import { decodeBrief } from "./brief";
import { decodePage } from "./decode";
import type { ChatCard, Step, StreamEvent, UndoSpec } from "./types";

// Một lượt trả lời đang chạy (LiveTurn bên Swift) và cách áp từng sự kiện NDJSON vào đó (Desk.apply).

export type LiveTurn = { status: string; text: string; steps: Step[]; cards: ChatCard[] };
export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  cards: ChatCard[];
  steps: Step[];
  at: Date;
  /** Ảnh gửi kèm (xem trước cục bộ, chỉ trong phiên này — server không lưu ảnh). */
  images?: string[];
  /** Lượt cũ mở lại: số ảnh đã gửi (server chỉ ghi "có N ảnh"). */
  attachments?: number;
  /** Tài liệu gửi kèm chỉ cho cuộc trò chuyện này (ADR 0249) — tên tệp; thân tệp không lưu. */
  documents?: string[];
  /** Lượt dừng vì chạm trần bước: câu gửi khi bấm "Tiếp tục" (lõi soạn). Không có ⇒ không vẽ nút. */
  cont?: string;
};

export const newTurn = (): LiveTurn => ({ status: "Đang suy nghĩ…", text: "", steps: [], cards: [] });

export function decodeCard(raw: ChatCard): ChatCard {
  const why = Array.isArray(raw.why) ? raw.why.filter((w): w is string => typeof w === "string" && w.trim() !== "") : [];
  const u = (raw.undo ?? null) as { command?: unknown; input?: unknown; title?: unknown } | null;
  const undo =
    u && typeof u === "object" && typeof u.command === "string" && u.command && u.input && typeof u.input === "object" && !Array.isArray(u.input)
      ? { command: u.command, input: u.input as UndoSpec["input"], title: typeof u.title === "string" && u.title ? u.title : "Hoàn tác" }
      : null;
  return { ...raw, page: raw.page ? decodePage(raw.page) : null, preview: raw.preview ? decodePage(raw.preview) : null, why, undo, brief: decodeBrief(raw.brief) };
}

/** Thẻ vừa tới có tự bung ra khung làm việc không: thẻ gọn (xem ngay trong chat), bảng rỗng, đề xuất (xác nhận ngay trong câu trả lời — 0144) và
 *  thẻ kết quả lần tự làm (nút Hoàn tác ở câu trả lời) ⇒ không. */
export function opensOnCanvas(card: ChatCard): boolean {
  if (card.preview || card.kind === "result") return false;
  if (card.kind === "proposal" && card.resumable && !card.aside) return false;
  return !(card.page?.kind === "table" && card.page.rows.length === 0);
}

export type Applied = {
  turn: LiveTurn;
  /** Chữ trả lời chốt khi gặp "done" / "error"; còn lại undefined. */
  reply?: string;
  threadId?: string;
  /** Thẻ vừa tới — nơi gọi quyết định có bung lên khung làm việc không. */
  card?: ChatCard;
  /** "done" của lượt chạm trần bước: câu gửi khi bấm "Tiếp tục". */
  cont?: string;
};

/** Câu "Tiếp tục" từ trường ``limit`` của sự kiện done / lượt đã lưu (lõi soạn câu; thiếu / lạ ⇒ không có nút). */
export function continueOf(limit: unknown): string | undefined {
  const c = limit && typeof limit === "object" ? (limit as { continue?: unknown }).continue : undefined;
  return typeof c === "string" && c.trim() ? c.trim().slice(0, 300) : undefined;
}

export function applyEvent(turn: LiveTurn, ev: StreamEvent): Applied {
  switch (ev.type) {
    case "status":
      return { turn: { ...turn, status: ev.text ?? "" } };
    case "step": {
      if (typeof ev.n !== "number") return { turn };
      const step: Step = { n: ev.n, tool: ev.tool ?? "", title: ev.title ?? "", status: ev.status ?? "running", error: ev.error, summary: ev.summary, card_id: ev.card_id };
      const i = turn.steps.findIndex((s) => s.n === ev.n);
      const steps = i >= 0 ? turn.steps.map((s, j) => (j === i ? step : s)) : [...turn.steps, step];
      return { turn: { ...turn, steps } };
    }
    case "card": {
      if (!ev.card) return { turn };
      const card = decodeCard(ev.card);
      return { turn: { ...turn, cards: [...turn.cards, card] }, card };
    }
    case "text":
      return { turn: { ...turn, text: turn.text + (ev.text ?? "") } };
    case "text_reset":
      return { turn: { ...turn, text: "" } };
    case "done":
      return { turn, reply: ev.text ?? turn.text, threadId: ev.thread_id, cont: continueOf(ev.limit) };
    case "error":
      return { turn, reply: `⚠︎ ${ev.message ?? "Lỗi"}` };
    default:
      return { turn };
  }
}

/** Bước đã xong (kể cả thành đề xuất / chờ duyệt) ⇒ true; đang chạy ⇒ null; bị chặn / lỗi ⇒ false. */
export function stepDone(s: Step): boolean | null {
  if (s.status === "running") return null;
  return ["ok", "proposed", "pending_approval", "dismissed"].includes(s.status);
}

/** Dưới câu trả lời chỉ hiện KẾT QUẢ: mọi thẻ đề xuất + mọi thẻ "trợ lý đã tự làm" (nút Hoàn tác — 0144) + thẻ kết quả cuối (bỏ bảng rỗng).
 *  Thẻ bước trung gian mở qua "Đã làm N bước". */
export function resultCards(cards: ChatCard[]): ChatCard[] {
  const emptyTable = (c: ChatCard) => c.page?.kind === "table" && c.page.rows.length === 0;
  const always = (c: ChatCard) => c.kind === "proposal" || c.kind === "result";
  const shown = cards.filter((c) => !c.aside); // gợi ý bên lề vẽ riêng — `asideCards`
  const pages = shown.filter((c) => !always(c));
  const final = [...pages].reverse().find((c) => !emptyTable(c)) ?? pages[pages.length - 1];
  return shown.filter((c) => always(c) || c.id === final?.id);
}

/** Đề xuất BÊN LỀ của lượt (vd gợi ý ghi nhớ): một dòng nhỏ dưới câu trả lời, trợ lý không chờ. */
export function asideCards(cards: ChatCard[]): ChatCard[] {
  return cards.filter((c) => c.kind === "proposal" && c.aside);
}
