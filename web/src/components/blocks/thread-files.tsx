"use client";

import { BookOpen, ExternalLink, FileText, ImageIcon, Link2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { jumpTarget, talkFileUrl } from "@/lib/talk";
import type { Block, CardPage, ListItem, OpenRef } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ErrorLine, Pill, Spinner } from "@/components/primitives";
import { useDesk } from "@/components/desk/desk-context";

const TAB_ICON: Record<string, typeof FileText> = { media: ImageIcon, file: FileText, link: Link2, team: BookOpen };

/**
 * Tab "Tệp" của một trao đổi (0128, như "Ảnh, file, link" của Zalo): trang thẻ do server mô tả (`talk.files`) — các nhóm là `tabs` của trang, dòng là
 * khối danh sách. Web chỉ vẽ: ảnh thành lưới ảnh thu nhỏ (tải qua đường tệp sẵn có, quyền như tin), bấm một dòng ⇒ mở đúng tin trong luồng.
 */
export function ThreadFiles({ files, onJump, onClose, className }: { files: OpenRef; onJump: (messageId: number) => void; onClose?: () => void; className?: string }) {
  const desk = useDesk();
  const [kind, setKind] = useState<string | null>(null);
  const [page, setPage] = useState<CardPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const inputKey = JSON.stringify(files.input);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const p = await api.query(files.query, { ...(JSON.parse(inputKey) as OpenRef["input"]), ...(kind ? { kind } : {}) });
        if (!alive) return;
        if (p.kind !== "card") throw new Error("Không đọc được danh sách tệp");
        setPage(p);
        setError(null);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "Không đọc được danh sách tệp");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [files.query, inputKey, kind]);

  const current = page?.tabs?.current ?? kind ?? "media";
  const open = (item: ListItem) => {
    const to = jumpTarget(item.open);
    if (to) onJump(to);
    else if (item.open) desk.openPage(item.open.query, item.open.input, item.title);
  };

  return (
    <section className={cn("flex min-h-0 flex-col gap-2", className)} aria-label="Tệp trong trao đổi">
      <div className="flex items-center gap-2">
        <h3 className="t16 flex-1">Tệp</h3>
        {loading && page ? <Spinner className="size-4" /> : null}
        {onClose ? (
          <button type="button" onClick={onClose} aria-label="Đóng tệp" title="Đóng" className="grid size-8 place-items-center rounded-lg outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
            <X className="size-4" />
          </button>
        ) : null}
      </div>
      {page?.tabs ? (
        <div role="tablist" aria-label="Loại tệp" className="flex gap-1 overflow-x-auto rounded-lg bg-foreground/[0.05] p-0.5">
          {page.tabs.items.map((t) => {
            const Icon = TAB_ICON[t.key] ?? FileText;
            return (
              <button key={t.key} type="button" role="tab" aria-selected={current === t.key} onClick={() => {
                        if (t.key === current) return;
                        setLoading(true);
                        setKind(t.key);
                      }}
                      className={cn("t12m inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2.5 py-1.5 outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                    current === t.key ? "bg-surface shadow-sm" : "text-muted-foreground hover:text-foreground")}>
                <Icon className="size-3.5" aria-hidden /> {t.title}
              </button>
            );
          })}
        </div>
      ) : null}
      {error ? <ErrorLine>{error}</ErrorLine> : null}
      {!page && loading ? <div className="grid place-items-center py-10"><Spinner className="size-6" /></div> : null}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto" role="tabpanel">
        {page?.blocks.map((b, i) => <FilesBlock key={`${current}-${i}`} block={b} media={current === "media"} onOpen={open} />)}
      </div>
    </section>
  );
}

function FilesBlock({ block, media, onOpen }: { block: Block; media: boolean; onOpen: (item: ListItem) => void }) {
  if (block.type === "callout") {
    return (
      <div className="rounded-xl bg-surface2 px-3 py-2.5">
        <p className="t14m">{block.title}</p>
        {block.text ? <p className="t12 text-muted-foreground">{block.text}</p> : null}
      </div>
    );
  }
  if (block.type !== "list") return null;
  return (
    <div className="flex flex-col gap-1.5">
      {block.title ? <p className="t12m text-muted-foreground">{block.title}</p> : null}
      {media ? (
        <ul className="grid grid-cols-3 gap-1.5">
          {block.items.map((it, i) => (
            <li key={it.file?.id ?? i}>
              <button type="button" onClick={() => onOpen(it)} title={`${it.title}${it.subtitle ? ` — ${it.subtitle}` : ""}`}
                      className="block aspect-square w-full overflow-hidden rounded-lg border border-border bg-surface2 outline-none focus-visible:ring-2 focus-visible:ring-ring">
                {it.file?.is_image ? (
                  // eslint-disable-next-line @next/next/no-img-element -- ảnh qua BFF có cookie, không qua bộ tối ưu ảnh
                  <img src={talkFileUrl(it.file.id)} alt={it.title} loading="lazy" className="size-full object-cover" />
                ) : (
                  <span className="t12 flex size-full flex-col items-center justify-center gap-1 p-1 text-center text-muted-foreground">
                    <ImageIcon className="size-5" aria-hidden />
                    <span className="line-clamp-2 break-all">{it.title}</span>
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="flex flex-col">
          {block.items.map((it, i) => {
            const url = it.title.startsWith("https://") || it.title.startsWith("http://") ? it.title : null;
            const Icon = url ? Link2 : it.file ? FileText : BookOpen;
            return (
              <li key={i} className="flex items-center gap-1 border-b border-hairline last:border-b-0">
                <button type="button" onClick={() => onOpen(it)} className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-1.5 py-2 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
                  <Icon className="size-4 shrink-0 text-primary" aria-hidden />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="t14m truncate">{it.title}</span>
                    {it.subtitle ? <span className="t12 truncate text-muted-foreground">{it.subtitle}</span> : null}
                  </span>
                  {it.badge ? <Pill text={it.badge} tone={it.tone === "accent" ? "blue" : "gray"} className="shrink-0" /> : null}
                </button>
                {url ? (
                  <a href={url} target="_blank" rel="noreferrer noopener" title="Mở link (thẻ mới)" aria-label="Mở link trong thẻ mới"
                     className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-accent">
                    <ExternalLink className="size-3.5" />
                  </a>
                ) : it.file ? (
                  <a href={talkFileUrl(it.file.id)} target="_blank" rel="noreferrer" title="Tải tệp" aria-label={`Tải ${it.title}`}
                     className="grid size-8 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-accent">
                    <ExternalLink className="size-3.5" />
                  </a>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
