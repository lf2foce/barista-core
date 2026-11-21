// "CHỜ TÔI DUYỆT" (ADR 0253): TỪNG việc người này phải quyết — đề xuất trợ lý soạn + phiếu duyệt — nhóm theo loại, cùng hợp đồng bản chạy thử. Hàm thuần.
import { decodeBrief } from "./brief";
import { decodeCard } from "./chat";
import type { Brief, ChatCard } from "./types";

export type DecideItem = {
  kind: "proposal" | "approval";
  id: string;
  group: string;
  groupTitle: string;
  title: string;
  reason: string;
  requester?: string;
  brief: Brief | null;
  /** Gộp lô được (cùng loại, không phải việc tiền / quyền ngoài phiếu cùng chuỗi) — server nói, web không tự đoán. */
  batchable: boolean;
  /** Đề xuất: thẻ đầy đủ để mở ở khung làm việc. */
  card?: ChatCard;
};

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown) => (typeof v === "string" ? v : "");

export function decodeItems(raw: unknown): DecideItem[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isObj).flatMap((o): DecideItem[] => {
    const kind = o.kind === "approval" ? "approval" : o.kind === "proposal" ? "proposal" : null;
    if (!kind || !str(o.id)) return [];
    return [{
      kind, id: str(o.id), group: str(o.group), groupTitle: str(o.group_title), title: str(o.title), reason: str(o.reason),
      ...(str(o.requester) ? { requester: str(o.requester) } : {}),
      brief: decodeBrief(o.brief), batchable: o.batchable === true,
      ...(isObj(o.card) ? { card: decodeCard(o.card as unknown as ChatCard) } : {}),
    }];
  });
}

export type DecideGroup = { key: string; title: string; kind: "proposal" | "approval"; items: DecideItem[]; batchable: boolean };

/** Nhóm theo loại việc, giữ thứ tự server; "Duyệt cả N" chỉ khi nhóm gộp lô được và có từ 2 việc. */
export function groupItems(items: DecideItem[]): DecideGroup[] {
  const out: DecideGroup[] = [];
  for (const it of items) {
    let g = out.find((x) => x.key === it.group);
    if (!g) out.push((g = { key: it.group, title: it.groupTitle, kind: it.kind, items: [], batchable: it.batchable }));
    g.items.push(it);
    g.batchable = g.batchable && it.batchable;
  }
  return out;
}

export type BatchResult = { id: string; status: string; message: string };

/** Một câu cho cả lô: bao nhiêu xong, bao nhiêu ở lại (căn cứ đã đổi / lỗi) và vì sao. */
export function batchSummary(results: BatchResult[]): { text: string; failed: boolean } {
  const done = results.filter((r) => ["ok", "executed", "dismissed", "pending"].includes(r.status)).length;
  const left = results.length - done;
  if (!left) return { text: `Đã xong ${done} việc`, failed: false };
  const why = results.find((r) => !["ok", "executed", "dismissed", "pending"].includes(r.status))?.message;
  return { text: `Đã xong ${done}, còn ${left} việc ở lại để xem từng cái${why ? ` — ${why}` : ""}`, failed: true };
}

/** Dòng chào ở cột Trợ lý: nói đúng việc (một việc ⇒ tên việc; nhiều ⇒ số lượng theo loại) — bấm mở đúng chỗ, không chỉ "xem ở Việc". */
export function greetingOf(items: DecideItem[]): string {
  if (items.length === 1) {
    const it = items[0];
    return it.kind === "approval" ? `${it.requester ? `${it.requester} xin` : "Có phiếu xin"} anh/chị duyệt: ${it.title}` : `Em soạn sẵn: ${it.title} — xem và xác nhận`;
  }
  const p = items.filter((x) => x.kind === "proposal").length;
  const a = items.length - p;
  const parts = [p ? `${p} đề xuất em soạn sẵn` : "", a ? `${a} phiếu chờ duyệt` : ""].filter(Boolean);
  return `${items.length} việc chờ anh/chị duyệt (${parts.join(", ")}) — xem danh sách`;
}
