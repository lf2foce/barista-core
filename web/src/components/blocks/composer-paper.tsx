"use client";

import { useEffect, useState } from "react";
import {
  Check, CircleAlert, Copy, Download, FileDown, FileSpreadsheet, Link2, MoreHorizontal, RefreshCw, RotateCcw, Sparkles, X,
} from "lucide-react";
import { api } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { splitPlaceholders, type LiveTurn } from "@/lib/composer";
import { cell, datetime, kpi as kpiValue } from "@/lib/format";
import { documentFileUrl } from "@/lib/links";
import type { ComposerData, ComposerItem, ComposerSection, Option } from "@/lib/types";
import { actionClass } from "@/components/primitives";
import { useDesk } from "@/components/desk/desk-context";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

const ORIGIN: Record<string, string> = { ai: "AI viết", human: "Người sửa", data: "Số liệu", kb: "Tài liệu", template: "Mẫu gói" };
const VIA: Record<string, string> = { ai: "AI", agent: "Trợ lý", human: "Bạn" };
const SELECT = "t14 h-9 w-full rounded-lg border border-border bg-surface px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60";

// Bảng màu giấy in cố định kiểu ReportDocument / ReportExhibit (phenau_v3): sáng hay tối đều đọc đúng mặt giấy thành phẩm.
const INK = "#0f172a";
const BODY = "#262522";
const MUTED = "#575550";
const RULE = "#dfdcd5";
const DARK_BAND = "color-mix(in oklab, var(--brand) 82%, #000)";
const trackStyle = (cols: number, min = "10rem"): React.CSSProperties =>
  ({ gridTemplateColumns: `repeat(auto-fit, minmax(max(${min}, calc(100% / ${cols} - 1rem)), 1fr))` });

function Prose({ text }: { text: string }) {
  return (
    <>
      {splitPlaceholders(text).map((p, i) =>
        p.hole ? <mark key={i} className="rounded bg-[#fef3c7] px-1 py-px text-[#78350f]">{p.text}</mark> : <span key={i}>{p.text}</span>,
      )}
    </>
  );
}

/** Tách khối thành từng slide 16:9 khi loại ấn phẩm là `slides`: mỗi tiêu đề H1 bắt đầu một trang slide mới. */
function splitSlides(blocks: ComposerItem[]): ComposerItem[][] {
  const slides: ComposerItem[][] = [];
  for (const b of blocks) {
    if ((b.type === "heading" && b.level === 1) || !slides.length) slides.push([b]);
    else slides[slides.length - 1].push(b);
  }
  return slides;
}

/** Artifact Canvas kiểu Data Analyst (phenau_v3):
 *  - Thanh đầu 56px tối giản (loại · tiêu đề · vN · xuất tệp · chia sẻ · ⋯ · đóng)
 *  - Mặt giấy trắng chuẩn in ở giữa: không lặp tiêu đề khi đã có khối bìa, dải số phẳng + bảng kẻ mảnh kiểu ReportExhibit,
 *    chế độ Slide tách từng khung 16:9 riêng có số trang, nút sửa khối chỉ hiện nhẹ khi bấm vào khối. */
export function ComposerPaper({
  doc, live, busy, title, docs, frameActions, onClose,
  onRevise, onEdit, onRemove, onRefresh, onRestore, onExport, onDownload, onLayout, onSlot, onCover, onShare, onUnshare, onDuplicate,
}: {
  doc: ComposerData; live: LiveTurn | null; busy: string | null; title: string;
  docs: React.ReactNode; frameActions: React.ReactNode; onClose: () => void;
  onRevise: (block: string, instruction: string, mode: "rewrite" | "insert_after") => void;
  onEdit: (block: string, text: string) => void;
  onRemove: (block: string) => void;
  onRefresh: () => void;
  onRestore: (version: number) => void;
  onExport: (format: string) => void;
  onDownload: (id: string) => void;
  onLayout: (template: string) => void;
  onSlot: (block: string, slot: string) => void;
  onCover: (block: string) => void;
  onShare: (recipient: string, days: number) => Promise<string | null>;
  onUnshare: (id: string) => void;
  onDuplicate: (leadId: string) => Promise<void>;
}) {
  const locked = !!busy || !!live;
  const blocks = live?.blocks ?? doc.blocks;
  const ready = doc.blocks.length > 0 && !live?.blocks;
  const brandHex = `#${doc.brand.color || "1F4E79"}`;
  const activeShares = doc.shares.filter((x) => x.active).length;
  const hasLayout = !live?.blocks && !doc.layout.html && !!doc.layout.template && doc.layout.sections.length > 0;
  const hasCover = hasLayout && doc.layout.sections.some((s) => s.kind === "cover");
  const isSlides = doc.kind === "slides" && !hasLayout && !doc.layout.html;
  const metaLine = [
    doc.version ? `Phiên bản ${doc.version}` : "",
    doc.stats.data ? `${doc.stats.data} bảng số liệu chốt` : "",
    doc.subject.label ? doc.subject.label : "",
    doc.customer.label ? `Dành cho ${doc.customer.label}` : "",
    doc.status === "stopped" ? "Đã dừng giữa chừng" : "",
  ].filter(Boolean).join(" · ");

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-[#f3f2ee] dark:bg-canvas" style={{ "--brand": brandHex } as React.CSSProperties}>
      {/* Thanh đầu Artifact 56px */}
      <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-hairline bg-surface px-3 @3xl:px-4">
        <div className="flex min-w-0 items-center gap-2">
          {doc.kind_label ? (
            <span className="t12m shrink-0 rounded-md border border-hairline bg-surface2 px-2 py-0.5 text-muted-foreground">
              {doc.kind_label}
            </span>
          ) : null}
          <h2 className="t14m truncate">{title}</h2>
          {doc.version > 0 ? (
            <VersionsPopover doc={doc} locked={locked} onRestore={onRestore} onDownload={onDownload} />
          ) : null}
          {live ? (
            <span className="t12 shrink-0 text-primary">đang viết…</span>
          ) : busy ? (
            <span className="t12 flex shrink-0 items-center gap-1.5 text-muted-foreground" role="status">
              <span className="block size-3 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary" aria-hidden />
              {busy}
            </span>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {ready ? (
            <>
              {doc.formats.map((f, i) => (
                <button
                  key={f.key}
                  type="button"
                  disabled={locked || doc.stats.flagged > 0}
                  onClick={() => onExport(f.key)}
                  title={doc.stats.flagged ? "Còn khối có số không khớp số liệu — sửa hoặc xoá trước khi xuất" : `Xuất ${f.label}`}
                  className={cn(actionClass(i === 0 ? "primary" : "default"), "h-8 px-2.5 text-[12px]")}
                >
                  <FileDown className="size-3.5" aria-hidden />
                  <span className="@max-xl:sr-only">{f.label}</span>
                </button>
              ))}
              <SharePopover doc={doc} locked={locked} activeCount={activeShares} onShare={onShare} onUnshare={onUnshare} />
              <MoreMenu doc={doc} locked={locked} docs={docs} onRefresh={onRefresh} onLayout={onLayout} onDuplicate={onDuplicate} />
            </>
          ) : (
            docs
          )}
          <span className="contents @3xl:hidden">{frameActions}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Đóng bản xem"
            title="Đóng bản xem (bản vẫn được lưu)"
            className="hidden size-8 shrink-0 place-items-center rounded-md text-muted-foreground outline-none hover:bg-foreground/5 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring @3xl:grid"
          >
            <X className="size-4" />
          </button>
        </div>
      </header>

      {/* Vùng trang thành phẩm */}
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-5 @3xl:px-8 @3xl:py-8">
        {(!live || live.blocks) && !live?.blocks && doc.layout.html ? (
          <div className="mx-auto w-full max-w-[960px] overflow-hidden rounded-sm border border-black/10 bg-white">
            <iframe
              title="Xem trước ấn phẩm"
              srcDoc={doc.layout.html}
              sandbox=""
              className="block h-[calc(100vh-9.5rem)] min-h-[560px] w-full bg-white"
            />
          </div>
        ) : isSlides && (!live || live.blocks) && blocks.length > 0 ? (
          /* Chế độ trình chiếu (DeckViewer kiểu phenau_v3): từng trang slide 16:9 sắc nét */
          <div className="mx-auto flex w-full max-w-[880px] flex-col gap-6">
            {ready && (doc.stats.flagged > 0 || doc.stats.placeholders > 0) ? <DataWarning doc={doc} /> : null}
            {splitSlides(blocks).map((slideBlocks, idx, all) => (
              <article
                key={slideBlocks[0]?.id ?? idx}
                aria-label={`Trang slide ${idx + 1}`}
                className="relative flex min-h-[420px] flex-col justify-between overflow-hidden rounded-sm border border-black/10 bg-white px-7 pb-6 pt-0 @3xl:px-12"
                style={{ color: BODY, fontFamily: doc.brand.font || undefined }}
              >
                <div className="-mx-7 h-1.5 @3xl:-mx-12" style={{ background: brandHex }} />
                <div className="flex flex-1 flex-col justify-center gap-2 py-7">
                  {idx === 0 ? (
                    <div className="mb-2 flex items-center justify-between gap-4">
                      <p className="text-[12px] font-semibold tracking-[0.08em]" style={{ color: brandHex }}>
                        {doc.kind_label || "Slide trình bày"}{doc.subject.label ? ` · ${doc.subject.label}` : ""}
                      </p>
                      {doc.brand.logo ? (
                        // eslint-disable-next-line @next/next/no-img-element -- logo nhỏ server gửi dạng data URL
                        <img src={doc.brand.logo} alt="Logo công ty" className="h-8 w-auto shrink-0 object-contain" />
                      ) : null}
                    </div>
                  ) : null}
                  {slideBlocks.map((b) => (
                    <BlockRow
                      key={b.id} b={b} color={brandHex} busy={locked || !ready} kind="plain"
                      onRevise={(instruction, mode) => onRevise(b.id, instruction, mode)}
                      onEdit={(text) => onEdit(b.id, text)}
                      onRemove={() => onRemove(b.id)}
                    />
                  ))}
                </div>
                <footer className="flex items-center justify-between border-t pt-3 text-[11px]" style={{ borderColor: RULE, color: MUTED }}>
                  <span className="truncate">{doc.brand.footer || title}</span>
                  <span className="tnum shrink-0 font-medium">{String(idx + 1).padStart(2, "0")} / {String(all.length).padStart(2, "0")}</span>
                </footer>
              </article>
            ))}
          </div>
        ) : (
          /* Tờ giấy trắng phẳng kiểu ReportDocument (phenau_v3) */
          <article
            aria-label="Ấn phẩm"
            className="mx-auto w-full max-w-[820px] overflow-hidden rounded-sm border border-black/10 bg-white px-6 pb-12 @3xl:px-12"
            style={{ color: BODY, fontFamily: doc.brand.font || undefined }}
          >
            {/* Dải màu đầu trang — bỏ qua nếu phần cover đã tràn lề trên */}
            {!hasCover ? <div className="-mx-6 h-1.5 @3xl:-mx-12" style={{ background: brandHex }} /> : null}

            {/* Đầu tài liệu — chỉ vẽ khi bố cục KHÔNG có khối bìa cover (tránh lặp tiêu đề 2 lần!) */}
            {!hasCover ? (
              <header className="flex items-start justify-between gap-4 pb-6 pt-7" style={{ borderBottom: `1px solid ${RULE}` }}>
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] font-semibold tracking-[0.08em]" style={{ color: brandHex }}>
                    {doc.kind_label || "Ấn phẩm"}
                  </p>
                  <h1 className="mt-2 text-[24px] font-semibold leading-[1.2] tracking-[-0.02em] @3xl:text-[32px]" style={{ color: INK }}>
                    {title}
                  </h1>
                  {metaLine ? <p className="mt-2.5 text-[13px]" style={{ color: MUTED }}>{metaLine}</p> : null}
                </div>
                {doc.brand.logo ? (
                  // eslint-disable-next-line @next/next/no-img-element -- logo nhỏ server gửi dạng data URL
                  <img src={doc.brand.logo} alt="Logo công ty" className="h-10 w-auto shrink-0 object-contain" />
                ) : null}
              </header>
            ) : null}

            {ready && (doc.stats.flagged > 0 || doc.stats.placeholders > 0) ? <DataWarning doc={doc} /> : null}

            {live && !live.blocks ? (
              <div className="flex flex-col gap-5 py-7" aria-label="AI đang viết">
                <div className="flex flex-col gap-2.5">
                  <div className="h-5 w-2/5 animate-pulse rounded bg-black/[0.07]" />
                  <div className="h-3.5 w-full animate-pulse rounded bg-black/[0.05]" />
                  <div className="h-3.5 w-11/12 animate-pulse rounded bg-black/[0.05]" />
                  <div className="h-3.5 w-4/5 animate-pulse rounded bg-black/[0.05]" />
                </div>
                {live.data.map((b) => <DataView key={b.id} b={b} color={brandHex} />)}
              </div>
            ) : null}

            {!live && !blocks.length ? (
              <div className="my-10 grid place-items-center rounded-lg border border-dashed px-4 py-14 text-center" style={{ borderColor: RULE }}>
                <p className="max-w-sm text-[14px]" style={{ color: MUTED }}>
                  {doc.status === "failed"
                    ? "Chưa có nội dung — xem lời báo bên khung trò chuyện rồi bấm “Thử lại”."
                    : "Nội dung ấn phẩm sẽ hiện trên trang giấy này ngay khi AI lấy xong số liệu."}
                </p>
              </div>
            ) : null}

            {(!live || live.blocks) && blocks.length > 0 ? (
              <div className={cn("flex flex-col gap-2", hasCover ? "pt-0" : "pt-6")}>
                {hasLayout ? (
                  <LayoutBody
                    doc={doc} title={title} metaLine={metaLine} color={brandHex} busy={locked || !ready}
                    onRevise={onRevise} onEdit={onEdit} onRemove={onRemove} onSlot={onSlot} onCover={onCover}
                  />
                ) : (
                  blocks.map((b) => (
                    <BlockRow
                      key={b.id} b={b} color={brandHex} busy={locked || !ready} kind={doc.kind}
                      onRevise={(instruction, mode) => onRevise(b.id, instruction, mode)}
                      onEdit={(text) => onEdit(b.id, text)}
                      onRemove={() => onRemove(b.id)}
                    />
                  ))
                )}
              </div>
            ) : null}

            {doc.brand.footer ? (
              <footer className="mt-10 pt-4 text-[12px] leading-5" style={{ borderTop: `1px solid ${INK}`, color: MUTED }}>
                {doc.brand.footer}
              </footer>
            ) : null}
          </article>
        )}
      </div>
    </div>
  );
}

function DataWarning({ doc }: { doc: ComposerData }) {
  return (
    <aside className="my-5 px-4 py-3 text-[13px] leading-6" style={{ borderLeft: "3px solid #c98500", background: "#fdf8ec", color: "#5c4200" }}>
      <p className="font-semibold">Cần rà trước khi xuất / chia sẻ</p>
      <p className="mt-0.5">
        {[
          doc.stats.flagged ? `${doc.stats.flagged} khối có số chưa khớp bảng số liệu (chặn xuất tới khi sửa)` : "",
          doc.stats.placeholders ? `${doc.stats.placeholders} chỗ “[cần số liệu]” đang để trống` : "",
        ].filter(Boolean).join(" · ")}
      </p>
    </aside>
  );
}

// ── Popover gọn cho Thanh đầu (Phiên bản · Chia sẻ · Menu ⋯) ──

function VersionsPopover({ doc, locked, onRestore, onDownload }: {
  doc: ComposerData; locked: boolean; onRestore: (v: number) => void; onDownload: (id: string) => void;
}) {
  return (
    <Popover>
      <PopoverTrigger
        className="t12 tnum shrink-0 rounded-md px-1.5 py-0.5 text-muted-foreground outline-none hover:bg-foreground/5 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        title="Xem lịch sử phiên bản và tệp đã xuất"
      >
        v{doc.version}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(26rem,calc(100vw-1.5rem))] gap-3 p-3">
        <section className="flex flex-col gap-1">
          <h3 className="t12m text-muted-foreground">Lịch sử phiên bản</h3>
          <ul className="flex max-h-52 flex-col gap-0.5 overflow-y-auto">
            {doc.versions.map((v) => (
              <li key={v.version} className="t12 flex items-start gap-2 rounded-lg px-2 py-1.5 hover:bg-foreground/5">
                <span className="tnum shrink-0 text-muted-foreground">v{v.version}</span>
                <span className="min-w-0 flex-1">{v.note} <span className="text-muted-foreground">· {VIA[v.via] ?? v.via}</span></span>
                {v.version !== doc.version ? (
                  <button type="button" title="Quay lại bản này" aria-label={`Quay lại bản ${v.version}`} disabled={locked} onClick={() => onRestore(v.version)}
                          className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground">
                    <RotateCcw className="size-3.5" aria-hidden />
                  </button>
                ) : <span className="t12m shrink-0 text-primary">đang xem</span>}
              </li>
            ))}
          </ul>
        </section>
        {doc.exports.length ? (
          <section className="flex flex-col gap-1 border-t border-hairline pt-2">
            <h3 className="t12m text-muted-foreground">Tệp đã xuất</h3>
            <ul className="flex max-h-36 flex-col gap-0.5 overflow-y-auto">
              {doc.exports.map((e) => (
                <li key={e.id}>
                  <button type="button" onClick={() => onDownload(e.id)} className="t12 flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left hover:bg-foreground/5">
                    <Download className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 flex-1 truncate">{e.filename}</span>
                    <span className="tnum text-muted-foreground">v{e.version}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function SharePopover({ doc, locked, activeCount, onShare, onUnshare }: {
  doc: ComposerData; locked: boolean; activeCount: number;
  onShare: (recipient: string, days: number) => Promise<string | null>; onUnshare: (id: string) => void;
}) {
  const [recipient, setRecipient] = useState("");
  const [days, setDays] = useState(30);
  const [url, setUrl] = useState("");
  const [copied, setCopied] = useState(false);
  const blocked = doc.stats.flagged > 0 || doc.stats.placeholders > 0;
  return (
    <Popover>
      <PopoverTrigger
        className={cn(actionClass("default"), "h-8 px-2.5 text-[12px]")}
        title="Gửi khách link xem trang web của bản này (có hạn, thu hồi được, đếm lượt xem)"
      >
        <Link2 className="size-3.5" aria-hidden />
        <span className="@max-xl:sr-only">Chia sẻ</span>
        {activeCount > 0 ? <span className="t12m rounded-full bg-ok/15 px-1.5 text-ok">{activeCount}</span> : null}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(24rem,calc(100vw-1.5rem))] gap-3 p-3">
        <p className="t12 text-muted-foreground">
          Khách mở link xem trang web chụp đúng bản này, không cần đăng nhập.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="t12 flex min-w-[9rem] flex-1 flex-col gap-1">Gửi cho
            <input value={recipient} maxLength={120} onChange={(e) => setRecipient(e.target.value)} placeholder="Vd: Chị Lan"
                   className="t14 h-8 rounded-lg border border-border bg-surface px-2 outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          </label>
          <label className="t12 flex flex-col gap-1">Hạn
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="t14 h-8 rounded-lg border border-border bg-surface px-2 outline-none">
              {[7, 14, 30, 60].map((d) => <option key={d} value={d}>{d} ngày</option>)}
            </select>
          </label>
          <button type="button" disabled={locked || blocked} className={cn(actionClass("primary"), "h-8")}
                  onClick={() => { void onShare(recipient.trim(), days).then((u) => { if (u) { setUrl(u); setCopied(false); } }); }}>
            Tạo link
          </button>
        </div>
        {blocked ? <p className="t12 text-bad" role="note">Còn số lệch hoặc chỗ “[cần số liệu]” — sửa trước khi gửi ra ngoài.</p> : null}
        {url ? (
          <div className="flex flex-col gap-1 rounded-lg bg-surface2 p-2">
            <div className="flex items-center gap-1.5">
              <input readOnly value={url} aria-label="Link chia sẻ" onFocus={(e) => e.currentTarget.select()}
                     className="t12 h-8 min-w-0 flex-1 rounded-md border border-border bg-surface px-2 outline-none" />
              <button type="button" className={cn(actionClass("default"), "h-8 px-2")}
                      onClick={() => { void navigator.clipboard?.writeText(url).then(() => setCopied(true)); }}>
                {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
              </button>
            </div>
            <p className="t12 text-muted-foreground" role="status">Link chỉ hiện lần này — hãy chép ngay.</p>
          </div>
        ) : null}
        {doc.shares.length ? (
          <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto border-t border-hairline pt-2" aria-label="Các lần đã chia sẻ">
            {doc.shares.map((x) => (
              <li key={x.id} className="t12 flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-foreground/5">
                <span className="min-w-0 flex-1 truncate">{x.recipient || "(không tên)"} <span className="text-muted-foreground">· v{x.version} · {datetime(x.at).slice(0, 10)}</span></span>
                <span className="tnum shrink-0 text-muted-foreground">{x.views} xem</span>
                {x.active ? (
                  <button type="button" disabled={locked} onClick={() => onUnshare(x.id)} className="shrink-0 rounded px-1.5 py-0.5 text-bad hover:bg-bad/10">Thu hồi</button>
                ) : <span className="shrink-0 text-muted-foreground">đóng</span>}
              </li>
            ))}
          </ul>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function MoreMenu({ doc, locked, docs, onRefresh, onLayout, onDuplicate }: {
  doc: ComposerData; locked: boolean; docs: React.ReactNode;
  onRefresh: () => void; onLayout: (template: string) => void; onDuplicate: (leadId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [customers, setCustomers] = useState<Option[] | null>(null);
  const [lead, setLead] = useState("");
  const custQuery = doc.commands.customers ?? "";
  useEffect(() => {
    if (!open || customers || !custQuery || !doc.commands.duplicate) return;
    api.options(custQuery).then(setCustomers, () => setCustomers([]));
  }, [open, customers, custQuery, doc.commands.duplicate]);
  const choices = (customers ?? []).filter((o) => o.value !== doc.customer.id);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className={cn(actionClass("default"), "size-8 p-0")} aria-label="Tùy chọn ấn phẩm" title="Tùy chọn ấn phẩm">
        <MoreHorizontal className="size-4" aria-hidden />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(21rem,calc(100vw-1.5rem))] gap-3 p-3">
        <div className="flex items-center justify-between gap-2">
          <button type="button" disabled={locked} onClick={() => { setOpen(false); onRefresh(); }}
                  className={cn(actionClass("default"), "h-8 flex-1 justify-start text-[12px]")}>
            <RefreshCw className="size-3.5" aria-hidden /> Cập nhật số liệu mới nhất
          </button>
          {docs}
        </div>
        {doc.layout.templates.length ? (
          <label className="t12 flex flex-col gap-1 text-muted-foreground">
            Trang mẫu trình bày
            <select value={doc.layout.template} disabled={locked} onChange={(e) => { onLayout(e.target.value); setOpen(false); }}
                    aria-label="Trang mẫu của ấn phẩm" className={SELECT}>
              {doc.layout.template ? null : <option value="">Bố cục mặc định</option>}
              {doc.layout.templates.map((t) => <option key={t.key} value={t.key} title={t.about}>{t.label}</option>)}
            </select>
          </label>
        ) : null}
        {doc.commands.duplicate && doc.commands.customers ? (
          <div className="flex flex-col gap-2 border-t border-hairline pt-2.5">
            <label className="t12 flex flex-col gap-1 text-muted-foreground">
              Làm bản riêng cho khách khác
              <select value={lead} onChange={(e) => setLead(e.target.value)} disabled={customers === null} className={SELECT}>
                <option value="">{customers === null ? "Đang tải…" : choices.length ? "Chọn khách" : "Chưa có khách nào"}</option>
                {choices.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <button type="button" disabled={!lead || locked} className={cn(actionClass("primary"), "h-8 self-start text-[12px]")}
                    onClick={() => { setOpen(false); void onDuplicate(lead).then(() => setLead("")); }}>
              <Copy className="size-3.5" aria-hidden /> {doc.from_record ? "Dựng bản cho khách này" : "Chép thành bản mới"}
            </button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

// ── Bố cục trang giấy & từng khối nội dung (editorial kiểu ReportDocument / ReportExhibit) ──

function Tiles({ items, color }: { items: { label: string; value: string }[]; color: string }) {
  return (
    <ul className="my-3 grid gap-5 border-y py-4" style={{ ...trackStyle(Math.min(items.length || 1, 4), "8.5rem"), borderColor: RULE }}>
      {items.map((it, i) => (
        <li key={i} className="flex min-w-0 flex-col gap-1">
          <span className={cn("tnum select-text font-semibold leading-none tracking-[-0.02em]", it.value.length > 14 ? "text-[16px] break-words" : "text-[26px]")} style={{ color }}>
            {it.value}
          </span>
          <span className="text-[12px]" style={{ color: MUTED }}>{it.label}</span>
        </li>
      ))}
    </ul>
  );
}

function LayoutBody({ doc, title, metaLine, color, busy, onRevise, onEdit, onRemove, onSlot, onCover }: {
  doc: ComposerData; title: string; metaLine: string; color: string; busy: boolean;
  onRevise: (block: string, instruction: string, mode: "rewrite" | "insert_after") => void; onEdit: (block: string, text: string) => void;
  onRemove: (block: string) => void; onSlot: (block: string, slot: string) => void; onCover: (block: string) => void;
}) {
  const by = new Map(doc.blocks.map((b) => [b.id, b]));
  const row = (id: string, extra: { view?: React.ReactNode; imgClass?: string; onDark?: boolean } = {}) => {
    const b = by.get(id);
    if (!b) return null;
    return (
      <BlockRow key={id} b={b} color={color} busy={busy} kind={doc.kind} slots={doc.layout.slots} onSlot={(slot) => onSlot(id, slot)} onCover={() => onCover(id)}
                onRevise={(instruction, mode) => onRevise(id, instruction, mode)} onEdit={(text) => onEdit(id, text)} onRemove={() => onRemove(id)} {...extra} />
    );
  };
  const part = (s: ComposerSection, i: number): React.ReactNode => {
    switch (s.kind) {
      case "cover": {
        // Bỏ khối heading trùng chính tiêu đề chính để không in lặp 2 dòng giống nhau trong bìa
        const norm = (x: string) => x.trim().toLowerCase();
        const coverText = s.text.filter((id) => {
          const b = by.get(id);
          return !(b && b.type === "heading" && norm(b.text) === norm(title));
        });
        return (
          <section key={i} aria-label="Bìa" className="-mx-6 mb-4 overflow-hidden @3xl:-mx-12">
            {s.hero ? row(s.hero, { imgClass: "max-h-80 w-full object-cover" }) : null}
            <div className="flex flex-col gap-2 px-6 py-7 text-white @3xl:px-12 @3xl:py-9" style={{ background: DARK_BAND }}>
              <div className="flex items-center justify-between gap-4">
                <p className="text-[12px] font-semibold tracking-[0.08em] text-white/75">
                  {doc.kind_label || "Ấn phẩm"}
                </p>
                {doc.brand.logo ? (
                  // eslint-disable-next-line @next/next/no-img-element -- logo công ty dạng data URL
                  <img src={doc.brand.logo} alt="Logo công ty" className="h-8 w-auto shrink-0 rounded bg-white/95 px-2 py-1 object-contain" />
                ) : null}
              </div>
              <h1 className="text-[26px] font-semibold leading-[1.2] tracking-[-0.02em] @3xl:text-[32px]">{title}</h1>
              {coverText.map((id) => row(id, { onDark: true }))}
              {metaLine ? <p className="mt-2 text-[12px] text-white/70">{metaLine}</p> : null}
            </div>
          </section>
        );
      }
      case "flow":
        return <div key={i} className="flex flex-col gap-1.5">{s.ids.map((id) => row(id))}</div>;
      case "columns":
        return (
          <div key={i} className="my-2 grid gap-6" style={trackStyle(s.cols, "12rem")}>
            {s.columns.map((col, j) => <div key={j} className="flex min-w-0 flex-col gap-1.5">{col.map((id) => row(id))}</div>)}
          </div>
        );
      case "tiles":
        return <div key={i} className="flex flex-col gap-1">{s.ids.map((id) => row(id, { view: <Tiles items={s.items.filter((t) => t.block === id)} color={color} /> }))}</div>;
      case "grid":
        return (
          <div key={i} className="my-2 grid gap-3" style={trackStyle(s.cols, "9rem")}>
            {s.ids.map((id) => row(id, { imgClass: "aspect-[4/3] w-full rounded object-cover" }))}
          </div>
        );
      case "footer":
        return (
          <footer key={i} aria-label="Liên hệ" className="mt-6 flex flex-col gap-1 rounded-lg px-6 py-5 text-white" style={{ background: DARK_BAND }}>
            {s.ids.map((id) => row(id, { onDark: true, view: s.lines[id]?.length ? <div className="flex flex-col gap-0.5">{s.lines[id].map((t, j) => <p key={j} className="text-[14px] select-text">{t}</p>)}</div> : undefined }))}
          </footer>
        );
    }
  };
  return <div className="flex flex-col gap-3" data-layout={doc.layout.template}>{doc.layout.sections.map(part)}</div>;
}

/** Khối số liệu kiểu ReportExhibit (phenau_v3): dải số phẳng không hộp be + bảng kẻ mảnh chuẩn tài liệu tư vấn. */
function DataView({ b, color }: { b: ComposerItem; color: string }) {
  const desk = useDesk();
  if (!b.page) return null;
  const page = b.page;
  const src = b.source;
  const cols = page.columns;
  async function excel() {
    if (!src?.query) return;
    try {
      saveBlob(await api.xlsx(src.query, src.input), `${page.title || src.title || "so-lieu"}.xlsx`);
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Xuất Excel lỗi", true);
    }
  }
  return (
    <figure className="my-4 flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-2">
        {page.title ? <figcaption className="text-[15px] font-semibold" style={{ color: INK }}>{page.title}</figcaption> : <span />}
        {src?.query ? (
          <button
            type="button"
            onClick={() => void excel()}
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md border border-black/10 px-2 text-[12px] text-[#575550] opacity-70 transition-opacity hover:bg-black/[0.04] hover:opacity-100"
            title="Tải bảng gốc ra Excel — chạy lại truy vấn bằng quyền của bạn"
          >
            <FileSpreadsheet className="size-3.5" aria-hidden /> Excel
          </button>
        ) : null}
      </div>

      {/* Dải số chính phẳng kiểu KeyFigures của phenau_v3 (không bọc hộp be thô) */}
      {page.kpis.length ? (
        <div
          className="grid gap-y-3 border-y py-3.5"
          style={{
            borderColor: RULE,
            gridTemplateColumns: `repeat(${Math.min(page.kpis.length, 4)}, minmax(0, 1fr))`,
          }}
        >
          {page.kpis.map((k, i) => (
            <div key={i} className={cn("flex min-w-0 flex-col gap-1", i > 0 && "border-l border-[#e5e2dc] pl-4")}>
              <span className="tnum text-[26px] font-semibold leading-none tracking-[-0.02em]" style={{ color }}>
                {kpiValue(k)}
              </span>
              <span className="text-[12px]" style={{ color: MUTED }}>{k.label}</span>
            </div>
          ))}
        </div>
      ) : null}

      {/* Bảng biên tập kẻ mảnh kiểu McKinsey / phenau_v3 */}
      {page.rows.length && cols.length ? (
        <div className="max-h-[380px] overflow-auto">
          <table className="w-full border-collapse text-[13.5px]" style={{ borderTop: `1.5px solid ${INK}`, borderBottom: `1px solid ${INK}` }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${RULE}` }}>
                {cols.map((col) => {
                  const num = col.format === "money" || col.format === "number" || col.format === "percent";
                  return (
                    <th key={col.key} className={cn("py-2 pr-3 font-semibold", num ? "text-right" : "text-left")} style={{ color: MUTED }}>
                      {col.label}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {page.rows.map((r, ri) => (
                <tr key={r.id || ri} style={{ borderTop: ri > 0 ? "1px solid #efede8" : undefined }}>
                  {cols.map((col) => {
                    const num = col.format === "money" || col.format === "number" || col.format === "percent";
                    return (
                      <td key={col.key} className={cn("py-2 pr-3 align-top", num ? "tnum text-right font-medium" : "text-left")} style={{ color: BODY }}>
                        {cell(r.values[col.key], col.format, col.labels)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {b.texts.map((t, i) => (
        <div key={i} className="flex flex-col gap-0.5">
          {t.title ? <h5 className="text-[13px] font-semibold" style={{ color: INK }}>{t.title}</h5> : null}
          <p className="text-[14px] select-text whitespace-pre-line leading-7" style={{ color: BODY }}>{t.text}</p>
        </div>
      ))}

      {src?.title || page.note ? (
        <p className="text-[11.5px] leading-5" style={{ color: MUTED }}>
          {src?.title ? `Nguồn: ${src.title}${src.at ? ` · ${datetime(src.at).slice(0, 16)}` : ""}` : page.note}
        </p>
      ) : null}
    </figure>
  );
}

type Mode = "rewrite" | "insert_after" | "edit" | null;

function BlockRow({ b, color, busy, kind, onRevise, onEdit, onRemove, slots, onSlot, onCover, view, imgClass, onDark }: {
  b: ComposerItem; color: string; busy: boolean; kind: string;
  onRevise: (instruction: string, mode: "rewrite" | "insert_after") => void; onEdit: (text: string) => void; onRemove: () => void;
  slots?: { key: string; label: string }[]; onSlot?: (slot: string) => void; onCover?: () => void; view?: React.ReactNode; imgClass?: string; onDark?: boolean;
}) {
  const [mode, setMode] = useState<Mode>(null);
  const [text, setText] = useState("");
  const [sticky, setSticky] = useState(false);
  if (!["heading", "paragraph", "data", "quote", "image", "placeholder"].includes(b.type)) return null;
  const editable = b.type === "heading" || b.type === "paragraph";
  const close = () => { setMode(null); setSticky(false); };
  const submit = () => {
    if (!text.trim()) return;
    if (mode === "edit") onEdit(text.trim());
    else if (mode === "rewrite" || mode === "insert_after") onRevise(mode === "insert_after" ? `Chèn khối số liệu: ${text.trim()}` : text.trim(), mode);
    close();
  };
  const slideBreak = kind === "slides" && b.type === "heading" && b.level === 1;
  const bar = "h-7 rounded-md px-2 text-[12px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring";
  return (
    <div
      role="group"
      aria-label={`Khối ${ORIGIN[b.origin] ?? ""}`.trim()}
      tabIndex={busy ? -1 : 0}
      data-open={sticky || mode ? "true" : undefined}
      onClick={() => !busy && setSticky(true)}
      onFocus={(e) => { if (!busy && e.target === e.currentTarget) setSticky(true); }}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) close(); }}
      onKeyDown={(e) => { if (e.key === "Escape" && (mode || sticky)) { e.stopPropagation(); close(); e.currentTarget.blur(); } }}
      className={cn(
        "group relative -mx-2 rounded-lg px-2 py-1.5 outline-none transition-colors",
        onDark ? "hover:bg-white/10" : "hover:bg-black/[0.025] focus-visible:bg-black/[0.025]",
        b.flags.length && "bg-[#fef2f2] ring-1 ring-[#dc2626]/30",
        slideBreak && "mt-5 border-t border-dashed border-[#d6d3cd] pt-5",
      )}
    >
      <div className="min-w-0">
        {mode === "edit" ? (
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={Math.max(2, Math.ceil(b.text.length / 70))}
            autoFocus
            aria-label="Nội dung"
            className="w-full rounded-lg border border-black/20 bg-white px-2.5 py-1.5 text-[15px] leading-7 text-[#1a1917] outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        ) : view ? (
          view
        ) : b.type === "heading" ? (
          <h3
            className={cn("select-text font-semibold leading-snug tracking-[-0.01em]", b.level === 1 ? "mt-4 border-t border-[#efede8] pt-4 text-[20px] first:mt-0 first:border-t-0 first:pt-0" : "mt-2 text-[16px]")}
            style={onDark ? undefined : { color: b.level === 1 ? INK : color }}
          >
            <Prose text={b.text} />
          </h3>
        ) : b.type === "paragraph" ? (
          <p className="select-text text-[15px] leading-[1.78]" style={onDark ? undefined : { color: BODY }}>
            <Prose text={b.text} />
          </p>
        ) : b.type === "quote" ? (
          <blockquote className="my-2 select-text border-l-[3px] pl-4 text-[15px] leading-7" style={{ borderColor: onDark ? "rgba(255,255,255,0.6)" : color }}>
            “{b.text}”
            {b.cite ? <span className="mt-1 block text-[12px]" style={{ color: onDark ? "rgba(255,255,255,0.75)" : MUTED }}>— {b.cite}</span> : null}
          </blockquote>
        ) : b.type === "placeholder" ? (
          <div className="my-2 rounded-lg border border-dashed border-black/15 bg-[#f8f7f4] px-4 py-5 text-center">
            <p className="text-[14px] font-medium" style={{ color: INK }}>{b.text}</p>
            <p className="mt-1 text-[12px]" style={{ color: MUTED }}>{b.caption}</p>
          </div>
        ) : b.type === "image" ? (
          b.thumb || (b.document_id && !b.derived) ? (
            <figure className="relative my-2 flex flex-col gap-1.5">
              {/* eslint-disable-next-line @next/next/no-img-element -- ảnh trong kho tài liệu của công ty qua BFF */}
              <img src={b.thumb || documentFileUrl(b.document_id, null)} alt={b.caption || b.filename || "Hình minh hoạ"} loading="lazy" className={imgClass ?? "max-h-80 w-full rounded object-cover"} />
              {b.illustration ? <span className="absolute bottom-2 right-2 rounded bg-black/65 px-2 py-0.5 text-[11px] text-white">{b.illustration}</span> : null}
              {b.caption ? <figcaption className="text-[12px]" style={{ color: MUTED }}>{b.caption}</figcaption> : null}
            </figure>
          ) : <p className="rounded bg-[#f8f7f4] px-3 py-2 text-[12px]" style={{ color: MUTED }}>Ảnh: {b.filename}{b.caption ? ` — ${b.caption}` : ""}</p>
        ) : <DataView b={b} color={color} />}

        {b.flags.map((f) => (
          <p key={f} className="mt-1.5 flex items-center gap-1.5 text-[12px] text-[#b91c1c]">
            <CircleAlert className="size-3.5 shrink-0" aria-hidden /> {f}
          </p>
        ))}
        {mode === "edit" ? <p className="mt-1 text-[12px]" style={{ color: MUTED }}>Khối sửa tay thành “Người sửa” — số trong đó do bạn chịu trách nhiệm.</p> : null}
      </div>

      {busy ? null : (
        <div
          role="toolbar"
          aria-label="Thao tác với khối"
          className="mt-1.5 hidden flex-wrap items-center justify-between gap-2 border-t border-black/8 pt-1.5 group-data-[open=true]:flex"
        >
          <span className="text-[11px]" style={{ color: onDark ? "rgba(255,255,255,0.75)" : MUTED }}>
            Nguồn: {ORIGIN[b.origin] ?? b.origin}
          </span>
          <div className="flex flex-wrap items-center gap-0.5">
            {mode === "edit" ? (
              <>
                <button type="button" disabled={!text.trim()} onClick={submit} className={cn(bar, "bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50")}>Lưu</button>
                <button type="button" onClick={close} className={cn(bar, "text-[#575550] hover:bg-black/5")}>Huỷ</button>
              </>
            ) : (
              <>
                {(b.type === "placeholder" ? [] : [["rewrite", "Viết lại"], ["insert_after", "Chèn số liệu"], ...(editable ? [["edit", "Sửa tay"]] : [])]).map(([k, label]) => (
                  <button key={k} type="button" aria-pressed={mode === k} onClick={() => { setMode(k as Mode); setText(k === "edit" ? b.text : ""); }}
                          className={cn(bar, mode === k ? "bg-primary text-primary-foreground" : onDark ? "text-white hover:bg-white/15" : "text-[#0f172a] hover:bg-black/5")}>
                    {label}
                  </button>
                ))}
                {b.type === "image" && onCover && b.slot !== "cover" ? (
                  <button type="button" onClick={onCover} className={cn(bar, "text-[#0f172a] hover:bg-black/5")}>Đặt làm bìa</button>
                ) : null}
                {slots?.length && onSlot && b.type !== "placeholder" ? (
                  <select value={b.slot} onChange={(e) => onSlot(e.target.value)} aria-label="Chỗ đứng của khối trong bố cục"
                          className={cn(bar, "bg-[#f8f7f4] text-[#0f172a] hover:bg-black/5")}>
                    {slots.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                  </select>
                ) : null}
                <button type="button" onClick={onRemove} className={cn(bar, "text-[#b91c1c] hover:bg-[#fee2e2]")}>Xoá</button>
              </>
            )}
          </div>
        </div>
      )}

      {mode && mode !== "edit" && !busy ? (
        <div className="mt-2 flex w-full flex-col gap-2 rounded-lg border border-black/15 bg-[#fafaf8] p-2.5 text-[#0f172a]">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={2}
            autoFocus
            aria-label={mode === "insert_after" ? "Cần số liệu gì" : "Muốn viết lại thế nào"}
            placeholder={mode === "insert_after" ? "Vd: bảng hàng còn bán theo nhóm" : "Vd: ngắn gọn hơn, nhấn mạnh điểm nổi bật"}
            className="w-full rounded-md border border-black/15 bg-white px-2.5 py-1.5 text-[14px] text-[#0f172a] outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <div className="flex gap-2">
            <button type="button" disabled={!text.trim()} onClick={submit} className={cn(actionClass("primary"), "h-8 text-[12px] disabled:opacity-50")}>
              <Sparkles className="size-3.5" aria-hidden /> Gửi AI
            </button>
            <button type="button" onClick={close} className={cn(actionClass("default"), "h-8 text-[12px]")}>Huỷ</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
