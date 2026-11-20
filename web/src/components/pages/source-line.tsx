import { date } from "@/lib/format";
import { sourceText } from "@/lib/source";
import type { BlockSource } from "@/lib/types";

/** Nguồn gốc của số dưới một khối (0220): nguồn · phạm vi · đủ tới ngày nào; cảnh báo độ phủ tô màu cảnh báo. Server quyết nội dung, web chỉ hiện. */
export function SourceLine({ source }: { source?: BlockSource }) {
  if (!source) return null;
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <p className="t12 truncate text-muted-foreground" title={sourceText(source, date)}>{sourceText(source, date)}</p>
      {source.warn ? <p className="t12 text-warn">{source.warn}</p> : null}
    </div>
  );
}
