"use client";

import { ChartColumn } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, LabelList, Line, Pie, PieChart, Scatter, ScatterChart, Tooltip, Treemap, XAxis, YAxis, ZAxis } from "recharts";
import { ChartContainer, type ChartConfig } from "@/components/ui/chart";
import { EditFrame } from "@/components/pages/edit-block";
import { WhyButton } from "@/components/pages/why-button";
import { useScreenFilters } from "@/components/pages/filter-context";
import { SourceLine } from "@/components/pages/source-line";
import type { ActionSpec, BlockSource } from "@/lib/types";
import { chartAxis, chartColor, chartRows, chartValue, comboSeries, donutSlices, funnelSteps, hasChartData, headroom, seriesUnit, type ChartData } from "@/lib/chart";
import { cn } from "@/lib/utils";
import { ClampNote } from "@/components/clamp-note";

// Khối "chart" theo cách Apple dùng Swift Charts (Charts.swift): màu hệ thống, một chuỗi = một màu, trục số mảnh bên phải, KHÔNG in số
// lên mọi cột — rê chuột mới hiện ô số. Thanh ngang / phễu thì số là điều cần đọc ⇒ in cuối thanh. null ⇒ không vẽ (không thành 0).

/** id dùng trong url(#…) của SVG: chỉ chữ / số (useId của React có ký tự như «» hay ":"). */
function useSvgId(): string {
  return "c" + useId().replace(/[^a-zA-Z0-9_-]/g, "");
}

const AXIS_TICK = { fontSize: 12, fill: "var(--muted-foreground)" };

/** Nhãn trục X khi nhiều nhãn / nhãn dài (biểu đồ hẹp cạnh nhau): nghiêng và chừa chỗ theo nhãn dài nhất, chỉ cắt đuôi khi quá 24 ký tự (tên đầy đủ vẫn ở ô số khi rê chuột). */
export function xAxisFit(labels: string[]): { tickFormatter?: (v: string) => string; angle?: number; textAnchor?: "end"; height?: number } {
  const longest = Math.max(0, ...labels.map((l) => l.length));
  if (labels.length <= 6 && longest <= 9) return {};
  const cut = 24;
  const shown = Math.min(longest, cut);
  return { tickFormatter: (v: string) => (v.length > cut ? `${v.slice(0, cut - 1)}…` : v), angle: -40, textAnchor: "end", height: Math.min(130, Math.round(shown * 4.6) + 18) };
}

/** Các khối biểu đồ liền nhau: lưới 1–2 cột theo bề rộng của thẻ (thẻ rộng không kéo dài biểu đồ ra hết bề ngang). */
export function ChartGrid({ blocks, onChanged }: { blocks: (ChartData & { edit?: ActionSpec; source?: BlockSource })[]; onChanged?: () => void }) {
  return (
    <div className="@container">
      <div className="grid grid-cols-1 items-start gap-3 @min-[824px]:grid-cols-2">
        {blocks.map((b, i) => {
          // số biểu đồ lẻ ⇒ biểu đồ cuối trải hết hai cột, không để trống nửa thẻ (rà UI 18/11)
          const wide = blocks.length % 2 === 1 && i === blocks.length - 1;
          return (
            <div key={i} className={wide ? "@min-[824px]:col-span-2" : undefined}>
              <EditFrame edit={b.edit} onChanged={onChanged}><ChartCard block={b} /></EditFrame>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function ChartCard({ block }: { block: ChartData & { source?: BlockSource; why?: string } }) {
  return (
    <figure className="flex min-w-0 flex-col gap-3 rounded-[14px] border border-hairline bg-surface p-4" aria-label={block.title || "Biểu đồ"}>
      {block.title || block.why ? (
        <div className="flex min-w-0 items-start gap-2">
          {block.title ? <figcaption className="t14m min-w-0 flex-1">{block.title}</figcaption> : <span className="flex-1" />}
          {block.why ? <WhyButton prompt={block.why} /> : null}
        </div>
      ) : null}
      {hasChartData(block) ? (
        <ChartBody block={block} />
      ) : (
        <p className="t12 flex min-h-[120px] items-center justify-center gap-1.5 text-muted-foreground">
          <ChartColumn className="size-4" aria-hidden />
          Chưa đo được — chưa có dữ liệu đủ tin cậy
        </p>
      )}
      {block.note ? <ClampNote text={block.note} /> : null}
      <SourceLine source={block.source} />
    </figure>
  );
}

function ChartBody({ block }: { block: ChartData }) {
  switch (block.kind) {
    case "line":
    case "area":
      return <LineArea block={block} />;
    case "hbar":
      return <HBar block={block} />;
    case "donut":
      return <Donut block={block} />;
    case "funnel":
      return <Funnel block={block} />;
    case "combo":
      return <Combo block={block} />;
    case "scatter":
      return <ScatterPlot block={block} />;
    case "treemap":
      return <TreemapPlot block={block} />;
    default:
      return <Bars block={block} />;
  }
}

function configOf(block: ChartData): ChartConfig {
  return Object.fromEntries(block.series.map((s, i) => [`s${i}`, { label: s.name, color: chartColor(s.tone, i) }]));
}

/** Ô số nổi khi rê chuột: nhãn + từng chuỗi (chấm màu, tên, số) — như Stocks / Health. Đọc thẳng từ khối để null hiện "chưa đo được". */
function Pop({ block, label }: { block: ChartData; label: string }) {
  const li = block.labels.indexOf(label);
  if (li < 0) return null;
  const multi = block.series.length > 1;
  return (
    <div className="flex flex-col gap-0.5 rounded-lg border border-border bg-popover px-2.5 py-1.5 text-popover-foreground shadow-md">
      <span className="t12m">{label}</span>
      {block.series.map((s, i) => {
        const v = s.values[li];
        return (
          <span key={i} className="t12 flex items-center gap-1.5">
            {multi ? <span className="size-[7px] rounded-full" style={{ background: chartColor(s.tone, i) }} /> : null}
            {multi ? <span className="text-muted-foreground">{s.name}</span> : null}
            <span className="tnum font-medium">{typeof v === "number" ? chartValue(v, seriesUnit(block, s)) : "chưa đo được"}</span>
          </span>
        );
      })}
    </div>
  );
}

function Legend({ block }: { block: ChartData }) {
  if (block.series.length < 2) return null;
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1">
      {block.series.map((s, i) => (
        <span key={i} className="t12 flex items-center gap-1.5 text-muted-foreground">
          <span className="size-2 rounded-full" style={{ background: chartColor(s.tone, i) }} />
          {s.name}
        </span>
      ))}
    </div>
  );
}

/** Hoạt hoạ mọc lên chỉ lúc mới vẽ: sau đó tắt để rê chuột (đổi độ mờ, hiện ô số) không làm Recharts vẽ lại / nhấp nháy cả biểu đồ. */
function useIntro(): boolean {
  const [on, setOn] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setOn(false), 1200);
    return () => clearTimeout(t);
  }, []);
  return on;
}

/** Lọc chéo (0173): bấm nhãn i đặt bộ lọc của màn; nhãn đang được lọc nổi lên, các nhãn khác mờ đi. Ngoài màn ⇒ không bấm được. */
function usePick(block: ChartData) {
  const f = useScreenFilters();
  const pick = block.pick;
  if (!f || !pick) return { clickable: false, sel: -1, pickAt: () => {} };
  const cur = f.get(pick.param);
  return {
    clickable: true,
    sel: cur ? pick.values.indexOf(cur) : -1,
    pickAt: (i: number) => {
      const v = pick.values[i];
      if (v === undefined) return;
      f.set(pick.param, cur === v ? undefined : v);
    },
  };
}

const dimmed = (sel: number, i: number, active: number | null) => (sel >= 0 ? (sel === i ? 1 : 0.3) : active === null || active === i ? 1 : 0.35);

function useActive() {
  const [active, setActive] = useState<number | null>(null);
  return {
    active,
    onMouseMove: (st: { activeTooltipIndex?: number | string | null } | null | undefined) => {
      const i = st?.activeTooltipIndex;
      setActive(i === null || i === undefined || i === "" ? null : Number(i));
    },
    onMouseLeave: () => setActive(null),
  };
}

function Bars({ block }: { block: ChartData }) {
  const intro = useIntro();
  const id = useSvgId();
  const rows = useMemo(() => chartRows(block), [block]);   // cùng mảng giữa các lần vẽ ⇒ rê chuột không làm Recharts hoạt hoạ lại cả biểu đồ (nhấp nháy)
  const config = useMemo(() => configOf(block), [block]);
  const { active, onMouseMove, onMouseLeave } = useActive();
  const { clickable, sel, pickAt } = usePick(block);
  const onClick = (st: { activeTooltipIndex?: number | string | null } | null | undefined) => {
    const i = st?.activeTooltipIndex;
    if (clickable && i !== null && i !== undefined && i !== "") pickAt(Number(i));
  };
  const multi = block.series.length > 1;
  return (
    <div className="flex flex-1 flex-col gap-2">
      <ChartContainer config={config} minHeight={200} className="aspect-auto min-h-[200px] w-full flex-1">
        <BarChart data={rows} margin={{ top: 8, right: 0, left: 0, bottom: 0 }} barCategoryGap={multi ? "20%" : "35%"} onMouseMove={onMouseMove} onMouseLeave={onMouseLeave} onClick={onClick} style={clickable ? { cursor: "pointer" } : undefined}>
          <defs>
            {block.series.map((s, i) => (
              <linearGradient key={i} id={`${id}-g${i}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={chartColor(s.tone, i)} stopOpacity={1} />
                <stop offset="100%" stopColor={chartColor(s.tone, i)} stopOpacity={0.72} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid vertical={false} stroke="var(--foreground)" strokeOpacity={0.1} strokeWidth={0.5} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} tick={AXIS_TICK} tickMargin={6} minTickGap={8} interval={block.labels.length <= 8 ? 0 : "preserveStartEnd"} {...xAxisFit(block.labels)} />
          <YAxis orientation="right" tickLine={false} axisLine={false} tick={AXIS_TICK} tickCount={4} width={64} tickFormatter={(v: number) => chartAxis(v, block.unit)} />
          <Tooltip cursor={{ fill: "var(--foreground)", fillOpacity: 0.05 }} content={(p) => (p.active && p.label !== undefined ? <Pop block={block} label={String(p.label)} /> : null)} />
          {block.series.map((s, i) => (
            <Bar key={i} dataKey={`s${i}`} name={s.name} fill={`url(#${id}-g${i})`} radius={[4, 4, 0, 0]} maxBarSize={56} isAnimationActive={intro}>
              {rows.map((_, ri) => (
                <Cell key={ri} fillOpacity={dimmed(sel, ri, active)} />
              ))}
            </Bar>
          ))}
        </BarChart>
      </ChartContainer>
      <Legend block={block} />
    </div>
  );
}

/** Combo (0166): cột + đường trên một biểu đồ; chuỗi `axis: "right"` có trục phải riêng đo bằng `unit_right` (vd tỉ lệ % cạnh số tiền). */
function Combo({ block }: { block: ChartData }) {
  const intro = useIntro();
  const id = useSvgId();
  const rows = useMemo(() => chartRows(block), [block]);   // cùng mảng giữa các lần vẽ ⇒ rê chuột không làm Recharts hoạt hoạ lại cả biểu đồ (nhấp nháy)
  const config = useMemo(() => configOf(block), [block]);
  const { active, onMouseMove, onMouseLeave } = useActive();
  const { clickable, sel, pickAt } = usePick(block);
  const onClick = (st: { activeTooltipIndex?: number | string | null } | null | undefined) => {
    const i = st?.activeTooltipIndex;
    if (clickable && i !== null && i !== undefined && i !== "") pickAt(Number(i));
  };
  const parts = comboSeries(block);
  const hasRight = parts.some((p) => p.axis === "right");
  const unitRight = block.unit_right || block.unit;
  const bars = parts.filter((p) => p.as === "bar");
  return (
    <div className="flex flex-1 flex-col gap-2">
      <ChartContainer config={config} minHeight={220} className="aspect-auto min-h-[220px] w-full flex-1">
        <ComposedChart data={rows} margin={{ top: 8, right: 0, left: 0, bottom: 0 }} barCategoryGap="30%" onMouseMove={onMouseMove} onMouseLeave={onMouseLeave} onClick={onClick} style={clickable ? { cursor: "pointer" } : undefined}>
          <defs>
            {bars.map(({ index, series }) => (
              <linearGradient key={index} id={`${id}-g${index}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={chartColor(series.tone, index)} stopOpacity={1} />
                <stop offset="100%" stopColor={chartColor(series.tone, index)} stopOpacity={0.72} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid vertical={false} stroke="var(--foreground)" strokeOpacity={0.1} strokeWidth={0.5} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} tick={AXIS_TICK} tickMargin={6} minTickGap={8} interval={block.labels.length <= 8 ? 0 : "preserveStartEnd"} {...xAxisFit(block.labels)} />
          <YAxis yAxisId="left" orientation={hasRight ? "left" : "right"} tickLine={false} axisLine={false} tick={AXIS_TICK} tickCount={4} width={64} tickFormatter={(v: number) => chartAxis(v, block.unit)} />
          {hasRight ? <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} tick={AXIS_TICK} tickCount={4} width={48} tickFormatter={(v: number) => chartAxis(v, unitRight)} /> : null}
          <Tooltip cursor={{ fill: "var(--foreground)", fillOpacity: 0.05 }} content={(p) => (p.active && p.label !== undefined ? <Pop block={block} label={String(p.label)} /> : null)} />
          {bars.map(({ index, series, axis }) => (
            <Bar key={`b${index}`} yAxisId={axis} dataKey={`s${index}`} name={series.name} fill={`url(#${id}-g${index})`} radius={[4, 4, 0, 0]} maxBarSize={48} isAnimationActive={intro}>
              {rows.map((_, ri) => (
                <Cell key={ri} fillOpacity={dimmed(sel, ri, active)} />
              ))}
            </Bar>
          ))}
          {parts.filter((p) => p.as === "line").map(({ index, series, axis }) => (
            <Line
              key={`l${index}`}
              yAxisId={axis}
              type="linear"
              dataKey={`s${index}`}
              name={series.name}
              stroke={chartColor(series.tone, index)}
              strokeWidth={2.2}
              strokeLinecap="round"
              strokeLinejoin="round"
              dot={{ r: 3, strokeWidth: 0, fill: chartColor(series.tone, index) }}
              activeDot={{ r: 4, strokeWidth: 0, fill: chartColor(series.tone, index) }}
              connectNulls={false}
            />
          ))}
        </ComposedChart>
      </ChartContainer>
      <Legend block={block} />
    </div>
  );
}

/** Scatter (0173): mỗi nhãn một điểm; `series[0].x` hoành độ, `values` tung độ (như biểu đồ "% lãi gộp theo số giao dịch của từng dự án"). */
function ScatterPlot({ block }: { block: ChartData }) {
  const intro = useIntro();
  const { clickable, sel, pickAt } = usePick(block);
  const s0 = block.series[0];
  const pts = useMemo(
    () => block.labels.flatMap((label, i) => (typeof s0?.x?.[i] === "number" && typeof s0.values[i] === "number" ? [{ label, i, x: s0.x[i] as number, y: s0.values[i] as number }] : [])),
    [block, s0],
  );
  const color = chartColor(s0?.tone, 0);
  return (
    <ChartContainer config={{ s0: { label: s0?.name ?? "", color } }} minHeight={240} className="aspect-auto min-h-[240px] w-full flex-1">
      <ScatterChart margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="var(--foreground)" strokeOpacity={0.1} strokeWidth={0.5} />
        <XAxis type="number" dataKey="x" name="x" tickLine={false} axisLine={false} tick={AXIS_TICK} tickFormatter={(v: number) => chartAxis(v, block.unit_right || "count")} />
        <YAxis type="number" dataKey="y" name={s0?.name ?? "y"} orientation="right" tickLine={false} axisLine={false} tick={AXIS_TICK} width={56} tickFormatter={(v: number) => chartAxis(v, block.unit)} />
        <ZAxis range={[70, 70]} />
        <Tooltip cursor={{ strokeDasharray: "3 3" }} content={(p) => {
          const d = p.active ? (p.payload?.[0]?.payload as { label: string; x: number; y: number } | undefined) : undefined;
          return d ? (
            <div className="flex flex-col gap-0.5 rounded-lg border border-border bg-popover px-2.5 py-1.5 text-popover-foreground shadow-md">
              <span className="t12m">{d.label}</span>
              <span className="t12 tnum">{chartValue(d.x, block.unit_right || "count")} · {chartValue(d.y, block.unit)}</span>
            </div>
          ) : null;
        }} />
        <Scatter data={pts} fill={color} isAnimationActive={intro} onClick={(d) => { const i = (d as unknown as { payload?: { i?: number } }).payload?.i; if (clickable && typeof i === "number") pickAt(i); }} cursor={clickable ? "pointer" : undefined}>
          {pts.map((p) => <Cell key={p.i} fill={color} fillOpacity={sel >= 0 ? (p.i === sel ? 1 : 0.3) : 0.8} />)}
        </Scatter>
      </ScatterChart>
    </ChartContainer>
  );
}

/** Treemap (0173): diện tích ô = giá trị; ô quá nhỏ không in tên (rê chuột xem). */
function TreemapPlot({ block }: { block: ChartData }) {
  const intro = useIntro();
  const { clickable, sel, pickAt } = usePick(block);
  const data = useMemo(() => {
    const vals = block.series[0]?.values ?? [];
    return block.labels.flatMap((name, i) => (typeof vals[i] === "number" && (vals[i] as number) > 0 ? [{ name, size: vals[i] as number, i }] : []));
  }, [block]);
  return (
    <ChartContainer config={{}} minHeight={260} className="aspect-auto min-h-[260px] w-full flex-1">
      <Treemap
        data={data}
        dataKey="size"
        nameKey="name"
        stroke="var(--background)"
        isAnimationActive={intro}
        content={(p: { x?: number; y?: number; width?: number; height?: number; name?: string; value?: number; index?: number }) => {
          const { x = 0, y = 0, width = 0, height = 0, name = "", value = 0, index = 0 } = p;
          const i = data[index]?.i ?? index;
          const big = width > 70 && height > 34;
          return (
            <g onClick={() => clickable && pickAt(i)} style={clickable ? { cursor: "pointer" } : undefined}>
              <rect x={x} y={y} width={width} height={height} rx={6} fill={chartColor(undefined, index)} fillOpacity={sel >= 0 ? (i === sel ? 1 : 0.3) : 0.9} stroke="var(--background)" strokeWidth={2}>
                <title>{`${name}: ${chartValue(value, block.unit)}`}</title>
              </rect>
              {big ? (
                <>
                  <text x={x + 8} y={y + 18} fontSize={12} fontWeight={500} fill="#fff">{name.length > Math.floor(width / 7) ? `${name.slice(0, Math.max(1, Math.floor(width / 7) - 1))}…` : name}</text>
                  <text x={x + 8} y={y + 34} fontSize={11} fill="#fff" fillOpacity={0.85}>{chartValue(value, block.unit)}</text>
                </>
              ) : null}
            </g>
          );
        }}
      />
    </ChartContainer>
  );
}

function LineArea({ block }: { block: ChartData }) {
  const intro = useIntro();
  const id = useSvgId();
  const rows = useMemo(() => chartRows(block), [block]);   // cùng mảng giữa các lần vẽ ⇒ rê chuột không làm Recharts hoạt hoạ lại cả biểu đồ (nhấp nháy)
  const config = useMemo(() => configOf(block), [block]);
  const multi = block.series.length > 1;
  const filled = block.kind === "area" || !multi;
  return (
    <div className="flex flex-1 flex-col gap-2">
      <ChartContainer config={config} minHeight={200} className="aspect-auto min-h-[200px] w-full flex-1">
        <ComposedChart data={rows} margin={{ top: 8, right: 0, left: 24, bottom: 0 }}>   {/* trái 24: nhãn đầu kiểu "11/2026" canh giữa điểm đầu — 14 thì bị cắt còn "/2026" */}
          <defs>
            {block.series.map((s, i) => (
              <linearGradient key={i} id={`${id}-a${i}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={chartColor(s.tone, i)} stopOpacity={block.kind === "area" ? 0.3 : 0.18} />
                <stop offset="100%" stopColor={chartColor(s.tone, i)} stopOpacity={0} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid vertical={false} stroke="var(--foreground)" strokeOpacity={0.1} strokeWidth={0.5} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} tick={AXIS_TICK} tickMargin={6} minTickGap={8} interval={block.labels.length <= 8 ? 0 : "preserveStartEnd"} {...xAxisFit(block.labels)} />
          <YAxis orientation="right" tickLine={false} axisLine={false} tick={AXIS_TICK} tickCount={4} width={64} tickFormatter={(v: number) => chartAxis(v, block.unit)} />
          <Tooltip cursor={{ stroke: "var(--foreground)", strokeOpacity: 0.18, strokeWidth: 1 }} content={(p) => (p.active && p.label !== undefined ? <Pop block={block} label={String(p.label)} /> : null)} />
          {filled
            ? block.series.map((s, i) => (
                <Area key={`a${i}`} type="monotone" dataKey={`s${i}`} stroke="none" fill={`url(#${id}-a${i})`} connectNulls={false} isAnimationActive={intro} activeDot={false} tooltipType="none" />
              ))
            : null}
          {block.series.map((s, i) => (
            <Line
              key={`l${i}`}
              type="monotone"
              dataKey={`s${i}`}
              name={s.name}
              stroke={chartColor(s.tone, i)}
              strokeWidth={2.2}
              strokeLinecap="round"
              strokeLinejoin="round"
              dot={rows.length <= 2 ? { r: 3.5, strokeWidth: 0, fill: chartColor(s.tone, i) } : false}   // một / hai mốc ⇒ đường gần như không thấy, hiện chấm
              activeDot={{ r: 4, strokeWidth: 0, fill: chartColor(s.tone, i) }}
              connectNulls={false}
            />
          ))}
        </ComposedChart>
      </ChartContainer>
      <Legend block={block} />
    </div>
  );
}

const labelWidth = (labels: string[]) => Math.min(170, Math.max(48, Math.max(0, ...labels.map((l) => l.length)) * 7.4 + 10));

/** Nhãn hàng của trục dọc: một dòng, cắt đuôi nếu dài (Recharts mặc định bẻ dòng). */
function CategoryTick(p: { x?: number | string; y?: number | string; payload?: { value?: unknown }; width: number }) {
  const raw = String(p.payload?.value ?? "");
  const max = Math.floor((p.width - 10) / 7);
  const text = raw.length > max ? `${raw.slice(0, Math.max(1, max - 1))}…` : raw;
  return (
    <text x={Number(p.x ?? 0) - 6} y={Number(p.y ?? 0)} textAnchor="end" dominantBaseline="central" fontSize={12} fill="var(--muted-foreground)">
      <title>{raw}</title>
      {text}
    </text>
  );
}

/** Chỗ trống bên phải cho số in cuối thanh (ước theo độ dài chữ dài nhất, ~7px / ký tự) — số không bao giờ bị cắt. */
function endPad(block: ChartData, extraChars = 0): number {
  const longest = Math.max(0, ...block.series.flatMap((s) => s.values).map((v) => (typeof v === "number" ? chartValue(v, block.unit).length : 0)));
  return Math.round((longest + extraChars) * 7 + 14);
}

/** Số cuối thanh (thanh ngang): một dòng, không bẻ. */
function EndValue(p: { x?: number | string; y?: number | string; width?: number | string; height?: number | string; value?: unknown; unit: string }) {
  if (typeof p.value !== "number") return null;
  return (
    <text x={Number(p.x ?? 0) + Number(p.width ?? 0) + 6} y={Number(p.y ?? 0) + Number(p.height ?? 0) / 2} dominantBaseline="central" fontSize={12} fontWeight={500} fill="var(--muted-foreground)" className="tnum">
      {chartValue(p.value, p.unit)}
    </text>
  );
}

/** Thanh ngang (xếp hạng): số cuối thanh. Nhãn không đo được ⇒ không có thanh. */
function HBar({ block }: { block: ChartData }) {
  const intro = useIntro();
  const id = useSvgId();
  const rows = useMemo(() => chartRows(block), [block]);   // cùng mảng giữa các lần vẽ ⇒ rê chuột không làm Recharts hoạt hoạ lại cả biểu đồ (nhấp nháy)
  const config = useMemo(() => configOf(block), [block]);
  const { active, onMouseMove, onMouseLeave } = useActive();
  const { clickable, sel, pickAt } = usePick(block);
  const onClick = (st: { activeTooltipIndex?: number | string | null } | null | undefined) => {
    const i = st?.activeTooltipIndex;
    if (clickable && i !== null && i !== undefined && i !== "") pickAt(Number(i));
  };
  return (
    <ChartContainer config={config} className="aspect-auto w-full" style={{ height: block.labels.length * 34 * Math.max(1, block.series.length) + 8 }}>
      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: endPad(block), left: 0, bottom: 0 }} barCategoryGap={6} onMouseMove={onMouseMove} onMouseLeave={onMouseLeave} onClick={onClick} style={clickable ? { cursor: "pointer" } : undefined}>
        <defs>
          {block.series.map((s, i) => (
            <linearGradient key={i} id={`${id}-h${i}`} x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor={chartColor(s.tone, i)} stopOpacity={0.78} />
              <stop offset="100%" stopColor={chartColor(s.tone, i)} stopOpacity={1} />
            </linearGradient>
          ))}
        </defs>
        <XAxis type="number" hide domain={[0, headroom(block, 1.02)]} />
        <YAxis type="category" dataKey="label" tickLine={false} axisLine={false} width={labelWidth(block.labels)} tick={(p: { x?: number | string; y?: number | string; payload?: { value?: unknown } }) => <CategoryTick {...p} width={labelWidth(block.labels)} />} />
        <Tooltip cursor={false} content={(p) => (p.active && p.label !== undefined ? <Pop block={block} label={String(p.label)} /> : null)} />
        {block.series.map((s, i) => (
          <Bar key={i} dataKey={`s${i}`} name={s.name} fill={`url(#${id}-h${i})`} radius={4} barSize={20} isAnimationActive={intro}>
            {rows.map((_, ri) => (
              <Cell key={ri} fillOpacity={dimmed(sel, ri, active)} />
            ))}
            <LabelList dataKey={`s${i}`} content={(p) => <EndValue x={p.x} y={p.y} width={p.width} height={p.height} value={p.value} unit={block.unit} />} />
          </Bar>
        ))}
      </BarChart>
    </ChartContainer>
  );
}

/** Phễu: một số đo ⇒ một màu nhạt dần theo bước; cuối thanh: số + "N% bước trước". */
function Funnel({ block }: { block: ChartData }) {
  const steps = funnelSteps(block);
  const base = chartColor(block.series[0]?.tone, 0);
  const rows = steps.map((s) => ({ label: s.label, v: s.value }));
  return (
    <ChartContainer config={configOf(block)} className="aspect-auto w-full" style={{ height: block.labels.length * 40 + 8 }}>
      <BarChart data={rows} layout="vertical" margin={{ top: 0, right: endPad(block, 14), left: 0, bottom: 0 }} barCategoryGap={8}>
        <XAxis type="number" hide domain={[0, headroom(block, 1.02)]} />
        <YAxis type="category" dataKey="label" tickLine={false} axisLine={false} width={labelWidth(block.labels)} tick={(p: { x?: number | string; y?: number | string; payload?: { value?: unknown } }) => <CategoryTick {...p} width={labelWidth(block.labels)} />} />
        <Bar dataKey="v" radius={5} barSize={24} isAnimationActive>
          {steps.map((_, i) => (
            <Cell key={i} fill={base} fillOpacity={Math.max(0.3, 1 - i * 0.16)} />
          ))}
          <LabelList
            dataKey="v"
            content={(p) => {
              const i = Number(p.index ?? -1);
              const step = steps[i];
              if (!step || step.value === null) return null;
              const x = Number(p.x ?? 0) + Number(p.width ?? 0) + 8;
              const y = Number(p.y ?? 0) + Number(p.height ?? 0) / 2;
              return (
                <text x={x} y={y} dominantBaseline="central" fontSize={12} className="tnum">
                  <tspan fontWeight={500} fill="var(--foreground)">{chartValue(step.value, block.unit)}</tspan>
                  {step.pctPrev !== null ? <tspan dx={6} fill="var(--muted-foreground)" fontWeight={400}>{`${step.pctPrev}% bước trước`}</tspan> : null}
                </text>
              );
            }}
          />
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}

/** Bánh: giữa = tổng (hoặc miếng đang rê); chú thích lưới gọn: tên · số · %. */
function Donut({ block }: { block: ChartData }) {
  const intro = useIntro();
  const { slices, total } = donutSlices(block);
  const [hover, setHover] = useState<number | null>(null);
  const picked = hover === null ? null : (slices[hover] ?? null);
  const { clickable, sel, pickAt } = usePick(block);
  const faded = (i: number) => (sel >= 0 ? (slices[i]?.index === sel ? 1 : 0.3) : hover === null || hover === i ? 1 : 0.4);
  return (
    <div className="flex flex-wrap items-center gap-6">
      <div className="relative size-[168px] shrink-0">
        <PieChart width={168} height={168}>
          <Pie
            data={slices}
            dataKey="value"
            nameKey="label"
            innerRadius="64%"
            outerRadius="96%"
            paddingAngle={1.5}
            cornerRadius={5}
            stroke="none"
            startAngle={90}
            endAngle={-270}
            isAnimationActive={intro}
            onMouseEnter={(_, i) => setHover(i)}
            onMouseLeave={() => setHover(null)}
            onClick={(_, i) => { const sl = slices[i]; if (clickable && sl) pickAt(sl.index); }}
            style={clickable ? { cursor: "pointer" } : undefined}
          >
            {slices.map((s, i) => (
              <Cell key={s.index} fill={chartColor(undefined, s.index)} fillOpacity={faded(i)} />
            ))}
          </Pie>
        </PieChart>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
          <span className="t16 tnum">{chartValue(picked?.value ?? total, block.unit)}</span>
          <span className="t12 max-w-[100px] truncate text-muted-foreground">{picked?.label ?? "tổng"}</span>
        </div>
      </div>
      <dl className="grid grid-cols-[auto_auto_auto_auto] items-center gap-x-2.5 gap-y-2">
        {slices.map((s, i) => (
          <div key={s.index} className={cn("contents", clickable && "cursor-pointer")} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => clickable && pickAt(s.index)}>
            <span className="size-2 rounded-full" style={{ background: chartColor(undefined, s.index) }} aria-hidden />
            <dt className={cn("t12 max-w-[160px] truncate", hover === i && "font-medium")}>{s.label}</dt>
            <dd className="t12 tnum text-right text-muted-foreground">{chartValue(s.value, block.unit)}</dd>
            <dd className="t12m tnum text-right">{s.pct === null ? "—" : `${s.pct}%`}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
