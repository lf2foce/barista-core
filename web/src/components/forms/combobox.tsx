"use client";

import { Check, ChevronsUpDown, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/primitives";
import { filterOptions, recentOptions, rememberOption, withRecents } from "@/lib/form-ui";
import type { Option } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Danh sách ngắn thì không cần ô tìm — bấm là thấy hết. */
const SEARCH_FROM = 8;

/**
 * Ô chọn có tìm cho `options_query` (khách, căn, người…): thay select phẳng. Bấm ⇒ danh sách thả xuống có ô gõ tìm (bỏ dấu, nhiều từ), nhãn + dòng phụ (`hint`);
 * ↑ ↓ Enter chọn, Esc đóng; lựa chọn gần nhất của máy này lên đầu (nhóm "Gần đây"). Danh sách có thể còn nữa (`remote`) ⇒ gõ là hỏi lại server (`search(q)`,
 * truy vấn chọn nhận `q`); server không hỗ trợ `q` thì cửa options lọc thay (core 1.61).
 */
export function Combobox({
  id, value, options, onChange, onClose, placeholder = "Chọn…", invalid, disabled, optional, query, remote, search, label, className, create,
}: {
  id?: string;
  value: string;
  options: Option[];
  /** `undefined` = bỏ chọn (chỉ khi `optional`). */
  onChange: (v: string | undefined) => void;
  /** Đóng danh sách (đã chọn hoặc bỏ qua) — form coi là "đã chạm ô" để hiện lỗi nếu còn trống. */
  onClose?: () => void;
  placeholder?: string;
  invalid?: boolean;
  disabled?: boolean;
  optional?: boolean;
  /** Tên truy vấn chọn — khoá nhớ "gần đây". Không có ⇒ không nhớ. */
  query?: string;
  /** Danh sách đang hiện CHƯA đủ (đã cắt ở giới hạn) ⇒ gõ để hỏi lại server. */
  remote?: boolean;
  search?: (q: string) => Promise<Option[]>;
  label?: string;
  className?: string;
  /** Danh sách rỗng ⇒ lối "Tạo mới" ngay trong ô (server khai `options_create`); bấm ⇒ mở form tạo, tạo xong form cha tải lại danh sách. */
  create?: { label: string; onCreate: () => void };
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [found, setFound] = useState<Option[] | null>(null);   // kết quả server cho `q` (null = chưa hỏi / q trống)
  const [loading, setLoading] = useState(false);
  const [extra, setExtra] = useState<Option | null>(null);     // giá trị đã chọn mà danh sách cắt không có: tra nhãn riêng
  const listId = useId();
  const seq = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);
  const searchable = options.length >= SEARCH_FROM || !!remote;

  // giá trị có sẵn (điền sẵn / lần trước) mà không nằm trong danh sách đã tải ⇒ hỏi server nhãn của nó
  useEffect(() => {
    let live = true;
    if (value && remote && search && !options.some((o) => o.value === value)) {
      search(value).then((rows) => live && setExtra(rows.find((r) => r.value === value) ?? null)).catch(() => undefined);
    }
    return () => {
      live = false;
    };
  }, [value, remote, search, options]);

  // gõ ⇒ hỏi lại server sau một nhịp (danh sách chưa đủ)
  useEffect(() => {
    if (!open || !remote || !search || !q.trim()) return;
    const mine = ++seq.current;
    const t = setTimeout(() => {
      setLoading(true);
      search(q.trim())
        .then((rows) => mine === seq.current && setFound(rows))
        .catch(() => mine === seq.current && setFound([]))
        .finally(() => mine === seq.current && setLoading(false));
    }, 250);
    return () => clearTimeout(t);
  }, [q, open, remote, search]);

  const recents = useMemo(() => (open && query && !q && options.length >= 6 ? recentOptions(query) : []), [open, query, q, options.length]);
  const shown = useMemo(() => {
    if (q.trim()) return { recent: [] as Option[], rest: remote && found ? found : filterOptions(options, q) };
    return withRecents(options, recents);
  }, [q, found, options, recents, remote]);
  const items = useMemo(() => [...(optional && !q.trim() && value ? [{ value: "", label: "Bỏ chọn" }] : []), ...shown.recent, ...shown.rest], [optional, q, value, shown]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const current = options.find((o) => o.value === value) ?? (extra && extra.value === value ? extra : null);
  const pick = (o: Option) => {
    if (o.value === "") onChange(undefined);
    else {
      onChange(o.value);
      if (query) rememberOption(query, o);
    }
    setOpen(false);
  };
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();   // Enter chọn dòng đang sáng — không gửi form
      if (items[active]) pick(items[active]);
    }
  };

  const row = (o: Option, i: number) => (
    <div
      key={`${i}-${o.value}`}
      id={`${listId}-${i}`}
      role="option"
      aria-selected={o.value === value}
      data-i={i}
      onMouseMove={() => setActive(i)}
      onClick={() => pick(o)}
      className={cn("flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5", i === active && "bg-accent", o.value === "" && "text-muted-foreground")}
    >
      <span className="min-w-0 flex-1">
        <span className="t14 block truncate">{o.label}</span>
        {o.hint ? <span className="t12 block truncate text-muted-foreground">{o.hint}</span> : null}
      </span>
      {o.value !== "" && o.value === value ? <Check className="size-4 shrink-0 text-primary" aria-hidden /> : null}
    </div>
  );
  let n = 0;
  const offset = items.length - shown.recent.length - shown.rest.length;   // dòng "Bỏ chọn" (0 hoặc 1) đứng trước nhóm

  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (o) {
          setQ("");
          setActive(0);
          setFound(null);
        }
        else onClose?.();
      }}
    >
      <PopoverTrigger
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={label}
        aria-invalid={invalid || undefined}
        disabled={disabled}
        className={cn(
          "t14 flex h-8 w-full items-center justify-between gap-2 rounded-lg border border-input bg-field px-2.5 text-left outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-bad data-popup-open:border-ring",
          className,
        )}
      >
        <span className={cn("min-w-0 flex-1 truncate", !current && !value && "text-muted-foreground")}>
          {current ? (
            <>
              {current.label}
              {current.hint ? <span className="t12 ml-2 text-muted-foreground">{current.hint}</span> : null}
            </>
          ) : value ? value : placeholder}
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-(--anchor-width) min-w-60 gap-1 p-1.5" onKeyDown={keys}>
        {searchable ? (
          <div className="flex items-center gap-2 border-b border-hairline px-1.5 pb-1.5">
            <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            <input
              autoFocus
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setActive(0);
                setFound(null);
              }}
              placeholder="Gõ để tìm…"
              aria-label="Tìm trong danh sách"
              aria-controls={listId}
              aria-activedescendant={items.length ? `${listId}-${active}` : undefined}
              className="t14 h-7 min-w-0 flex-1 bg-transparent outline-none placeholder:text-muted-foreground"
            />
            {loading ? <Spinner className="size-3.5" /> : null}
          </div>
        ) : null}
        <div ref={listRef} id={listId} role="listbox" tabIndex={searchable ? -1 : 0} aria-label={label} className="flex max-h-64 flex-col gap-0.5 overflow-y-auto outline-none">
          {items.length === 0 || (items.length === offset) ? (
            <p className="t14 px-2 py-3 text-muted-foreground">{loading ? "Đang tìm…" : q.trim() ? `Không có “${q.trim()}”` : create ? `Chưa có ${label ? label.toLowerCase() : "lựa chọn"} nào` : "Chưa có lựa chọn nào"}</p>
          ) : null}
          {create && !loading ? (
            <button type="button" className="t14m flex w-full items-center gap-1.5 rounded-md px-2 py-2 text-left text-primary hover:bg-surface2" onClick={() => { setOpen(false); create.onCreate(); }}>
              + {create.label}
            </button>
          ) : null}
          {offset ? row(items[0], n++) : null}
          {shown.recent.length ? <p className="t12m px-2 pt-1 text-muted-foreground">Gần đây</p> : null}
          {shown.recent.map((o) => row(o, n++))}
          {shown.recent.length && shown.rest.length ? <p className="t12m px-2 pt-1 text-muted-foreground">Tất cả</p> : null}
          {shown.rest.map((o) => row(o, n++))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
