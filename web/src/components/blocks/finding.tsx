"use client";

import { ExternalLink, FileSearch, ScanLine } from "lucide-react";
import { useState } from "react";
import { PdfDialog } from "@/components/blocks/lazy";
import { actionClass } from "@/components/primitives";
import { cardToneVar } from "@/lib/format";
import { documentFileUrl } from "@/lib/links";
import type { ActionSpec, Evidence } from "@/lib/types";

/** Khối "finding" (kernel/ui.py b_finding): một phát hiện có BẰNG CHỨNG mở được — như FindingBlock.swift. Bấm một dòng bằng chứng: file gốc PDF ⇒
 *  hộp xem PDF đúng trang + câu nguyên văn; tài liệu khác (Word, chữ dán) ⇒ trang tài liệu (chữ) — không hứa "đúng trang trong file". */
export function FindingCard({ tone, title, badge, text, facts, evidence, actions, onAction, onOpenDocument }: {
  tone: string; title: string; badge: string; text: string; facts: string[]; evidence: Evidence[]; actions: ActionSpec[];
  onAction: (a: ActionSpec) => void; onOpenDocument: (documentId: string, page: number | null, title: string) => void;
}) {
  const [open, setOpen] = useState<Evidence | null>(null);
  const c = tone === "muted" ? "var(--muted-foreground)" : cardToneVar(tone);
  return (
    <div className="flex overflow-hidden rounded-xl bg-surface2">
      <span className="w-1 shrink-0" style={{ background: c }} aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-2.5 p-3.5">
        <div className="flex items-baseline gap-2">
          <p className={tone === "muted" ? "t14m text-muted-foreground" : "t14m"}>{title}</p>
          {badge ? (
            <span className="t12m ml-auto shrink-0 rounded-full px-2 py-0.5" style={{ color: c, background: `color-mix(in oklab, ${c} 12%, transparent)` }}>
              {badge}
            </span>
          ) : null}
        </div>
        <p className="t16 select-text">{text}</p>
        {evidence.length ? (
          <ul className="flex flex-col gap-1.5">
            {evidence.map((e, i) => (
              <EvidenceRow key={i} e={e} onOpen={() => (isPdf(e) ? setOpen(e) : e.document_id && onOpenDocument(e.document_id, e.page, e.label))} />
            ))}
          </ul>
        ) : null}
        {facts.map((f, i) => <p key={i} className="t12 text-muted-foreground">{f}</p>)}
        {actions.length ? (
          <div className="flex flex-wrap gap-2">
            {actions.map((a) => (
              <button key={a.command + a.title} type="button" className={actionClass(a.style)} onClick={() => onAction(a)}>{a.title}</button>
            ))}
          </div>
        ) : null}
      </div>
      {open?.document_id ? (
        <PdfDialog title={open.label} url={documentFileUrl(open.document_id, open.page, open.scan ? undefined : open.quote || undefined)}
                   quote={open.quote || undefined} scan={open.scan} onClose={() => setOpen(null)} />
      ) : null}
    </div>
  );
}

const isPdf = (e: Evidence) => e.filename.toLowerCase().endsWith(".pdf");

function EvidenceRow({ e, onOpen }: { e: Evidence; onOpen: () => void }) {
  const body = (
    <>
      {e.scan ? <ScanLine className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden /> : <FileSearch className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />}
      <span className="flex min-w-0 flex-1 flex-col gap-0.5 text-left">
        <span className="t12m flex flex-wrap items-center gap-x-1.5">
          {e.label}
          {e.page ? <span className="t12 text-muted-foreground">trang {e.page}</span> : null}
          {e.scan ? <span className="t12" style={{ color: "var(--warn)" }}>bản scan</span> : null}
        </span>
        <span className={e.quote ? "font-mono text-[13px] leading-snug" : "t12 text-muted-foreground"}>
          {e.quote ? `“${e.quote.replace(/\s+/g, " ").trim()}”` : e.note || "(không có trên chứng từ)"}
        </span>
      </span>
      {e.document_id ? <ExternalLink className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden /> : null}
    </>
  );
  return (
    <li>
      {e.document_id ? (
        <button type="button" onClick={onOpen} title={isPdf(e) ? "Mở chứng từ gốc đúng trang, đối chiếu câu nguyên văn" : "Mở tài liệu trong kho, đối chiếu câu nguyên văn"}
                className="flex w-full items-start gap-2.5 rounded-lg bg-surface p-2.5 hover:bg-surface/70">
          {body}
        </button>
      ) : (
        <div className="flex w-full items-start gap-2.5 rounded-lg bg-surface p-2.5">{body}</div>
      )}
    </li>
  );
}
