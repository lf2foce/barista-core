"use client";

import { ExternalLink, ScanLine, Search } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

/** Bản gốc PDF qua BFF (cookie phiên), trình xem PDF của trình duyệt mở đúng trang (#page=N). Có ``quote`` (bằng chứng của một phát hiện, 0072)
 *  ⇒ hiện câu nguyên văn ở đầu hộp + URL kèm #search= (trình duyệt hỗ trợ thì tô; không thì người đối chiếu bằng câu hiện sẵn). Bản scan ⇒ nói rõ
 *  không tìm được chữ trên ảnh. */
export function PdfDialog({ title, url, filename, quote, scan, onClose }: {
  title: string; url: string; filename?: string; quote?: string; scan?: boolean; onClose: () => void;
}) {
  const shown = quote ? quote.replace(/\s+/g, " ").trim() : "";
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="flex h-[90vh] flex-col sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle className="t16 pr-8">{title}</DialogTitle>
          <DialogDescription className="t12 flex items-center gap-3">
            {filename ? <span className="truncate">{filename}</span> : null}
            <a href={url} target="_blank" rel="noopener" className="inline-flex shrink-0 items-center gap-1 text-primary hover:underline">
              <ExternalLink className="size-3.5" aria-hidden />
              Mở trong tab mới
            </a>
          </DialogDescription>
        </DialogHeader>
        {shown ? (
          <div className="t12 flex items-start gap-2 rounded-lg px-3 py-2"
               style={{ background: `color-mix(in oklab, var(--${scan ? "warn" : "primary"}) 10%, transparent)` }}>
            {scan ? <ScanLine className="mt-0.5 size-3.5 shrink-0" aria-hidden /> : <Search className="mt-0.5 size-3.5 shrink-0" aria-hidden />}
            <span className="select-text">
              {scan ? "Bản scan — không tìm được chữ trên ảnh, đối chiếu bằng mắt: " : "Câu nguyên văn cần đối chiếu (Ctrl / ⌘ F để tìm nếu chưa được tô): "}
              <span className="font-mono">“{shown}”</span>
            </span>
          </div>
        ) : null}
        <iframe src={url} title={title} className="min-h-0 w-full flex-1 rounded-lg border border-border bg-surface2" />
      </DialogContent>
    </Dialog>
  );
}
