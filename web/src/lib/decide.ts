import { applyEvent, newTurn, type LiveTurn } from "./chat";
import type { ChatCard, StreamEvent } from "./types";

// Người Xác nhận / Bỏ một đề xuất của lượt đang DỪNG (quyết định 0049): POST /v1/assistant/proposals/decide trả NDJSON.
// Dòng ĐẦU quyết số phận của form: rejected (lỗi bằng quyền người — form giữ, sửa rồi bấm lại) · decided (đóng form, thẻ xong) · error.
// Nếu decided.resuming ⇒ phần trợ lý chạy tiếp chảy tiếp trên cùng luồng, y như một lượt chat (status · step · card · text · done).
// Phần thuần ở đây (kiểm thử được); nơi gọi lo trạng thái React.

/** Quyết định gửi về server: Xác nhận · Bỏ · ĐỐI SOÁT (nút server gửi ở `card.resolve` — giá trị gửi lại nguyên văn). */
export type Decision = "confirm" | "dismiss" | (string & {});

export type DecideOutcome = {
  /** ok · pending_approval · dismissed · (rejected) invalid / conflict / forbidden … · error */
  status: string;
  /** decided ⇒ thẻ xong, form đóng; còn lại ⇒ form giữ, hiện `message`. */
  done: boolean;
  message: string;
  replayed: boolean;
  /** Phiếu duyệt sinh ra (thẻ thành "Đã gửi duyệt") — mở đúng phiếu này. */
  approvalId?: string;
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
          outcome: { status: ev.status || "ok", done: true, message: ev.message || (ev.status === "dismissed" ? "Đã bỏ đề xuất" : "Đã xong"), replayed: ev.replayed === true, ...(ev.approval_id ? { approvalId: ev.approval_id } : {}) },
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
