"use client";

import { ChartColumn } from "lucide-react";
import { EditFrame } from "@/components/pages/edit-block";
import { WhyButton } from "@/components/pages/why-button";
import { SourceLine } from "@/components/pages/source-line";
import type { ActionSpec, BlockSource } from "@/lib/types";
import { chartBodyHeight, hasChartData, type ChartData } from "@/lib/chart";
import { ClampNote } from "@/components/clamp-note";
import { ChartBody } from "./lazy";

// Khung thẻ biểu đồ: vẽ ngay (tiêu đề, ghi chú, nguồn). Phần vẽ Recharts nặng ở chart-body.tsx, tải theo nhu cầu (lazy.tsx) — giữ sẵn chỗ
// bằng chiều cao của biểu đồ để thẻ không nhảy khi vẽ xong.

/** Các khối biểu đồ liền nhau: lưới 1–2 cột theo bề rộng của thẻ (thẻ rộng không kéo dài biểu đồ ra hết bề ngang). */
export function ChartGrid({ blocks, onChanged }: { blocks: (ChartData & { edit?: ActionSpec; source?: BlockSource })[]; onChanged?: () => void }) {
  return (
    <div className="@container">
      <div className="grid grid-cols-1 items-start gap-3 @min-[824px]:grid-cols-2">
        {blocks.map((b, i) => {
          // số biểu đồ lẻ ⇒ biểu đồ cuối trải hết hai cột, không để trống nửa thẻ (rà UI 18/11)
          const wide = blocks.length % 2 === 1 && i === blocks.length - 1;
          return (
            <div key={i} className={wide ? "@min-[824px]:col-span-2" : undefined}>
              <EditFrame edit={b.edit} onChanged={onChanged}><ChartCard block={b} /></EditFrame>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function ChartCard({ block }: { block: ChartData & { source?: BlockSource; why?: string } }) {
  return (
    <figure className="flex min-w-0 flex-col gap-3 rounded-[14px] border border-hairline bg-surface p-4" aria-label={block.title || "Biểu đồ"}>
      {block.title || block.why ? (
        <div className="flex min-w-0 items-start gap-2">
          {block.title ? <figcaption className="t14m min-w-0 flex-1">{block.title}</figcaption> : <span className="flex-1" />}
          {block.why ? <WhyButton prompt={block.why} /> : null}
        </div>
      ) : null}
      {hasChartData(block) ? (
        <div className="flex min-w-0 flex-1 flex-col" style={{ minHeight: chartBodyHeight(block) }}>
          <ChartBody block={block} />
        </div>
      ) : (
        <p className="t12 flex min-h-[120px] items-center justify-center gap-1.5 text-muted-foreground">
          <ChartColumn className="size-4" aria-hidden />
          Chưa đo được — chưa có dữ liệu đủ tin cậy
        </p>
      )}
      {block.note ? <ClampNote text={block.note} /> : null}
      <SourceLine source={block.source} />
    </figure>
  );
}
