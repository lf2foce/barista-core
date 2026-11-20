"use client";

import { Inbox } from "lucide-react";
import type { Page } from "@/lib/types";
import { LoadError, Spinner } from "@/components/primitives";
import { CardView } from "./card";
import { DetailView } from "./detail";
import { DataTable, KpiRow } from "./table";
import { ClampNote } from "@/components/clamp-note";

/** Vẽ một trang theo `kind` (bảng tĩnh / chi tiết / thẻ) kèm trạng thái tải, lỗi, rỗng. Bảng có thao tác thì dùng ScreenView. */
export function PageContent({
  page,
  error,
  errorAt,
  loading,
  onChanged,
  onRetry,
  onClose,
}: {
  page: Page | null;
  error?: string | null;
  errorAt?: number | null;
  onRetry?: () => void;
  loading?: boolean;
  embedded?: boolean;
  onChanged?: () => void;
  onClose?: () => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      {error ? <LoadError message={error} at={errorAt} busy={loading} compact={!!page} onRetry={onRetry} /> : null}
      {!page && loading ? <div className="grid place-items-center py-16"><Spinner className="size-6" /></div> : null}
      {page?.kind === "detail" ? <DetailView page={page} onClose={onClose} onChanged={onChanged} /> : null}
      {page?.kind === "card" ? <CardView page={page} onClose={onClose} onChanged={onChanged} /> : null}
      {page?.kind === "table" ? (
        <>
          <KpiRow kpis={page.kpis} inset />
          {page.note ? <ClampNote text={page.note} /> : null}
          {page.rows.length ? (
            <div className="max-h-[60vh] overflow-auto rounded-xl border border-hairline"><DataTable page={page} /></div>
          ) : (
            <Empty text={page.empty} />
          )}
        </>
      ) : null}
    </div>
  );
}

export function Empty({ text, children }: { text: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center text-muted-foreground">
      <Inbox className="size-8" aria-hidden />
      <p className="t14 max-w-prose">{text}</p>
      {children ? <div className="mt-1 flex flex-wrap justify-center gap-2">{children}</div> : null}
    </div>
  );
}
