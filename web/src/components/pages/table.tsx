"use client";

import { ChevronRight, Phone } from "lucide-react";
import { useState } from "react";
import * as fmt from "@/lib/format";
import type { Column, Json, Kpi, Row, TablePage } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Pill, Spinner } from "@/components/primitives";
import { useDesk } from "@/components/desk/desk-context";
import { EditButton } from "./edit-block";
import { WhyButton } from "./why-button";
import { ChartCard } from "@/components/blocks/chart";

export function KpiRow({ kpis: all, inset = false, onChanged }: { kpis: Kpi[]; inset?: boolean; onChanged?: () => void }) {
  const desk = useDesk();
  const kpis = fmt.shownKpis(all);   // chưa có gì thì ẩn: ô 0 / trống không vẽ, cả hàng 0 thì không có hàng
  if (!kpis.length) return null;
  return (
    // một ô số đứng riêng thì gọn (tối đa 320px — cả hàng cho một con số trông trống, rà 25/11); ≤ 6 ô thì giãn đều cùng một hàng khi đủ rộng (không để ô thứ năm rơi xuống hàng riêng — rà UI 18/11);
    // hẹp (điện thoại) vẫn ÍT NHẤT hai cột — ô tối thiểu là min(150px, nửa hàng), không để mỗi ô một hàng đẩy bảng xuống mấy màn (rà bố cục 18/11)
    // hai cột (thẻ hẹp hơn chỗ cho ba ô) mà số ô lẻ ⇒ ô cuối trải hết hàng, không để nửa hàng trống
    <div className="@container flex min-w-0 flex-col">
    <div className={cn("grid flex-1 gap-2.5", kpis.length === 1 ? "grid-cols-[minmax(0,320px)]" : kpis.length <= 6
      ? "grid-cols-[repeat(auto-fit,minmax(min(150px,calc(50%-5px)),1fr))] @max-[469px]:[&>*:last-child:nth-child(odd)]:col-span-2"
      : "grid-cols-[repeat(auto-fill,minmax(min(180px,calc(50%-5px)),1fr))] @max-[559px]:[&>*:last-child:nth-child(odd)]:col-span-2")}>
      {kpis.map((k) => {
        const box = cn("flex min-w-0 flex-col gap-1 rounded-xl p-3 text-left sm:p-3.5", inset ? "bg-surface2" : "border border-hairline bg-surface");
        const body = (
          <>
            <span className="t12 flex items-center gap-1 text-muted-foreground">
              <span className="line-clamp-2 [overflow-wrap:anywhere]" title={k.label}>{k.label}</span>
              {k.drill ? <ChevronRight className="size-3 shrink-0 opacity-60" aria-hidden /> : null}
            </span>
            <span className={cn("t20 tnum [overflow-wrap:anywhere]", fmt.toneClass(k.tone))}>{fmt.kpi(k)}</span>
            {k.hint ? <span className="t12 line-clamp-2 text-muted-foreground/80">{k.hint}</span> : null}
          </>
        );
        // nguồn gốc con số (0097): bấm ô số ⇒ mở đúng các dòng tạo nên nó, bằng quyền người xem
        const cell = k.drill ? (
          <button
            key={k.label}
            type="button"
            className={cn(box, "outline-none hover:brightness-95 focus-visible:ring-2 focus-visible:ring-ring")}
            title={`${k.hint ? k.hint + " · " : ""}Bấm để xem các dòng tạo nên con số này`}
            onClick={() => void desk.openTarget(k.drill!.query, k.drill!.input, k.label)}
          >
            {body}
          </button>
        ) : (
          <div key={k.label} className={box} title={k.hint || undefined}>
            {body}
          </div>
        );
        // "Vì sao?" (0220 lát 7): nút riêng ở góc dưới ô số (không lồng trong nút mở dòng gốc)
        const withWhy = k.why ? (
          <div key={k.label} className="relative flex flex-col [&>*:first-child]:flex-1">
            {cell}
            <WhyButton prompt={k.why} className="absolute bottom-2 right-2" />
          </div>
        ) : cell;
        // app tự ghép (0098): ô số có nút "Sửa khối này"
        return k.edit ? <div key={k.label} className="group relative flex flex-col [&>*]:flex-1">{withWhy}<EditButton edit={k.edit} onChanged={onChanged} /></div> : withWhy;
      })}
    </div>
    </div>
  );
}

const numeric = (c: Column) => c.format === "money" || c.format === "number" || c.format === "percent";

function Cell({ c, v }: { c: Column; v: Json | undefined }) {
  const t = fmt.cell(v, c.format, c.labels);
  switch (c.format) {
    case "badge":
      return <Pill text={t} tone={c.tones?.[fmt.text(v)]} />;
    case "money":
    case "number":
    case "percent": {
      const neg = (fmt.toNumber(v) ?? 0) < 0;
      return <span className={cn("tnum block text-right", neg && "text-bad")}>{t}</span>;
    }
    case "phone":
      // số đã che (lead chờ bấm nhận — 0214): chữ thường, không thành link gọi một số cụt
      if (t.includes("•")) return <span className="tnum whitespace-nowrap text-muted-foreground" title="Bấm Nhận lead để xem đủ số">{t}</span>;
      return t ? (
        <a href={`tel:${fmt.text(v).replace(/[^\d+]/g, "")}`} className="tnum inline-flex items-center gap-1 whitespace-nowrap hover:underline" onClick={(e) => e.stopPropagation()}>
          <Phone className="size-3 text-muted-foreground" aria-hidden />
          {t}
        </a>
      ) : null;
    case "date":
    case "datetime":
      return <span className="tnum whitespace-nowrap" title={t}>{t}</span>;
    default:
      return <span className="line-clamp-2" title={t}>{t}</span>;
  }
}

/** Bảng dữ liệu: server mô tả cột + định dạng. Chọn dòng ⇒ thanh thao tác của màn; nhấp đúp / Enter ⇒ mở chi tiết. */
export function DataTable({
  page,
  selected,
  onSelect,
  onOpen,
  onActivate,
  onMore,
}: {
  page: TablePage;
  selected?: string | null;
  onSelect?: (id: string | null) => void;
  onOpen?: (row: Row) => void;
  /** Bấm MỘT lần (màn khai `row_click`): chọn dòng rồi mở luôn. ⌘ / Ctrl / Shift-bấm hoặc phím cách: chỉ chọn / bỏ chọn như cũ. */
  onActivate?: (row: Row) => void;
  /** Bảng có trang phía server (`page.paging`): tải trang kế từ server. */
  onMore?: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  // Vẽ dần (đo 18/11/2026: màn 1.000–2.000 dòng × 10+ cột = hàng chục nghìn ô DOM, trình duyệt khựng vài giây lúc mở). Dữ liệu vẫn đủ (ô số, xuất
  // Excel, tìm của màn dùng cả tập); chỉ phần VẼ theo từng mẻ. Trang mới (lọc lại) ⇒ về mẻ đầu.
  const [shown, setShown] = useState({ page, n: ROWS_STEP });
  const n = shown.page === page ? shown.n : ROWS_STEP;
  // Trang phía server (`paging`): mỗi lần server gửi một trang ⇒ vẽ hết dòng đã có, "Hiện thêm" xin trang kế; không có ⇒ vẽ dần dòng đã có sẵn.
  const server = page.paging && onMore ? page.paging : null;
  const rows = server ? page.rows : page.rows.length > n ? page.rows.slice(0, n) : page.rows;
  const rest = server ? Math.max(0, server.total - page.rows.length) : page.rows.length - rows.length;
  const step = server ? server.limit : ROWS_STEP;
  async function showMore() {
    if (!server || !onMore) {
      setShown({ page, n: n + ROWS_STEP });
      return;
    }
    setBusy(true);
    try {
      await onMore();
    } finally {
      setBusy(false);
    }
  }
  const columns = fmt.shownColumns(page.columns, page.rows);   // cột trống ở mọi dòng ⇒ ẩn
  return (
    <div className="overflow-auto">
      <table className="t14 w-full min-w-max border-separate border-spacing-0">
        <thead className="sticky top-0 z-10 bg-surface">
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={cn("t12m border-b border-border px-3 py-2 text-left text-muted-foreground", numeric(c) && "text-right")}>
                {c.label}
              </th>
            ))}
            {onOpen ? <th className="w-8 border-b border-border" aria-hidden /> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const on = selected === row.id;
            return (
              <tr
                key={row.id}
                tabIndex={onSelect ? 0 : undefined}
                aria-selected={onSelect ? on : undefined}
                onClick={(e) => {
                  if (onActivate && !(e.metaKey || e.ctrlKey || e.shiftKey)) {
                    onSelect?.(row.id);   // vẫn chọn: thanh thao tác của dòng còn đó sau khi đóng chi tiết
                    onActivate(row);
                  } else onSelect?.(on ? null : row.id);
                }}
                onDoubleClick={() => !onActivate && onOpen?.(row)}   // bấm-một-lần đã mở ⇒ bấm đúp không mở lần hai
                onKeyDown={(e) => {
                  if (e.key === "Enter" && onOpen) onOpen(row);
                  if (e.key === " " && onSelect) {
                    e.preventDefault();
                    onSelect(on ? null : row.id);
                  }
                }}
                className={cn("outline-none focus-visible:bg-accent", onActivate ? "cursor-pointer" : onSelect && "cursor-default", on ? "bg-primary/10" : "hover:bg-accent/60")}
              >
                {columns.map((c) => (
                  <td key={c.key} className={cn("border-b border-border/60 px-3 py-1.5 align-top", !c.width && "whitespace-nowrap")} style={c.width ? { minWidth: Math.min(c.width, 240) } : undefined}>
                    <Cell c={c} v={row.values[c.key]} />
                  </td>
                ))}
                {onOpen ? (
                  <td className="border-b border-border/60 px-1 align-middle">
                    <button type="button" aria-label="Mở chi tiết" className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" onClick={(e) => { e.stopPropagation(); onOpen(row); }}>
                      <ChevronRight className="size-4" />
                    </button>
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
      {rest > 0 ? (
        <button type="button" disabled={busy} onClick={() => void showMore()} className="t14m mx-auto my-2 flex items-center gap-2 rounded-lg px-3 py-1.5 text-primary hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60">
          {busy ? <Spinner className="size-4" /> : null}
          Hiện thêm {Math.min(rest, step).toLocaleString("vi-VN")} dòng (còn {rest.toLocaleString("vi-VN")})
        </button>
      ) : null}
    </div>
  );
}

const ROWS_STEP = 300;

/** Biểu đồ server gửi kèm ĐẦU bảng (`header` — vd bảng tổng hợp một chiều của tập dữ liệu, 0220 lát 5): vẽ trên bảng, ở chat lẫn màn đầy đủ. */
export function HeaderCharts({ page }: { page: TablePage }) {
  const charts = (page.header ?? []).flatMap((b) => (b.type === "chart" ? [b] : []));
  if (!charts.length) return null;
  return <>{charts.map((b, i) => <ChartCard key={i} block={b} />)}</>;
}
