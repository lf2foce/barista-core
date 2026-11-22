// BẢN CHẠY THỬ CHUẨN (ADR 0253) — giải mã + trạng thái "đã quyết" của một thẻ chờ duyệt. Hàm thuần.
import type { Brief, ChatCard, Input } from "./types";

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

export function decodeBrief(raw: unknown): Brief | null {
  if (!isObj(raw)) return null;
  const s = isObj(raw.subject) && str(raw.subject.label) ? raw.subject : null;
  const o = s && isObj(s.open) && str(s.open.query) ? { query: str(s.open.query), input: (isObj(s.open.input) ? s.open.input : {}) as Input } : null;
  const r = isObj(raw.reversible) ? raw.reversible : {};
  return {
    title: str(raw.title),
    subject: s ? { label: str(s.label), open: o } : null,
    changes: (Array.isArray(raw.changes) ? raw.changes : []).filter(isObj).filter((c) => str(c.label)).map((c) => ({ label: str(c.label), before: str(c.before), after: str(c.after) })),
    reason: str(raw.reason),
    reversible: { ok: typeof r.ok === "boolean" ? r.ok : null, how: str(r.how) },
  };
}

export type Settled = { label: string; tone: "ok" | "muted"; approvalId?: string; resolve?: { decision: string; label: string }[] };

/** Trạng thái CHƯA có kết cục ở bản thẻ đang giữ — người vừa bấm (đối soát) thì chữ vừa bấm mới hơn bản thẻ cũ. */
const TRANSIENT = new Set(["running", "needs_reconciliation"]);
export type DecisionLabel = { message: string; approvalId?: string; status?: string };

/** Thẻ đã quyết chưa — SERVER nói (`state`); `local` chỉ là chữ vừa bấm (trước khi tải lại), không phải nguồn sự thật. */
export function settledOf(card: ChatCard, local?: DecisionLabel): Settled | null {
  const server = card.state && card.state !== "proposed" && !(local?.status && TRANSIENT.has(card.state)) ? card.state : undefined;
  const state = server ?? local?.status;
  const label = state === card.state ? card.state_label : local?.message;
  switch (state) {
    case "executed":
      return { label: label || "Đã xong", tone: "ok" };
    case "sent":
      return { label: "Đã gửi duyệt", tone: "ok", approvalId: card.approval_id };
    case "dismissed":
      return { label: "Đã bỏ đề xuất", tone: "muted" };
    case "expired":
      return { label: "Đề xuất đã hết hạn", tone: "muted" };
    case "running":
      return { label: label || "Đang thực hiện…", tone: "muted" };
    case "failed":
      return { label: label || "Thực hiện lỗi", tone: "muted" };
    case "needs_reconciliation":
      return { label: label || "Cần đối soát với hệ ngoài", tone: "muted", ...(state === card.state && card.resolve?.length ? { resolve: card.resolve } : {}) };
  }
  if (!local) return null;
  return { label: local.message, tone: local.message === "Đã bỏ đề xuất" ? "muted" : "ok", approvalId: local.approvalId };
}
