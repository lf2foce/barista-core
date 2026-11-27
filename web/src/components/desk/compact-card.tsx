"use client";

import { compactCount } from "@/lib/compact";
import { ArrowUpRight, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Input, Page } from "@/lib/types";
import { cn } from "@/lib/utils";
import { CardView } from "@/components/pages/card";
import { DataTable, HeaderCharts, KpiRow } from "@/components/pages/table";
import { useDesk } from "./desk-context";

/**
 * THẺ GỌN (0094 đợt 2, CompactCard.swift): CHÍNH trang của một truy vấn, server cắt bớt (≤ 5 dòng theo thứ tự module đã xếp, ≤ 3 cột) — vẽ bằng
 * bộ vẽ trang sẵn có nên nút trong khối (ghi đã gọi, kéo trạng thái…) chạy y như màn đầy đủ. Dùng chung cho câu trả lời trong chat và mục bung
 * trên bàn việc. "3/28" chỉ khi server biết tổng thật. Bấm một nút xong ⇒ tải lại đúng truy vấn đó (bản gọn); "Mở" ⇒ màn / thẻ đầy đủ.
 */
export function CompactCard({ title, query, input, initial, onOpen }: { title: string; query: string; input: Input; initial?: Page | null; onOpen: () => void }) {
  const desk = useDesk();
  const [page, setPage] = useState<Page | null>(initial ?? null);
  const [state, setState] = useState<"idle" | "loading" | "failed" | "gone">(initial ? "idle" : "loading");

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setState("loading");
      try {
        setPage(await api.queryCompact(query, input, signal));
        setState("idle");
      } catch {
        if (!signal?.aborted) setState("failed");
      }
    },
    [query, input],
  );

  // chưa có bản gọn sẵn (mục bung trên bàn việc) ⇒ tải một lần bằng quyền lúc này
  useEffect(() => {
    if (initial) return;
    const c = new AbortController();
    api
      .queryCompact(query, input, c.signal)
      .then((p) => {
        setPage(p);
        setState("idle");
      })
      .catch(() => !c.signal.aborted && setState("failed"));
    return () => c.abort();
  }, [initial, query, input]);

  // làm xong một việc ngay trên thẻ ⇒ tải lại đúng truy vấn này (dòng vừa làm biến mất) + bàn việc / số trên nút cập nhật
  const changed = () => {
    void load();
    void desk.refreshInbox();
  };

  const c = page && page.kind !== "detail" ? page.compact : undefined;
  // tổng của thẻ gọn có thể cộng lẫn nhiều loại (cảnh báo + thẻ phòng + việc) ⇒ "3/14" vô nghĩa với người đọc; chỉ nói còn bao nhiêu mục chưa hiện (nút "Mở" xem đủ)
  const count = compactCount(c);
  return (
    <div className="flex flex-col overflow-hidden rounded-[14px] border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-hairline px-3 py-2">
        <span className="t14m min-w-0 flex-1 truncate">{title}</span>
        {count ? <span className="t12 tnum shrink-0 text-muted-foreground" title={c?.total == null ? "Chưa biết tổng — mở để xem đủ" : undefined}>{count}</span> : null}
        {state === "loading" ? <RefreshCw className="size-3.5 shrink-0 animate-spin text-muted-foreground" aria-label="Đang tải" /> : null}
        <button type="button" onClick={onOpen} className="t12m inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-primary outline-none hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-ring">
          Mở <ArrowUpRight className="size-3" aria-hidden />
        </button>
      </div>
      <div className={cn("px-3 py-2.5", state === "loading" && page && "opacity-60")}>
        {state === "failed" ? (
          <p className="t12 text-warn">Chưa tải được — có thể quyền hoặc dữ liệu vừa đổi. Bấm “Mở” để xem.</p>
        ) : !page ? (
          <p className="t12 text-muted-foreground">Đang tải…</p>
        ) : page.kind === "card" ? (
          // server đã bỏ tiêu đề / nút thanh công cụ của trang ở bản gọn — vẽ thẳng
          <CardView page={page} onChanged={changed} />
        ) : page.kind === "table" ? (
          page.rows.length ? (
            <div className="flex flex-col gap-2">
              <KpiRow kpis={page.kpis} inset />
              <HeaderCharts page={page} />
              <DataTable page={page} onOpen={onOpen} />
            </div>
          ) : (
            <p className="t12 text-muted-foreground">{page.empty}</p>
          )
        ) : (
          <p className="t12 text-muted-foreground">Bấm “Mở” để xem chi tiết.</p>
        )}
      </div>
    </div>
  );
}
