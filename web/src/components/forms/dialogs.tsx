"use client";

import { cn } from "@/lib/utils";

import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Pill, ErrorLine } from "@/components/primitives";
import { PageContent } from "@/components/pages/page-content";
import { usePage } from "@/hooks/use-page";
import { saveBlob } from "@/lib/download";
import { menuChanged, resultView } from "@/lib/form";
import type { ImportReport } from "@/lib/types";
import { useDesk, type FormRequest, type ImportRequest, type PageRequest } from "@/components/desk/desk-context";
import { FormBody } from "./form-body";
import { ImportWizard } from "./import-wizard";

/** Mọi hộp thoại đang mở (form lệnh, mở chi tiết, nhập Excel) — dựng một lần ở gốc Desk. */
export function DialogHost() {
  const { forms, pages, imports } = useDesk();
  return (
    <>
      {pages.map((p) => (
        <PageDialog key={p.id} req={p} />
      ))}
      {imports.map((i) => (
        <ImportDialog key={i.id} req={i} />
      ))}
      {forms.map((f) => (
        <FormDialog key={f.id} req={f} />
      ))}
    </>
  );
}

/** Dòng mở bằng MỘT lần bấm (`row_click`): người quen bấm ĐÚP thì lần bấm thứ hai rơi vào hộp thoại vừa mở. Trong nửa giây đầu: bấm vào nền mờ không
 *  đóng (không thì mở rồi đóng ngay), bấm đúp vào nội dung không bôi đen chữ. Esc / nút đóng vẫn đóng ngay. */
function useOpenGuard(close: () => void) {
  const [openedAt] = useState(() => performance.now());
  const fresh = () => performance.now() - openedAt < 500;
  return {
    onOpenChange: (open: boolean, details: { reason?: string }) => {
      if (open || (details.reason === "outside-press" && fresh())) return;
      close();
    },
    onMouseDownCapture: (e: React.MouseEvent) => {
      if (e.detail > 1 && fresh()) e.preventDefault();
    },
  };
}

function FormDialog({ req }: { req: FormRequest }) {
  const { closeForm, show, openPage, reloadManifest } = useDesk();
  const guard = useOpenGuard(() => closeForm(req.id));
  return (
    <Dialog open onOpenChange={guard.onOpenChange}>
      {/* điện thoại (< 640px): tấm trượt từ dưới lên, sát đáy, nút to dễ chạm; máy tính: hộp giữa màn */}
      <DialogContent
        className="flex max-h-[88vh] flex-col sm:max-w-lg max-sm:top-auto max-sm:bottom-0 max-sm:left-0 max-sm:max-h-[92dvh] max-sm:max-w-none max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-b-none max-sm:rounded-t-2xl max-sm:pb-[max(1rem,env(safe-area-inset-bottom))] max-sm:data-open:slide-in-from-bottom-full max-sm:data-open:zoom-in-100 max-sm:data-closed:slide-out-to-bottom-full max-sm:data-closed:zoom-out-100"
        onMouseDownCapture={guard.onMouseDownCapture}
      >
        <div className="mx-auto -mt-1 h-1 w-9 rounded-full bg-foreground/15 sm:hidden" aria-hidden />
        <DialogHeader>
          <DialogTitle className="t20">{req.action.title}</DialogTitle>
          <DialogDescription className="sr-only">Biểu mẫu nhập liệu cho thao tác {req.action.title}</DialogDescription>
        </DialogHeader>
        <FormBody
          action={req.action}
          row={req.row}
          initial={{ ...req.action.preset, ...req.initial }}
          onCancel={() => closeForm(req.id)}
          onOpenSimilar={(id, title) => openPage("kb.document", { document_id: id }, title)}
          onDone={(m, output, spec) => {
            closeForm(req.id);
            show(m);
            req.onDone?.(m, output);
            if (menuChanged(output)) void reloadManifest();   // xoá app / đưa dashboard vào menu… ⇒ menu đổi ngay, tab của màn đã mất tự đóng
            // lệnh có thẻ kết quả (vd soạn bản tư vấn căn ⇒ giấy tờ vừa soạn) ⇒ mở luôn, nổi trên cùng (form có thể mở từ một hộp chi tiết)
            const view = resultView(spec ?? null, output);
            if (view) openPage(view.query, view.input);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

/** Mở chi tiết từ một dòng / nút (OpenSheet.swift): trang chi tiết hoặc thẻ trong hộp thoại lớn. */
function PageDialog({ req }: { req: PageRequest }) {
  const { closePage } = useDesk();
  const { page, error, errorAt, loading, reload } = usePage(req.query, req.input);
  const wide = page?.kind === "card" && page.blocks.some((b) => b.type === "composer");   // Soạn với AI: hai cột cần chỗ
  const guard = useOpenGuard(() => closePage(req.id));
  return (
    <Dialog open onOpenChange={guard.onOpenChange}>
      <DialogContent className={cn("flex max-h-[90vh] min-h-[50vh] flex-col", wide ? "sm:max-w-6xl" : "sm:max-w-3xl")} showCloseButton
        onMouseDownCapture={guard.onMouseDownCapture}>
        <DialogHeader>
          <DialogTitle className="t20 pr-8">{page?.title || req.title || "Chi tiết"}</DialogTitle>
          <DialogDescription className="sr-only">Chi tiết đọc đúng quyền của bạn</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <PageContent page={page} error={error} errorAt={errorAt} loading={loading} embedded onChanged={reload} onRetry={reload} onClose={() => closePage(req.id)} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

const STATUS: Record<string, string> = {
  ok: "Hợp lệ", invalid: "Sai dữ liệu", conflict: "Trùng", denied: "Không có quyền", not_found: "Không thấy", pending_approval: "Chờ duyệt", error: "Lỗi hệ thống",
};

/**
 * Nhập Excel TỰ KHỚP (ADR 0226): thả file → khớp cột (máy đoán, người xác nhận) → xem trước → nhập. Lệnh đọc file theo mẫu riêng của công ty (không tự khớp)
 * rơi về cách cũ bên dưới với đúng file đã chọn.
 */
function ImportDialog({ req }: { req: ImportRequest }) {
  const { closeImport } = useDesk();
  const [legacy, setLegacy] = useState<File | null>(null);
  if (legacy) return <LegacyImportDialog req={req} initialFile={legacy} />;
  return (
    <Dialog open onOpenChange={(o) => !o && closeImport(req.id)}>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="t20">{req.action.import_only ? req.action.title : `Nhập từ Excel — ${req.action.title}`}</DialogTitle>
          <DialogDescription className="t12">Hệ thống tự khớp cột của file vào trường; bạn xem lại rồi nhập. Chạy thử trước, chưa ghi gì.</DialogDescription>
        </DialogHeader>
        <ImportWizard command={req.action.command} title={req.action.title} preset={req.action.preset} onDone={req.onDone} onClose={() => closeImport(req.id)} onLegacy={setLegacy} />
      </DialogContent>
    </Dialog>
  );
}

/** Cách nhập cũ theo mẫu: tải mẫu → chọn .xlsx → CHẠY THỬ (server chạy thật rồi bỏ, báo lỗi từng dòng) → nhập thật. Nhập lại cùng file không nhân đôi. */
function LegacyImportDialog({ req, initialFile }: { req: ImportRequest; initialFile?: File }) {
  const { closeImport } = useDesk();
  const [file, setFile] = useState<File | null>(initialFile ?? null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function run(dryRun: boolean) {
    if (!file) return;
    setBusy(true);
    try {
      const r = await api.importXlsx(req.action.command, file, dryRun);
      setReport(r);
      setError(null);
      if (!dryRun) req.onDone?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không nhập được");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && closeImport(req.id)}>
      <DialogContent className="flex max-h-[90vh] flex-col sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="t20">Nhập từ Excel — {req.action.title}</DialogTitle>
          <DialogDescription className="t12">Chạy thử trước: hệ thống kiểm từng dòng nhưng chưa ghi gì.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="t14m h-8 rounded-lg border border-border bg-surface px-3 hover:bg-accent"
            onClick={async () => {
              try {
                saveBlob(await api.template(req.action.command), `Mẫu nhập - ${req.action.title}.xlsx`);
              } catch (e) {
                setError(e instanceof Error ? e.message : "Không tải được mẫu");
              }
            }}
          >
            Tải mẫu Excel
          </button>
          <button type="button" className="t14m h-8 rounded-lg border border-border bg-surface px-3 hover:bg-accent" onClick={() => input.current?.click()}>
            Chọn file .xlsx / .csv…
          </button>
          <input
            ref={input}
            type="file"
            accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"
            className="sr-only"
            aria-label="Chọn file Excel để nhập"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setReport(null);
            }}
          />
          {file ? <span className="t14m">{file.name}</span> : null}
        </div>
        {error ? <ErrorLine>{error}</ErrorLine> : null}
        {report ? (
          <div className="flex min-h-0 flex-col gap-2">
            <div className="flex items-center gap-2">
              <span className="t16">{report.dry_run ? "Chạy thử (chưa ghi gì)" : "Đã nhập"}</span>
              <Pill text={`${report.ok} hợp lệ`} tone="green" />
              {report.failed > 0 ? <Pill text={`${report.failed} lỗi`} tone="red" /> : null}
            </div>
            {report.unknown_columns.length ? <p className="t12 text-warn">Cột không nhận ra (bỏ qua): {report.unknown_columns.join(", ")}</p> : null}
            <ul className="min-h-0 max-h-60 divide-y divide-border overflow-y-auto rounded-lg border border-border">
              {report.rows
                .filter((l) => l.status !== "ok" || !report.dry_run)
                .map((l) => (
                  <li key={l.line} className="t12 flex items-baseline gap-2 px-3 py-1.5">
                    <span className="t12m w-16 shrink-0">Dòng {l.line}</span>
                    <Pill text={STATUS[l.status] ?? l.status} tone={l.status === "ok" ? "green" : l.status === "pending_approval" ? "orange" : "red"} />
                    <span className="select-text">{l.error ?? ""}</span>
                  </li>
                ))}
            </ul>
          </div>
        ) : null}
        <div className="flex justify-end gap-2">
          <button type="button" className="t14m h-8 rounded-lg border border-border bg-surface px-3 hover:bg-accent" onClick={() => closeImport(req.id)}>
            Đóng
          </button>
          <button type="button" disabled={!file || busy} className="t14m h-8 rounded-lg border border-border bg-surface px-3 hover:bg-accent disabled:opacity-50" onClick={() => void run(true)}>
            Chạy thử
          </button>
          <button
            type="button"
            disabled={busy || report?.dry_run !== true || report.ok === 0}
            className="t14m h-8 rounded-lg bg-primary px-3 text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            onClick={() => void run(false)}
          >
            {report ? `Nhập thật ${report.ok} dòng hợp lệ` : "Nhập thật"}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
