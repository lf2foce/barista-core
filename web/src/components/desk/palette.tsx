"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { foldVietnamese } from "@/lib/search";
import type { ListItem, Page } from "@/lib/types";
import { Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { MessagesSquare } from "lucide-react";
import { preview } from "@/lib/talk";
import { SfIcon } from "@/components/icons";
import { useDesk } from "./desk-context";

// ⌘K: nhảy tới màn (lọc tại chỗ theo manifest) + tìm toàn hệ thống qua truy vấn `core.find` {q} (0087 — mọi gói đang bật tự khai nguồn tìm) (≥ 2 ký tự, gõ xong ~200ms mới gọi).
// Điểm mở rộng: thêm nguồn kết quả = thêm một phần tử vào SEARCH_SOURCES — server trả thẻ có khối `list` nhóm theo tiêu đề, mỗi mục có `open`.
const SEARCH_SOURCES: { query: string; minLength: number }[] = [{ query: "core.find", minLength: 2 }];

type Group = { title: string; items: ListItem[] };

function groupsOf(page: Page): Group[] {
  if (page.kind !== "card") return [];
  return page.blocks.flatMap((b) => (b.type === "list" && b.items.length ? [{ title: b.title, items: b.items }] : []));
}

export function CommandPalette() {
  const desk = useDesk();
  // chỉ dựng phần thân khi mở ⇒ đóng là xoá sạch ô gõ và kết quả
  return (
    <CommandDialog open={desk.paletteOpen} onOpenChange={desk.setPaletteOpen} title="Tìm nhanh" description="Gõ để nhảy tới màn hoặc tìm khách, căn, giao dịch" className="sm:max-w-xl">
      {desk.paletteOpen ? <PaletteBody /> : null}
    </CommandDialog>
  );
}

type Result = { term: string; groups: Group[]; error: string | null };

function PaletteBody() {
  const desk = useDesk();
  const [q, setQ] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const term = q.trim();
  const sources = SEARCH_SOURCES.filter((s) => term.length >= s.minLength);
  const settled = result?.term === term;
  const busy = sources.length > 0 && !settled;
  const groups = sources.length > 0 && settled ? result.groups : [];
  const err = sources.length > 0 && settled ? result.error : null;

  useEffect(() => {
    if (sources.length === 0) return;
    const ctl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const pages = await Promise.all(sources.map((s) => api.query(s.query, { q: term }, ctl.signal)));
        if (!ctl.signal.aborted) setResult({ term, groups: pages.flatMap(groupsOf), error: null });
      } catch (e) {
        if (!ctl.signal.aborted) setResult({ term, groups: [], error: e instanceof Error ? e.message : "Không tìm được" });
      }
    }, 200);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [term]);

  // gõ trống ⇒ màn hay dùng của vai; gõ ⇒ màn khớp tên (bỏ dấu) + trao đổi khớp tên hồ sơ / phòng
  const screens = useMemo(() => {
    const f = foldVietnamese(q.trim());
    if (!f) return desk.quickScreens.map((s) => ({ ...s, ws: "" }));
    const all = [
      ...(desk.manifest?.inbox ? [{ ...desk.manifest.inbox, ws: "Phiếu duyệt" }] : []),
      ...(desk.manifest?.workspaces.flatMap((w) => w.screens.map((s) => ({ ...s, ws: w.title }))) ?? []),
      ...(desk.manifest?.desk?.map((s) => ({ ...s, ws: "Việc" })) ?? []), // màn việc chỉ ở bàn việc (0094) vẫn tìm được
      ...(desk.manifest?.account?.map((s) => ({ ...s, ws: "Tài khoản" })) ?? []), // màn cá nhân ở menu tài khoản vẫn tìm được bằng ⌘K
    ];
    return all.filter((s) => foldVietnamese(s.title).includes(f)).slice(0, 30);
  }, [desk.manifest, desk.quickScreens, q]);
  const talks = useMemo(() => {
    const f = foldVietnamese(q.trim());
    return f ? (desk.talk?.items ?? []).filter((t) => foldVietnamese(t.title).includes(f)).slice(0, 5) : [];
  }, [desk.talk, q]);

  const close = () => desk.setPaletteOpen(false);

  return (
      <Command shouldFilter={false} data-testid="palette">
        <CommandInputBox q={q} setQ={setQ} />
        <CommandList className="max-h-[60vh]">
          {screens.length ? (
            <CommandGroup heading={term ? "Màn" : "Màn hay dùng"}>
              {screens.map((s) => (
                <CommandItem key={s.query + s.key} value={`screen-${s.query}-${s.key}`} onSelect={() => { close(); desk.openScreen(s); }}>
                  <SfIcon name={s.icon} />
                  {s.title}
                  {s.ws ? <span className="t12 ml-auto text-muted-foreground">{s.ws}</span> : null}
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}
          {talks.length ? (
            <CommandGroup heading="Trao đổi">
              {talks.map((t) => (
                <CommandItem key={t.thread_id} value={`talk-${t.thread_id}`} onSelect={() => { close(); desk.openTalk(t); }}>
                  <MessagesSquare />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="t14 truncate">{t.title}</span>
                    <span className="t12 truncate text-muted-foreground">{preview(t)}</span>
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}
          {groups.map((g) => (
            <CommandGroup key={g.title} heading={g.title || "Kết quả"}>
              {g.items.map((it, i) => (
                <CommandItem
                  key={`${g.title}-${i}`}
                  value={`${g.title}-${i}-${it.title}`}
                  onSelect={() => {
                    close();
                    if (it.open) void desk.openTarget(it.open.query, it.open.input, it.title);
                  }}
                >
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="t14 truncate">{it.title}</span>
                    {it.subtitle ? <span className="t12 truncate text-muted-foreground">{it.subtitle}</span> : null}
                  </span>
                  {it.badge ? <span className="t12 ml-auto shrink-0 text-muted-foreground">{it.badge}</span> : null}
                </CommandItem>
              ))}
            </CommandGroup>
          ))}
          {busy ? <p className="t12 px-3 py-3 text-muted-foreground">Đang tìm…</p> : null}
          {err ? <p className="t12 px-3 py-3 text-bad">{err}</p> : null}
          {!busy && !err && screens.length === 0 && talks.length === 0 && groups.length === 0 ? (
            <CommandEmpty>{term.length < 2 ? "Gõ ít nhất 2 ký tự để tìm khách, căn, giao dịch." : `Không thấy “${term}” trong phần anh/chị được xem.`}</CommandEmpty>
          ) : null}
        </CommandList>
      </Command>
  );
}

function CommandInputBox({ q, setQ }: { q: string; setQ: (v: string) => void }) {
  return <CommandInput value={q} onValueChange={setQ} placeholder="Tìm khách (tên, SĐT), mã căn, mã giao dịch, tên màn…" aria-label="Tìm nhanh" />;
}

