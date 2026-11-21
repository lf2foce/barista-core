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

export type Settled = { label: string; tone: "ok" | "muted"; approvalId?: string };

/** Thẻ đã quyết chưa — SERVER nói (`state`); `local` chỉ là chữ vừa bấm (trước khi tải lại), không phải nguồn sự thật. */
export function settledOf(card: ChatCard, local?: { message: string; approvalId?: string }): Settled | null {
  switch (card.state) {
    case "executed":
      return { label: card.state_label || "Đã xong", tone: "ok" };
    case "sent":
      return { label: "Đã gửi duyệt", tone: "ok", approvalId: card.approval_id };
    case "dismissed":
      return { label: "Đã bỏ đề xuất", tone: "muted" };
    case "expired":
      return { label: "Đề xuất đã hết hạn", tone: "muted" };
  }
  if (!local) return null;
  return { label: local.message, tone: local.message === "Đã bỏ đề xuất" ? "muted" : "ok", approvalId: local.approvalId };
}
