"use client";

import {
  ChartArea, ChartBar, ChartColumn, ChartColumnBig, ChartLine, ChartPie, ChartScatter, Hash, LayoutGrid, Plus, SlidersHorizontal, Table, X,
  type LucideIcon,
} from "lucide-react";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import {
  autoTitle, dataChanged, datasetOf, decodeDesign, emptyBlock, filtersOf, listMeasures, normalize, removeFilter, setDataset, setFilter, setVisual, SPLITTABLE, TWO_MEASURES,
  visualOf, type BlockDef, type Choice, type Design, type Filter,
} from "@/lib/block-design";
import { newKey } from "@/lib/idempotency";
import type { ActionSpec, CardPage, Input, Json } from "@/lib/types";
import { cn } from "@/lib/utils";
import { actionClass, ErrorLine, Spinner } from "@/components/primitives";
import { selectClass } from "@/components/forms/form-body";
import { useDesk } from "@/components/desk/desk-context";

// Bảng sửa khối kiểu BI (0220 §bảng sửa): trái = khối xem trước (server chạy, chưa lưu), phải = khung chọn Hình / Dữ liệu / Lọc / Định dạng.
// Cùng định nghĩa khối với nút bút chì (AI sửa bằng lời) — người chọn tay chi tiết, AI dựng nhanh; lưu bằng đúng lệnh trợ lý dùng.

export type DesignTarget = { app_id: string; page: string; index: number | null };

const DesignContext = createContext<((t: DesignTarget) => void) | null>(null);

export function DesignProvider({ open, children }: { open: (t: DesignTarget) => void; children: ReactNode }) {
  return <DesignContext.Provider value={open}>{children}</DesignContext.Provider>;
}

/** Nút "Chỉnh khối" cạnh bút chì: chỉ khi khối thuộc app tự ghép (nút sửa của server là `apps.revise_block`) và đang trong màn có bảng sửa. */
export function useDesignOpener(edit?: ActionSpec): (() => void) | null {
  const open = useContext(DesignContext);
  if (!open || !edit || edit.command !== "apps.revise_block") return null;
  const { app_id, page, index } = edit.preset;
  if (typeof app_id !== "string" || typeof index !== "number") return null;
  return () => open({ app_id, page: typeof page === "string" ? page : "", index });
}

const ICON: Record<string, LucideIcon> = {
  kpi: Hash, table: Table, bar: ChartColumn, hbar: ChartBar, line: ChartLine, area: ChartArea, donut: ChartPie, combo: ChartColumnBig,
  scatter: ChartScatter, treemap: LayoutGrid,
};
export const WIDTHS: { w: number | null; label: string }[] = [
  { w: 3, label: "1/4" }, { w: 4, label: "1/3" }, { w: 6, label: "1/2" }, { w: 8, label: "2/3" }, { w: null, label: "Cả hàng" },
];

/** Trạng thái sửa MỘT khối: danh mục chọn (server), bản nháp, xem trước (server chạy, chưa lưu — 350ms sau lần đổi cuối), lưu bằng
 *  `apps.update_block` / `apps.add_block`. Tiêu đề tự gợi ý theo dữ liệu cho tới khi người sửa tự gõ. */
export function useBlockDesign(target: DesignTarget, onSaved: (index: number) => void) {
  const desk = useDesk();
  const [design, setDesign] = useState<Design | null>(null);
  const [draft, setDraft] = useState<BlockDef | null>(null);
  const [error, setError] = useState("");
  const [shown, setShown] = useState<CardPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [titled, setTitled] = useState(false);   // người sửa đã tự gõ tiêu đề ⇒ thôi gợi ý
  const key = useRef(newKey());

  useEffect(() => {
    let live = true;
    api.queryRaw<unknown>("apps.block_design", target as unknown as Input)
      .then((raw) => {
        if (!live) return;
        const d = decodeDesign(raw);
        setDesign(d);
        const b = d.block ?? emptyBlock(d);
        if (!b) setError("Bạn chưa xem được tập dữ liệu nào để dựng khối.");
        setDraft(b && !d.block ? { ...b, title: autoTitle(b, d) } : b);
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : "Không mở được bảng sửa"));
    return () => { live = false; };
  }, [target]);

  const body = useMemo(() => (draft ? JSON.stringify(draft) : ""), [draft]);
  useEffect(() => {
    if (!body || !design) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      setLoading(true);
      api.query("apps.preview_block", { ...target, block: JSON.parse(body) as Json }, ctrl.signal)
        .then((p) => {
          if (p.kind !== "card") return;
          setShown(p);
          setError("");
        })
        .catch((e) => {
          if (ctrl.signal.aborted) return;
          setShown(null);
          setError(e instanceof Error ? e.message : "Không xem trước được");
        })
        .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    }, 350);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [body, design, target]);

  const set = (b: BlockDef) => {   // chuẩn hoá theo tập dữ liệu của BẢN MỚI (đổi nguồn ⇒ chiều / chỉ số của nguồn mới)
    if (!design) return;
    setError("");
    const next = normalize(b, datasetOf(design, b));
    setDraft(draft && !titled && b.title === draft.title && dataChanged(draft, next) ? { ...next, title: autoTitle(next, design) } : next);
  };
  const setTitle = (title: string) => {
    if (!draft) return;
    setTitled(true);
    setDraft({ ...draft, title });
  };

  async function save() {
    if (!draft || !design) return;
    setBusy(true);
    try {
      const input: Input = { app_id: target.app_id, page: target.page, block: draft as unknown as Json };
      const r = target.index ? await api.command("apps.update_block", { ...input, index: target.index }, key.current)
                             : await api.command("apps.add_block", input, key.current);
      if (r.status !== "ok") {
        key.current = newKey();
        desk.show(r.error || `Chưa lưu được (${r.status})`, true);
        return;
      }
      key.current = newKey();
      desk.show(target.index ? "Đã lưu khối" : "Đã thêm khối");
      onSaved(target.index ?? design.blocks + 1);
    } catch (e) {
      desk.show(e instanceof Error ? e.message : "Chưa lưu được", true);
    } finally {
      setBusy(false);
    }
  }

  return { design, draft, set, setTitle, shown, loading, error, busy, save };
}

export type BlockDesign = ReturnType<typeof useBlockDesign>;

/** Khung bên phải của chế độ sửa: tên khối + Huỷ / Lưu + các ngăn chọn. */
export function DesignPane({ state, title, onClose }: { state: BlockDesign; title: string; onClose: () => void }) {
  const { design, draft, set, setTitle, error, busy, save, loading } = state;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-center gap-2">
        <SlidersHorizontal className="size-4 text-muted-foreground" aria-hidden />
        <span className="t14m min-w-0 flex-1 truncate">{title}</span>
        {loading ? <Spinner className="size-3.5" /> : null}
        <button type="button" className={cn(actionClass("default"), "h-7 px-2")} onClick={onClose} disabled={busy}>Huỷ</button>
        <button type="button" className={cn(actionClass("primary"), "h-7 px-2.5")} onClick={() => void save()} disabled={busy || !draft}>{busy ? "Đang lưu…" : "Lưu"}</button>
      </div>
      {error ? <ErrorLine>{error}</ErrorLine> : null}
      {design && draft ? <Panel design={design} draft={draft} set={set} setTitle={setTitle} /> : !error ? <Spinner /> : null}
    </div>
  );
}

function Panel({ design, draft, set, setTitle }: { design: Design; draft: BlockDef; set: (b: BlockDef) => void; setTitle: (t: string) => void }) {
  const ds = datasetOf(design, draft);
  const visual = visualOf(draft);
  const visuals: Choice[] = [...design.kinds, ...design.charts];
  const fixed = draft.kind === "source_kpis" || !!draft.source.sql;
  const list = !ds && !!draft.source.query && design.columns;
  const measures: Choice[] = ds ? ds.measures : list ? listMeasures(design.columns!) : [];
  const dims: Choice[] = ds ? ds.dims : list ? design.columns! : [];
  const selects = design.params.filter((p) => p.kind === "select");

  return (
    <div className="flex min-w-0 flex-col gap-4">   {/* nằm trong khung bên phải của chế độ sửa — không thêm một lớp viền nữa */}
      {design.note ? <p className="t12 text-muted-foreground">{design.note}</p> : null}
      {!fixed ? (
        <Group title="Hình">
          <div className="grid grid-cols-5 gap-1" role="radiogroup" aria-label="Hình">
            {visuals.map((v) => {
              const Icon = ICON[v.key] ?? ChartColumn;
              const on = v.key === visual;
              return (
                <button key={v.key} type="button" role="radio" aria-checked={on} title={v.label} aria-label={v.label}
                        onClick={() => set(setVisual(draft, v.key, ds))}
                        className={cn("grid h-9 place-items-center rounded-lg border outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                      on ? "border-primary bg-primary/10 text-primary" : "border-transparent text-muted-foreground hover:bg-accent hover:text-foreground")}>
                  <Icon className="size-4" aria-hidden />
                </button>
              );
            })}
          </div>
          <span className="t12 text-muted-foreground">{visuals.find((v) => v.key === visual)?.label}</span>
        </Group>
      ) : null}

      {!fixed ? (
        <Group title="Dữ liệu">
          {ds || !draft.source.query ? (
            <Pick label="Nguồn" value={draft.source.dataset ?? ""} options={design.datasets.map((d) => ({ key: d.key, label: d.title }))}
                  onChange={(k) => { const d = design.datasets.find((x) => x.key === k); if (d) set(setDataset(draft, d)); }} />
          ) : (
            <p className="t12 text-muted-foreground">Nguồn danh sách: {draft.source.query}</p>
          )}
          {draft.kind === "table" && ds ? (
            <>
              <Many label="Giá trị" options={measures} value={draft.source.measures ?? []}
                    onChange={(m) => set({ ...draft, source: { ...draft.source, measures: m } })} />
              <Many label="Chia theo (tối đa 2)" options={dims} value={draft.source.by ?? []} max={2}
                    onChange={(b) => set({ ...draft, source: { ...draft.source, by: b } })} />
            </>
          ) : draft.kind === "table" ? (
            <Many label="Cột" options={dims} value={draft.columns ?? []} onChange={(c) => set({ ...draft, columns: c })} />
          ) : (
            <>
              <Pick label={TWO_MEASURES.has(visual) ? (visual === "scatter" ? "Giá trị trục ngang" : "Giá trị cột") : "Giá trị"}
                    value={draft.measure ?? ""} options={measures} onChange={(k) => set({ ...draft, measure: k })} />
              {TWO_MEASURES.has(visual) ? (
                <Pick label={visual === "scatter" ? "Giá trị trục đứng" : "Giá trị đường (trục phải)"} value={draft.measure2 ?? ""}
                      options={measures.filter((m) => m.key !== draft.measure)} onChange={(k) => set({ ...draft, measure2: k })} />
              ) : null}
              {draft.kind === "chart" ? (
                <Pick label={visual === "donut" || visual === "treemap" ? "Chia theo" : visual === "scatter" ? "Mỗi điểm là" : "Trục ngang"}
                      value={draft.by ?? ""} options={dims} onChange={(k) => set({ ...draft, by: k })} />
              ) : null}
              {draft.kind === "chart" && SPLITTABLE.has(visual) && ds ? (
                <Pick label="Tách chuỗi theo" value={draft.split ?? ""} options={dims.filter((d) => d.key !== draft.by)} empty="Không tách"
                      onChange={(k) => set({ ...draft, split: k })} />
              ) : null}
            </>
          )}
        </Group>
      ) : null}

      {ds && !fixed ? <Filters design={design} draft={draft} set={set} /> : null}

      {ds && ds.dates.length && !fixed ? (
        <Group title="Kỳ">
          <Pick label="Lọc kỳ" value={draft.source.period ?? ""} empty="Không lọc kỳ"
                options={[...design.params.filter((p) => p.kind === "period").map((p) => ({ key: `$${p.key}`, label: `Theo tham số ${p.label}` })), ...design.periods]}
                onChange={(k) => set({ ...draft, source: { ...draft.source, period: k, date: k ? draft.source.date || ds.dates[0].key : "" } })} />
          {draft.source.period && ds.dates.length > 1 ? (
            <Pick label="Theo ngày" value={draft.source.date || ds.dates[0].key} options={ds.dates}
                  onChange={(k) => set({ ...draft, source: { ...draft.source, date: k } })} />
          ) : null}
        </Group>
      ) : null}

      <Group title="Định dạng">
        <label className="flex flex-col gap-1">
          <span className="t12 text-muted-foreground">Tiêu đề</span>
          <input className={selectClass} value={draft.title ?? ""} maxLength={80} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <div className="flex flex-col gap-1">
          <span className="t12 text-muted-foreground">Bề rộng trên dashboard</span>
          <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Bề rộng">
            {WIDTHS.map((x) => {
              const on = (draft.w ?? null) === x.w;
              return (
                <button key={x.label} type="button" role="radio" aria-checked={on} onClick={() => set({ ...draft, w: x.w })}
                        className={cn("t12m h-7 rounded-md border px-2", on ? "border-primary bg-primary/10 text-primary" : "border-border hover:bg-accent")}>
                  {x.label}
                </button>
              );
            })}
          </div>
        </div>
        {draft.kind === "table" ? (
          <>
            <label className="flex flex-col gap-1">
              <span className="t12 text-muted-foreground">Số dòng hiện</span>
              <input type="number" className={selectClass} min={1} max={design.maxRows} value={draft.rows ?? 20}
                     onChange={(e) => set({ ...draft, rows: Math.max(1, Math.min(design.maxRows, Number(e.target.value) || 1)) })} />
            </label>
            <Sort draft={draft} options={ds ? [...(draft.source.by ?? []), ...(draft.source.measures ?? [])].map((k) => [...dims, ...measures].find((c) => c.key === k) ?? { key: k, label: k }) : dims}
                  set={set} />
          </>
        ) : null}
        {draft.kind === "chart" && selects.length ? (
          <Pick label="Bấm vào biểu đồ để lọc cả trang theo" value={draft.filter_param ?? ""} empty="Không lọc chéo" options={selects}
                onChange={(k) => set({ ...draft, filter_param: k })} />
        ) : null}
      </Group>
    </div>
  );
}

function Filters({ design, draft, set }: { design: Design; draft: BlockDef; set: (b: BlockDef) => void }) {
  const ds = datasetOf(design, draft)!;
  const filters = filtersOf(draft);
  const [dim, setDim] = useState("");
  const [values, setValues] = useState<Choice[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [param, setParam] = useState("");
  const selects = design.params.filter((p) => p.kind === "select");
  const label = (d: string) => ds.dims.find((x) => x.key === d)?.label ?? d;

  const choose = (d: string) => {   // đổi chiều ⇒ bỏ giá trị đã tích của chiều cũ
    setDim(d);
    setValues(null);
    setPicked([]);
    setParam("");
  };

  useEffect(() => {
    if (!dim) return;
    let live = true;
    api.queryRaw<{ value: Json; label: string }[]>("apps.dim_values", { dataset: ds.key, dim })
      .then((v) => live && setValues(v.map((x) => ({ key: String(x.value), label: x.label }))))
      .catch(() => live && setValues([]));
    return () => { live = false; };
  }, [dim, ds.key]);

  const text = (f: Filter) => {
    if (f.param) return `theo tham số ${design.params.find((p) => p.key === f.param)?.label ?? f.param}`;
    const names = ds.dims.find((x) => x.key === f.dim)?.values ?? {};
    return f.values.map((v) => names[String(v)] ?? String(v)).join(", ");
  };

  return (
    <Group title="Lọc">
      {filters.length ? (
        <ul className="flex flex-col gap-1">
          {filters.map((f) => (
            <li key={f.dim} className="t12 flex items-start gap-1.5 rounded-lg bg-surface2 px-2 py-1.5">
              <span className="min-w-0 flex-1"><span className="t12m">{label(f.dim)}:</span> {text(f)}</span>
              <button type="button" aria-label={`Bỏ lọc ${label(f.dim)}`} onClick={() => set(removeFilter(draft, f.dim))} className="text-muted-foreground hover:text-foreground">
                <X className="size-3.5" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : <span className="t12 text-muted-foreground">Chưa lọc — tính trên mọi dòng bạn xem được.</span>}
      <Pick label="Thêm lọc theo" value={dim} empty="Chọn chiều…" options={ds.dims.filter((d) => !filters.some((f) => f.dim === d.key))} onChange={choose} />
      {dim ? (
        <div className="flex flex-col gap-2 rounded-lg border border-border p-2">
          {selects.length ? (
            <Pick label="Gắn tham số của app" value={param} empty="Không — chọn giá trị cố định" options={selects} onChange={setParam} />
          ) : null}
          {!param ? (
            values === null ? <Spinner /> : values.length ? (
              <div className="flex max-h-48 flex-col gap-0.5 overflow-y-auto">
                {values.map((v) => (
                  <label key={v.key} className="t12 flex items-center gap-2 rounded px-1 py-0.5 hover:bg-accent">
                    <input type="checkbox" checked={picked.includes(v.key)}
                           onChange={(e) => setPicked((cur) => (e.target.checked ? [...cur, v.key] : cur.filter((x) => x !== v.key)))} />
                    <span className="min-w-0 truncate">{v.label}</span>
                  </label>
                ))}
              </div>
            ) : <span className="t12 text-muted-foreground">Chưa có giá trị nào trong dòng bạn xem được.</span>
          ) : null}
          <button type="button" className={actionClass("default")} disabled={!param && !picked.length}
                  onClick={() => { set(setFilter(draft, { dim, values: picked, param })); choose(""); }}>
            <Plus className="size-3.5" aria-hidden />
            Áp lọc
          </button>
        </div>
      ) : null}
    </Group>
  );
}

function Sort({ draft, options, set }: { draft: BlockDef; options: Choice[]; set: (b: BlockDef) => void }) {
  const cur = (draft.sort ?? "").replace(/^-/, "");
  const desc = (draft.sort ?? "").startsWith("-");
  return (
    <div className="flex items-end gap-2">
      <div className="min-w-0 flex-1">
        <Pick label="Xếp theo" value={cur} empty="Như nguồn" options={options} onChange={(k) => set({ ...draft, sort: k ? (desc ? `-${k}` : k) : "" })} />
      </div>
      {cur ? (
        <button type="button" className={actionClass("default")} onClick={() => set({ ...draft, sort: desc ? cur : `-${cur}` })}>
          {desc ? "Lớn trước" : "Nhỏ trước"}
        </button>
      ) : null}
    </div>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h4 className="t12m text-muted-foreground">{title}</h4>
      {children}
    </section>
  );
}

function Pick({ label, value, options, onChange, empty }: { label: string; value: string; options: Choice[]; onChange: (k: string) => void; empty?: string }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="t12 text-muted-foreground">{label}</span>
      <select aria-label={label} className={selectClass} value={value} onChange={(e) => onChange(e.target.value)}>
        {empty !== undefined || !options.some((o) => o.key === value) ? <option value="">{empty ?? "—"}</option> : null}
        {options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
      </select>
    </label>
  );
}

function Many({ label, options, value, onChange, max }: { label: string; options: Choice[]; value: string[]; onChange: (v: string[]) => void; max?: number }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-1">
      <legend className="t12 mb-1 text-muted-foreground">{label}</legend>
      <div className="flex max-h-44 flex-col gap-0.5 overflow-y-auto">
        {options.map((o) => {
          const on = value.includes(o.key);
          return (
            <label key={o.key} className="t12 flex items-center gap-2 rounded px-1 py-0.5 hover:bg-accent">
              <input type="checkbox" checked={on} disabled={!on && max !== undefined && value.length >= max}
                     onChange={(e) => onChange(e.target.checked ? [...value, o.key] : value.filter((x) => x !== o.key))} />
              <span className="min-w-0 truncate">{o.label}</span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
