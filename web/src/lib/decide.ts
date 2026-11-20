import { applyEvent, newTurn, type LiveTurn } from "./chat";
import type { ChatCard, StreamEvent } from "./types";

// Người Xác nhận / Bỏ một đề xuất của lượt đang DỪNG (quyết định 0049): POST /v1/assistant/proposals/decide trả NDJSON.
// Dòng ĐẦU quyết số phận của form: rejected (lỗi bằng quyền người — form giữ, sửa rồi bấm lại) · decided (đóng form, thẻ xong) · error.
// Nếu decided.resuming ⇒ phần trợ lý chạy tiếp chảy tiếp trên cùng luồng, y như một lượt chat (status · step · card · text · done).
// Phần thuần ở đây (kiểm thử được); nơi gọi lo trạng thái React.

export type DecideOutcome = {
  /** ok · pending_approval · dismissed · (rejected) invalid / conflict / forbidden … · error */
  status: string;
  /** decided ⇒ thẻ xong, form đóng; còn lại ⇒ form giữ, hiện `message`. */
  done: boolean;
  message: string;
  replayed: boolean;
};

export type DecideState = { phase: "first" | "continuing" | "tail"; turn: LiveTurn };
export const startDecide = (): DecideState => ({ phase: "first", turn: newTurn() });

export type DecideStep = {
  state: DecideState;
  /** Có ở sự kiện đầu tiên mang kết quả (rejected / decided / error). */
  outcome?: DecideOutcome;
  /** decided.resuming ⇒ bắt đầu một lượt trả lời mới trong khung chat. */
  startTurn?: boolean;
  /** Lượt chạy tiếp vừa xong ("done" / "error" của phần chạy tiếp) ⇒ chữ trả lời chốt. */
  reply?: string;
  threadId?: string;
  card?: ChatCard;
};

export function decideStep(s: DecideState, ev: StreamEvent): DecideStep {
  if (s.phase === "first") {
    switch (ev.type) {
      case "rejected":
        return { state: { ...s, phase: "tail" }, outcome: { status: ev.status || "invalid", done: false, message: ev.message || ev.status || "Không thực hiện được", replayed: false } };
      case "decided": {
        const resuming = ev.resuming === true;
        return {
          state: { ...s, phase: resuming ? "continuing" : "tail" },
          outcome: { status: ev.status || "ok", done: true, message: ev.message || (ev.status === "dismissed" ? "Đã bỏ đề xuất" : "Đã xong"), replayed: ev.replayed === true },
          startTurn: resuming,
        };
      }
      case "error":
        return { state: { ...s, phase: "tail" }, outcome: { status: "error", done: false, message: ev.message || "Lỗi", replayed: false } };
      default:
        return { state: s }; // sự kiện lạ trước kết quả ⇒ bỏ qua
    }
  }
  if (s.phase === "continuing") {
    const r = applyEvent(s.turn, ev);
    const ended = r.reply !== undefined;
    return { state: { phase: ended ? "tail" : "continuing", turn: r.turn }, reply: r.reply, threadId: r.threadId, card: r.card };
  }
  // thẻ máy chủ phát kèm kết quả (vd thẻ có link gửi khách — chỉ hiện lần này, không lưu): mở dù trợ lý không chạy tiếp
  if (ev.type === "card" && ev.card) return { state: s, card: ev.card as ChatCard };
  return { state: s }; // "done" sau decided không chạy tiếp, hoặc rác sau khi xong
}

/** Luồng hết mà chưa có kết quả nào ⇒ coi như máy chủ không trả lời. */
export const NO_ANSWER: DecideOutcome = { status: "error", done: false, message: "Máy chủ không trả lời", replayed: false };

/**
 * Mở lại cuộc trò chuyện cũ: đề xuất nào đã được quyết (lượt lưu `decided: [{card_id, status, message}]`, hoặc `pending.calls` có status khác
 * "waiting") ⇒ hiện "Đã xong …" / "Đã bỏ đề xuất" thay vì form chờ — không mời người xác nhận lại thứ đã chạy.
 */
export function decidedInTurn(turn: { decided?: unknown; pending?: unknown; asides?: unknown }): Record<string, string> {
  const out: Record<string, string> = {};
  const take = (c: unknown) => {
    const o = (c ?? {}) as { card_id?: unknown; status?: unknown; message?: unknown };
    if (typeof o.card_id !== "string" || typeof o.status !== "string" || o.status === "waiting") return;
    out[o.card_id] = o.status === "dismissed" ? "Đã bỏ đề xuất" : typeof o.message === "string" && o.message ? o.message : "Đã xong";
  };
  if (Array.isArray(turn.decided)) turn.decided.forEach(take);
  const calls = (turn.pending as { calls?: unknown } | undefined)?.calls;
  if (calls && typeof calls === "object") Object.values(calls as Record<string, unknown>).forEach(take);
  // đề xuất bên lề (vd gợi ý ghi nhớ) — lưu theo mã thẻ ở `asides`, không thuộc danh sách chờ
  if (turn.asides && typeof turn.asides === "object") Object.values(turn.asides as Record<string, unknown>).forEach(take);
  return out;
}
