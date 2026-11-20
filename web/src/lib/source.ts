import type { BlockSource } from "./types";

/** Một dòng nguồn gốc (0220): "Nguồn: Chi phí quảng cáo · số cả công ty · đủ tới 13/11/2026". Phần server không khai thì bỏ. */
export function sourceText(s: BlockSource, fmtDate: (iso: string) => string): string {
  const parts = [s.kind === "sql" ? `Nguồn: ${s.label} (SQL tự do)` : `Nguồn: ${s.label}`];
  if (s.scope) parts.push(`số ${s.scope}`);
  if (s.through) parts.push(`đủ tới ${fmtDate(s.through)}`);
  return parts.join(" · ");
}
