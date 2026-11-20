"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Pill, ErrorLine } from "@/components/primitives";
import { saveBlob } from "@/lib/download";
import {
  columnLabel,
  confidence,
  dateOrders,
  DATE_WHY,
  importButton,
  initialMapping,
  matchOf,
  mergePicks,
  missingRequired,
  planOf,
  setColumn,
  suggestionsToApply,
  summaryLine,
  type Mapping,
  type PickItem,
} from "@/lib/import-plan";
import type { ImportAnalysis, ImportPlanReport } from "@/lib/types";
import { PlanImportPreview } from "./plan-import-preview";

// Nhập Excel TỰ KHỚP (ADR 0226). Máy đoán, người xác nhận: thả file → màn khớp cột (mỗi trường ← một cột, kèm độ tin cậy) → xem trước kết quả (thêm mới /
// cập nhật / trùng / lỗi theo từng lý do) → nhập. Mọi việc đoán / chuẩn hoá / chạy lệnh ở server; ở đây chỉ giữ lựa chọn của người.

type Step = "file" | "map" | "preview" | "done";

const BTN = "t14m h-8 rounded-lg border border-border bg-surface px-3 hover:bg-accent disabled:opacity-50";
const PRIMARY = "t14m h-8 rounded-lg bg-primary px-3 text-primary-foreground hover:bg-primary/90 disabled:opacity-50";
const SELECT = "t14 h-8 min-w-0 rounded-lg border border-border bg-surface px-2";
const ACTION: Record<string, { text: string; tone: string }> = {
  create: { text: "Thêm mới", tone: "green" },
  update: { text: "Cập nhật", tone: "blue" },
  skip: { text: "Đã có — bỏ qua", tone: "gray" },
};

export function ImportWizard({
  command: initialCommand,
  title: initialTitle,
  onDone,
  onClose,
  onLegacy,
  preset,
}: {
  command: string;
  title: string;
  /** Lệnh nhập cả bảng: giá trị cấp lệnh của nút đã bấm (vd kế hoạch đích) — gửi kèm mọi lần chạy thử / nhập. */
  preset?: Record<string, unknown>;
  onDone?: () => void;
  onClose: () => void;
  /** Lệnh này đọc file theo mẫu riêng của công ty (không tự khớp) ⇒ chuyển sang cách nhập cũ với đúng file đã chọn. */
  onLegacy: (file: File) => void;
}) {
  const [command, setCommand] = useState(initialCommand);
  const [title, setTitle] = useState(initialTitle);
  const [step, setStep] = useState<Step>("file");
  const [file, setFile] = useState<File | null>(null);
  const [a, setA] = useState<ImportAnalysis | null>(null);
  const [mapping, setMapping] = useState<Mapping>({});
  const [chosenOrder, setChosenOrder] = useState<Record<string, string>>({});
  const [resolutions, setResolutions] = useState<Record<string, Record<string, string>>>({});
  const [overwrite, setOverwrite] = useState(false);
  const [remember, setRemember] = useState(true);
  const [report, setReport] = useState<ImportPlanReport | null>(null);
  const [planTitle, setPlanTitle] = useState("");
  const fixed = useMemo(() => Object.fromEntries(Object.entries(preset ?? {}).filter(([, v]) => typeof v === "string" && v !== "") as [string, string][]), [preset]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [allFields, setAllFields] = useState(false);
  const [picks, setPicks] = useState<Record<string, PickItem>>({});
  const suggested = useRef<Set<string>>(new Set());
  const input = useRef<HTMLInputElement>(null);

  const analyze = useCallback(
    async (f: File, cmd: string, sheet?: string) => {
      setBusy(true);
      setError(null);
      try {
        const r = await api.importAnalyze(cmd, f, sheet);
        if (!r.auto) {
          onLegacy(f);
          return;
        }
        setA(r);
        setPlanTitle((cur) => cur || r.heading || "");
        setMapping(initialMapping(r));
        setResolutions(r.resolutions ?? {});
        setChosenOrder({});
        setStep("map");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Không đọc được file");
      } finally {
        setBusy(false);
      }
    },
    [onLegacy],
  );

  function pick(f: File | null | undefined) {
    if (!f) return;
    setFile(f);
    setReport(null);
    void analyze(f, command, undefined);
  }

  const plan = useCallback(
    (res: Record<string, Record<string, string>>, ow: boolean, rem: boolean) =>
      planOf(a!, mapping, {
        resolutions: res,
        dateOrders: dateOrders(a?.dates ?? [], chosenOrder),
        overwrite: ow,
        remember: rem,
        // kế hoạch mới (không chọn kế hoạch đích) ⇒ gửi tên người đã xác nhận ở bản xem trước
        preset: a?.table && !fixed.plan_id && planTitle.trim() ? { ...fixed, plan_title: planTitle.trim() } : fixed,
      }),
    [a, mapping, chosenOrder, planTitle, fixed],
  );

  const preview = useCallback(
    async (res: Record<string, Record<string, string>>, ow: boolean) => {
      if (!a || !file) return;
      setBusy(true);
      setError(null);
      try {
        const r = await api.importPlan(command, file, plan(res, ow, remember), true);
        setPicks((cur) => mergePicks(cur, r.unresolved));
        setReport(r);
        setStep("preview");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Không chạy thử được");
      } finally {
        setBusy(false);
      }
    },
    [a, file, command, plan, remember],
  );

  // bố cục đã quen (đúng chữ ký tiêu đề) và đủ trường bắt buộc ⇒ khỏi bắt khớp lại: sang thẳng bản xem trước, người chỉ còn bấm Nhập
  const autoRan = useRef<string | null>(null);
  useEffect(() => {
    if (step !== "map" || !a?.layout?.exact || missingRequired(a, mapping).length || autoRan.current === a.signature + a.sheet) return;
    autoRan.current = a.signature + a.sheet;
    void preview(resolutions, overwrite);
  }, [step, a, mapping, resolutions, overwrite, preview]);

  // gợi ý của máy (chữ gần giống một lựa chọn) điền sẵn vào ô chọn — người xem lại, đổi được; không đoán được thì để trống chờ người chọn
  useEffect(() => {
    if (step !== "preview" || !report || busy) return;
    const next = suggestionsToApply(report.unresolved, resolutions, suggested.current);
    if (next) {
      setResolutions(next);
      void preview(next, overwrite);
    }
  }, [step, report, busy, resolutions, overwrite, preview]);

  async function commit() {
    if (!a || !file) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.importPlan(command, file, plan(resolutions, overwrite, remember), false);
      setReport(r);
      setStep("done");
      onDone?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không nhập được");
    } finally {
      setBusy(false);
    }
  }

  async function repick(resolutionsNext: Record<string, Record<string, string>>, ow: boolean) {
    setResolutions(resolutionsNext);
    setOverwrite(ow);
    await preview(resolutionsNext, ow);
  }

  const missing = a ? missingRequired(a, mapping) : [];
  const labelOf = (key: string) => a?.fields.find((f) => f.key === key)?.label ?? key;
  const sheet = a?.sheets.find((s) => s.name === a.sheet);
  const shownFields = a ? a.fields.filter((f) => allFields || mapping[f.key] != null || f.required) : [];
  const other = a?.sheets.find((s) => s.suggest && !s.suggest.same && s.name === a.sheet);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      {step === "file" ? (
        <div
          role="button"
          tabIndex={0}
          aria-label="Thả file Excel hoặc bấm để chọn"
          onClick={() => input.current?.click()}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            pick(e.dataTransfer.files?.[0]);
          }}
          className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed px-6 py-10 text-center ${drag ? "border-primary bg-accent" : "border-border bg-surface"}`}
        >
          <span className="t16">{busy ? "Đang đọc file…" : "Thả file Excel vào đây"}</span>
          <span className="t12 text-muted-foreground">
            hoặc bấm để chọn (.xlsx, .csv). File không cần đúng mẫu — hệ thống tự tìm dòng tiêu đề và khớp cột, bạn chỉ xác nhận.
          </span>
        </div>
      ) : null}
      <input
        ref={input}
        type="file"
        accept=".xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"
        className="sr-only"
        aria-label="Chọn file Excel để nhập"
        onChange={(e) => {
          pick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {step === "file" ? (
        <button
          type="button"
          className="t12 self-start text-muted-foreground underline"
          onClick={async () => {
            try {
              saveBlob(await api.template(command), `Mẫu nhập - ${title}.xlsx`);
            } catch (e) {
              setError(e instanceof Error ? e.message : "Không tải được mẫu");
            }
          }}
        >
          Chưa có file? Tải mẫu Excel
        </button>
      ) : null}
      {error ? <ErrorLine>{error}</ErrorLine> : null}

      {step === "map" && a ? (
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
          <div className="flex flex-wrap items-center gap-2">
            <span className="t14m">{file?.name}</span>
            {a.sheets.length > 1 ? (
              <select
                aria-label="Chọn trang tính"
                className={SELECT}
                value={a.sheet}
                onChange={(e) => file && void analyze(file, command, e.target.value)}
              >
                {a.sheets.map((s) => (
                  <option key={s.name} value={s.name}>
                    Trang “{s.name}” · {s.rows} dòng{s.suggest && !s.suggest.same ? ` · hợp “${s.suggest.title}”` : ""}
                  </option>
                ))}
              </select>
            ) : null}
            <span className="t12 text-muted-foreground">
              Tiêu đề ở dòng {a.header_line} · {a.rows} dòng dữ liệu
            </span>
            {a.layout?.exact ? <Pill text={`Bố cục đã nhập ${a.layout.uses ?? 1} lần trước`} tone="green" /> : a.layout ? <Pill text="Có cột đã quen" tone="green" /> : null}
            {a.ai.state === "used" && (a.ai.matched ?? 0) > 0 ? <Pill text={`Máy đoán thêm ${a.ai.matched} cột`} tone="purple" /> : null}
          </div>
          {other?.suggest ? (
            <div className="t12 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-accent px-3 py-2">
              Trang này giống dữ liệu của “{other.suggest.title}” hơn “{title}”.
              <button
                type="button"
                className={BTN}
                onClick={() => {
                  setCommand(other.suggest!.command);
                  setTitle(other.suggest!.title);
                  if (file) void analyze(file, other.suggest!.command, a.sheet);
                }}
              >
                Nhập vào “{other.suggest.title}”
              </button>
            </div>
          ) : null}

          {a.dates.map((d) => (
            <div key={d.field} className="t12 flex flex-wrap items-center gap-2 rounded-lg border border-border px-3 py-2">
              <span className="t14m">{d.label}:</span>
              <span className="inline-flex overflow-hidden rounded-lg border border-border" role="group" aria-label={`Cách đọc ngày của ${d.label}`}>
                {(["dmy", "mdy"] as const).map((o) => {
                  const on = (chosenOrder[d.field] ?? d.order) === o;
                  return (
                    <button
                      key={o}
                      type="button"
                      aria-pressed={on}
                      className={`t12m px-2 py-1 ${on ? "bg-primary text-primary-foreground" : "bg-surface hover:bg-accent"}`}
                      onClick={() => setChosenOrder({ ...chosenOrder, [d.field]: o })}
                    >
                      {o === "dmy" ? "ngày/tháng" : "tháng/ngày"}
                    </button>
                  );
                })}
              </span>
              <span>{d.examples[chosenOrder[d.field] ?? d.order] ?? "ví dụ này không hợp lệ theo cách đọc đang chọn"}</span>
              <span className="text-muted-foreground">({chosenOrder[d.field] ? DATE_WHY.user : DATE_WHY[d.decided]})</span>
            </div>
          ))}

          <ul className="divide-y divide-border rounded-lg border border-border" aria-label="Khớp cột">
            {shownFields.map((f) => {
              const col = mapping[f.key] ?? null;
              const c = confidence(matchOf(a, mapping, f.key));
              const m = matchOf(a, mapping, f.key);
              const miss = f.required && col === null;
              const column = col !== null ? a.columns.find((x) => x.index === col) : undefined;
              return (
                <li key={f.key} className={`grid grid-cols-[minmax(0,1.1fr)_minmax(0,1.4fr)_auto] items-center gap-3 px-3 py-2 ${miss ? "bg-[color-mix(in_oklab,var(--bad)_8%,transparent)]" : ""}`}>
                  <span className="t14 min-w-0 truncate">
                    {f.label}
                    {f.required ? <span className="text-destructive"> *</span> : null}
                  </span>
                  <span className="flex min-w-0 flex-col gap-0.5">
                    <select
                      aria-label={`Cột cho ${f.label}`}
                      className={`${SELECT} ${miss ? "border-destructive" : ""}`}
                      value={col ?? ""}
                      onChange={(e) => setMapping(setColumn(mapping, f.key, e.target.value === "" ? null : Number(e.target.value)))}
                    >
                      <option value="">{miss ? "— chọn cột (bắt buộc) —" : "— không nhập —"}</option>
                      {a.columns
                        .filter((x) => !x.ignore || x.index === col)
                        .map((x) => (
                          <option key={x.index} value={x.index}>
                            {columnLabel(x)}
                          </option>
                        ))}
                    </select>
                    {column?.samples.length ? <span className="t12 truncate text-muted-foreground">VD: {column.samples.slice(0, 2).join(" · ")}</span> : null}
                  </span>
                  <span className="flex w-28 justify-end" title={m?.why ?? ""}>
                    {col !== null ? <Pill text={c.text} tone={c.tone} /> : miss ? <Pill text="Còn thiếu" tone="red" /> : null}
                  </span>
                </li>
              );
            })}
          </ul>

          {a.fields.some((f) => mapping[f.key] == null && !f.required) ? (
            <button type="button" className="t12 self-start text-muted-foreground underline" onClick={() => setAllFields(!allFields)}>
              {allFields ? "Ẩn các trường không có cột" : `Còn ${a.fields.filter((f) => mapping[f.key] == null && !f.required).length} trường khác chưa có cột — bấm để tự chọn cột`}
            </button>
          ) : null}
          {a.columns.some((c) => mapping && !Object.values(mapping).includes(c.index) && !c.ignore) ? (
            <p className="t12 text-muted-foreground">
              Cột không dùng:{" "}
              {a.columns
                .filter((c) => !Object.values(mapping).includes(c.index) && !c.ignore)
                .map((c) => c.header)
                .join(", ")}
            </p>
          ) : null}
          {a.skipped_count ? (
            <p className="t12 text-muted-foreground">
              Bỏ qua {a.skipped_count} dòng không phải dữ liệu: {a.skipped.slice(0, 4).map((s) => `dòng ${s.line} (${s.reason})`).join(", ")}
              {a.skipped_count > 4 ? "…" : ""}
            </p>
          ) : null}
          {missing.length ? <p className="t12 text-destructive">Còn thiếu: {missing.map(labelOf).join(", ")} — chọn cột tương ứng để tiếp tục.</p> : null}
          {sheet && sheet.rows === 0 ? <p className="t12 text-destructive">Trang này không có dòng dữ liệu.</p> : null}
        </div>
      ) : null}

      {step === "preview" && report ? (
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
          {report.plan_view ? (
            <PlanImportPreview report={report} title={planTitle} onTitle={setPlanTitle} />
          ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Pill text={`${report.summary.create} thêm mới`} tone="green" />
            {report.summary.update ? <Pill text={`${report.summary.update} cập nhật`} tone="blue" /> : null}
            {report.summary.waiting ? <Pill text={`${report.summary.waiting} chờ chọn giá trị`} tone="orange" /> : null}
            {report.summary.skip ? <Pill text={`${report.summary.skip} đã có — bỏ qua`} tone="gray" /> : null}
            {report.summary.failed ? <Pill text={`${report.summary.failed} lỗi`} tone="red" /> : null}
            {report.summary.ignored ? <span className="t12 text-muted-foreground">{report.summary.ignored} dòng không phải dữ liệu đã bỏ qua</span> : null}
          </div>
          )}

          {Object.values(picks).length ? (
            <div className="flex flex-col gap-1 rounded-lg border border-border px-3 py-2">
              <span className="t14m">Giá trị trong file chưa khớp danh sách — chọn một lần cho mỗi giá trị, áp cho mọi dòng</span>
              {Object.values(picks).map((v) => {
                const chosen = resolutions[v.field]?.[v.key] ?? "";
                return (
                  <label key={`${v.field}|${v.key}`} className="t12 flex items-center gap-2">
                    <span className="w-56 min-w-0 truncate">
                      {v.label}: “{v.raw}” <span className="text-muted-foreground">({v.count} dòng)</span>
                    </span>
                    <select
                      aria-label={`Chọn cho ${v.raw}`}
                      className={`${SELECT} ${chosen === "" ? "border-warn" : ""}`}
                      value={chosen}
                      onChange={(e) => {
                        const next = { ...resolutions, [v.field]: { ...(resolutions[v.field] ?? {}), [v.key]: e.target.value } };
                        if (e.target.value === "") delete next[v.field][v.key];
                        void repick(next, overwrite);
                      }}
                    >
                      <option value="">— chọn —</option>
                      {v.options.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                      <option value="__blank__">(bỏ trống ô này)</option>
                    </select>
                    {chosen !== "" && chosen === v.suggest ? <span className="text-muted-foreground">gợi ý của máy — đổi nếu chưa đúng</span> : null}
                  </label>
                );
              })}
            </div>
          ) : null}

          {report.key?.update ? (
            <label className="t12 flex items-center gap-2">
              <input type="checkbox" checked={overwrite} onChange={(e) => void repick(resolutions, e.target.checked)} />
              Ghi đè cả ô đã có dữ liệu (mặc định chỉ điền ô đang trống, không đụng dữ liệu đang có)
            </label>
          ) : null}

          {report.reasons.length ? (
            <div className="flex flex-col gap-1 rounded-lg border border-border px-3 py-2">
              <span className="t14m">Lỗi theo lý do</span>
              {report.reasons.map((r) => (
                <span key={r.reason} className="t12 flex gap-2">
                  <Pill text={`${r.count} dòng`} tone="red" />
                  <span className="select-text">
                    {r.reason} <span className="text-muted-foreground">(dòng {r.lines.join(", ")}{r.count > r.lines.length ? "…" : ""})</span>
                  </span>
                </span>
              ))}
            </div>
          ) : null}
          {report.skips.map((g) => (
            <details key={g.reason} className="t12 rounded-lg border border-border px-3 py-2">
              <summary className="flex cursor-pointer items-center gap-2">
                <Pill text={`${g.count} dòng`} tone="gray" />
                <span>{g.reason} — bỏ qua</span>
              </summary>
              <ul className="mt-1 flex max-h-40 flex-col gap-0.5 overflow-y-auto text-muted-foreground">
                {g.rows.map((r) => (
                  <li key={r.line} className="select-text">
                    Dòng {r.line}
                    {r.who ? ` · ${r.who}` : ""}
                    {r.note ? ` — ${r.note}` : ""}
                  </li>
                ))}
              </ul>
            </details>
          ))}
          {report.rows.filter((r) => r.action === "update").slice(0, 3).map((r) => (
            <div key={r.line} className="t12 rounded-lg border border-border px-3 py-2">
              <span className="t14m">Dòng {r.line} — cập nhật:</span>{" "}
              {(r.diff ?? []).map((d) => `${d.field}: ${d.old ?? "(trống)"} → ${String(d.new)}${d.applied ? "" : " (giữ nguyên)"}`).join("; ")}
            </div>
          ))}

          {report.nothing ? <p className="t14 rounded-lg border border-border bg-accent px-3 py-2">{report.nothing}</p> : null}
          {report.plan_view ? null : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="t12 w-full">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="px-2 py-1">Dòng</th>
                  <th className="px-2 py-1">Việc</th>
                  {(report.preview[0]?.values ?? []).map((v) => (
                    <th key={v.label} className="px-2 py-1">
                      {v.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {report.preview.map((r) => {
                  const act = ACTION[r.action] ?? ACTION.create;
                  return (
                    <tr key={r.line} className={r.status !== "ok" && r.status !== "pending_approval" && r.status !== "waiting" && r.action !== "skip" ? "text-destructive" : ""}>
                      <td className="px-2 py-1">{r.line}</td>
                      <td className="px-2 py-1">
                        {r.status === "waiting" ? (
                          <Pill text="Chờ chọn" tone="orange" />
                        ) : (
                          <Pill text={r.status === "ok" || r.status === "pending_approval" || r.action === "skip" ? act.text : "Lỗi"} tone={r.status === "ok" || r.action === "skip" ? act.tone : "red"} />
                        )}
                      </td>
                      {r.values.map((v) => (
                        <td key={v.label} className="max-w-40 truncate px-2 py-1">
                          {v.text}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          )}
          <label className="t12 flex items-center gap-2">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            Nhớ cách khớp này — lần sau file cùng bố cục khỏi khớp lại (sửa / xoá ở Cài đặt → Cách khớp cột nhập Excel)
          </label>
        </div>
      ) : null}

      {step === "done" && report ? (
        <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface px-4 py-5">
          <span className="t16">Đã nhập xong</span>
          <span className="t14">{report.plan_view ? report.plan_view.summary : summaryLine(report.summary)}</span>
          {report.reasons.map((r) => (
            <span key={r.reason} className="t12 text-destructive">
              {r.count} dòng lỗi: {r.reason} (dòng {r.lines.join(", ")})
            </span>
          ))}
          <span className="t12 text-muted-foreground">Nhập lại cùng file không nhân đôi; dòng lỗi sửa trong file rồi nhập lại cả file chỉ chạy những dòng chưa vào.</span>
        </div>
      ) : null}

      <div className="flex justify-end gap-2">
        {step === "preview" ? (
          <button type="button" className={BTN} disabled={busy} onClick={() => setStep("map")}>
            Sửa cột
          </button>
        ) : null}
        {step === "map" || step === "preview" ? (
          <button
            type="button"
            className={BTN}
            disabled={busy}
            onClick={() => {
              setStep("file");
              setA(null);
              setReport(null);
              setFile(null);
              autoRan.current = null;
            }}
          >
            Chọn file khác
          </button>
        ) : null}
        <button type="button" className={BTN} onClick={onClose}>
          {step === "done" ? "Xong" : "Đóng"}
        </button>
        {step === "map" ? (
          <button type="button" className={PRIMARY} disabled={busy || missing.length > 0 || !a?.rows} onClick={() => void preview(resolutions, overwrite)}>
            {busy ? "Đang kiểm…" : "Đúng rồi, tiếp tục"}
          </button>
        ) : null}
        {step === "preview" ? (
          <button
            type="button"
            className={PRIMARY}
            disabled={busy || !report || report.summary.create + report.summary.update === 0 || report.unresolved.length > 0}
            onClick={() => void commit()}
          >
            {report?.plan_view ? (report.summary.create + report.summary.update ? `Nhập kế hoạch (${report.summary.create + report.summary.update} việc)` : "Không có gì để nhập") : importButton(report)}
          </button>
        ) : null}
      </div>
    </div>
  );
}
