import { decodeComposerItem } from "./decode";
import type { ComposerItem, ComposerOverflowBlock, ComposerOverflowPage, ComposerTurn, StreamEvent } from "./types";

/** Soạn với AI (thử nghiệm): tách chữ thành phần thường + chỗ giữ "[cần số liệu: …]" để tô — không đổi, không tính gì. */
export function splitPlaceholders(text: string): { text: string; hole: boolean }[] {
  return text
    .split(/(\[cần số liệu:[^\]]*\])/i)
    .filter((p) => p !== "")
    .map((p) => ({ text: p, hole: /^\[cần số liệu:/i.test(p) }));
}

export type LiveStep = { title: string; status: "running" | "ok" | "error"; reason?: string };
/** Lượt đang chạy (luồng `/v1/compose/stream`): web chỉ ghép sự kiện server gửi để vẽ — không tự tính số. */
export type LiveTurn = {
  text: string;
  block: string;
  mode: string;
  status: string;
  steps: LiveStep[];
  /** bảng vừa lấy (hiện trước khi AI viết xong) */
  data: ComposerItem[];
  /** bản soạn đầy đủ khi xong */
  blocks: ComposerItem[] | null;
  title: string;
  sessionId: string;
  error: string;
  done: boolean;
};

/** `sessionId` chỉ có khi server đã nhận lượt (sự kiện `session`) — chưa có ⇒ lượt chưa được lưu, lỗi hiện tại chỗ. */
export function liveTurn(text: string, block = "", mode = ""): LiveTurn {
  return { text, block, mode, status: "Đang bắt đầu…", steps: [], data: [], blocks: null, title: "", sessionId: "", error: "", done: false };
}

/** Ghép một sự kiện luồng vào lượt đang chạy (thuần, không đổi lượt cũ). */
export function applyComposeEvent(t: LiveTurn, ev: StreamEvent): LiveTurn {
  switch (ev.type) {
    case "session":
      return typeof ev.session_id === "string" ? { ...t, sessionId: ev.session_id } : t;
    case "status":
      return typeof ev.text === "string" ? { ...t, status: ev.text } : t;
    case "step": {
      const title = ev.title || ev.tool || "Truy vấn";
      if (ev.status === "running") return { ...t, steps: [...t.steps, { title, status: "running" }] };
      const status: LiveStep["status"] = ev.status === "error" ? "error" : "ok";
      const i = t.steps.map((s) => s.status === "running" && s.title === title).lastIndexOf(true);
      const reason = ev.reason ? { reason: ev.reason } : {};
      let steps = i < 0 ? [...t.steps, { title, status, ...reason }] : t.steps.map((s, j) => (j === i ? { ...s, status, ...reason } : s));
      // lấy được ⇒ gỡ các dòng LỖI trước đó của cùng bước: AI đã tự sửa đầu vào, số vẫn đủ (lõi gỡ y như vậy khi lưu lượt — compose.settle_steps)
      if (status === "ok") steps = steps.filter((s) => !(s.status === "error" && s.title === title));
      return { ...t, steps };
    }
    case "data":
      return ev.block ? { ...t, data: [...t.data, decodeComposerItem(ev.block)] } : t;
    case "blocks":
      return { ...t, blocks: Array.isArray(ev.blocks) ? ev.blocks.map(decodeComposerItem) : [], title: ev.title ?? t.title };
    case "error":
      return { ...t, done: true, error: ev.message || "Chưa soạn được — thử lại" };
    case "done":
      return { ...t, done: true, error: ev.status === "failed" ? ev.error || "Chưa soạn được — thử lại" : "" };
    default:
      return t;
  }
}

/** Lượt cuối là AI báo chưa soạn được / đã dừng ⇒ lời người nói ngay trước đó (để "Thử lại" chạy lại đúng lượt, không thêm lượt mới). */
export function retryTarget(turns: ComposerTurn[]): ComposerTurn | null {
  const last = turns[turns.length - 1];
  if (!last || last.role !== "assistant" || (last.status !== "failed" && last.status !== "stopped")) return null;
  for (let i = turns.length - 2; i >= 0; i--) if (turns[i].role === "user") return turns[i];
  return null;
}

/** Nhật ký bước: gộp các bước GIỐNG HỆT (cùng tên, cùng trạng thái, cùng lý do) thành một dòng "× n" — vị trí theo lần xuất hiện đầu. Bước đang chạy giữ riêng. */
export function groupSteps<T extends { title: string; status: string; reason?: string }>(steps: T[]): (T & { count: number })[] {
  const out: (T & { count: number })[] = [];
  const at = new Map<string, number>();
  for (const s of steps) {
    const k = s.status === "running" ? "" : `${s.status}|${s.title}|${s.reason ?? ""}`;
    const i = k ? at.get(k) : undefined;
    if (i !== undefined) out[i] = { ...out[i], count: out[i].count + 1 };
    else {
      if (k) at.set(k, out.length);
      out.push({ ...s, count: 1 });
    }
  }
  return out;
}

/** Trang còn tràn khung của phiên bản HIỆN HÀNH: lời báo của lượt AI cuối chỉ đúng khi nó đo đúng phiên bản đang xem (sửa tiếp ⇒ phiên bản mới ⇒ lời báo cũ không vẽ nút). */
export function currentOverflow(turns: ComposerTurn[], version: number): ComposerOverflowPage[] {
  const last = turns[turns.length - 1];
  return last && last.role === "assistant" && last.version === version ? last.overflow : [];
}

/** Khối của trang tràn có nút bấm một chạm: khối gây tràn trước, tối đa `max` — bấm ⇒ gửi đúng `action.input.instruction` cho khối đó (lệnh `compose.revise`). */
export function overflowActions(page: ComposerOverflowPage, max = 2): ComposerOverflowBlock[] {
  return page.blocks.filter((b) => b.action).sort((a, b) => Number(b.culprit) - Number(a.culprit)).slice(0, max);
}
