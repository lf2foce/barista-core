"use client";

import { useMemo } from "react";
import { TimelineBlock } from "@/components/blocks/timeline";
import { Pill } from "@/components/primitives";
import { decodeTimeline } from "@/lib/decode";
import type { ImportPlanReport, PlanImportView } from "@/lib/types";

// Bản xem trước khi nhập KẾ HOẠCH từ Excel (ADR 0222 §15/11): kế hoạch sẽ trông thế nào SAU khi nhập — Gantt đúng khối của thẻ kế hoạch (server dựng: giai đoạn, mũi tên
// việc trước, ngày tính từ số ngày, mốc, ngày nghỉ), việc nào thêm / đổi / giữ nguyên, lưu ý, việc không còn trong file. Chỉ vẽ; mọi phép hiểu file làm ở server.

const ACTION: Record<
  PlanImportView["tasks"][number]["action"],
  { text: string; tone: string }
> = {
  create: { text: "Thêm mới", tone: "green" },
  update: { text: "Cập nhật", tone: "blue" },
  skip: { text: "Giữ nguyên", tone: "gray" },
  invalid: { text: "Lỗi", tone: "red" },
};

const dm = (iso: string | null) =>
  iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}` : "—";
const INPUT =
  "t14 h-8 min-w-0 flex-1 rounded-lg border border-border bg-surface px-2";

export function PlanImportPreview({
  report,
  title,
  onTitle,
}: {
  report: ImportPlanReport;
  title: string;
  onTitle: (t: string) => void;
}) {
  const view = report.plan_view;
  const rawTimeline = view?.timeline;
  const gantt = useMemo(() => decodeTimeline(rawTimeline), [rawTimeline]); // khối server mô tả — giải mã như mọi khối timeline (thêm mặc định cho trường thiếu)
  if (!view) return null;
  const s = report.summary;
  return (
    <div className="flex flex-col gap-3">
      {view.target.new ? (
        <label className="flex items-center gap-2">
          <span className="t14m shrink-0">Kế hoạch mới:</span>
          <input
            aria-label="Tên kế hoạch mới"
            className={INPUT}
            value={title}
            maxLength={200}
            onChange={(e) => onTitle(e.target.value)}
            placeholder="Tên kế hoạch"
          />
        </label>
      ) : (
        <p className="t14">
          <span className="t14m">Cập nhật kế hoạch:</span> {view.target.title}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {s.create ? <Pill text={`${s.create} việc mới`} tone="green" /> : null}
        {s.update ? (
          <Pill text={`${s.update} việc cập nhật`} tone="blue" />
        ) : null}
        {s.skip ? <Pill text={`${s.skip} giữ nguyên`} tone="gray" /> : null}
        {s.failed ? <Pill text={`${s.failed} lỗi`} tone="red" /> : null}
        {s.waiting ? (
          <Pill text={`${s.waiting} chờ chọn người`} tone="orange" />
        ) : null}
        {view.phases.length ? (
          <span className="t12 text-muted-foreground">
            {view.phases.length} giai đoạn ·{" "}
            {view.phases.map((p) => `${p.title} (${p.count})`).join(", ")}
          </span>
        ) : null}
      </div>
      <p
        className="t14 select-text rounded-lg border border-border bg-accent px-3 py-2"
        data-testid="plan-summary"
      >
        {view.summary}
      </p>
      {report.notes?.length ? (
        <ul className="t12 flex list-disc flex-col gap-0.5 pl-5 text-muted-foreground">
          {report.notes.map((n) => (
            <li key={n} className="select-text">
              {n}
            </li>
          ))}
        </ul>
      ) : null}

      {gantt ? (
        <div
          className="max-h-[360px] overflow-auto rounded-xl"
          aria-label="Kế hoạch sau khi nhập"
        >
          <TimelineBlock data={gantt} onOpen={() => undefined} />
        </div>
      ) : null}

      {view.missing.length ? (
        <p className="t12 rounded-lg border border-border px-3 py-2">
          <span className="t14m">
            {view.missing.length} việc có trong kế hoạch nhưng không còn trong
            file — giữ nguyên, không xoá:
          </span>{" "}
          <span className="select-text text-muted-foreground">
            {view.missing
              .map((m) => m.title + (m.due ? ` (hạn ${dm(m.due)})` : ""))
              .join(" · ")}
          </span>
        </p>
      ) : null}

      <details className="t12 rounded-lg border border-border px-3 py-2">
        <summary className="cursor-pointer">
          Chi tiết từng việc ({view.tasks.length})
        </summary>
        <div className="mt-2 max-h-72 overflow-auto">
          <table className="t12 w-full">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="px-2 py-1">Dòng</th>
                <th className="px-2 py-1">Việc</th>
                <th className="px-2 py-1">Ngày</th>
                <th className="px-2 py-1">Chờ dòng</th>
                <th className="px-2 py-1">Làm gì</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {view.tasks.map((t) => (
                <tr
                  key={t.line}
                  className={t.action === "invalid" ? "text-destructive" : ""}
                >
                  <td className="px-2 py-1">{t.line}</td>
                  <td className="max-w-56 px-2 py-1">
                    <span className="block truncate" title={t.title}>
                      {t.code ? `${t.code} · ` : ""}
                      {t.title}
                    </span>
                    {t.phase ? (
                      <span className="text-muted-foreground">{t.phase}</span>
                    ) : null}
                    {t.warnings.map((w) => (
                      <span
                        key={w}
                        className="block select-text text-[color:var(--warn)]"
                      >
                        {w}
                      </span>
                    ))}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1">
                    {dm(t.start)} – {dm(t.end)}
                    {t.computed.length ? (
                      <span className="text-muted-foreground"> · máy tính</span>
                    ) : null}
                  </td>
                  <td className="px-2 py-1">{t.after.join(", ")}</td>
                  <td className="px-2 py-1">
                    <Pill
                      text={
                        ACTION[t.action].text +
                        (t.action === "update" && t.diff.length
                          ? `: ${t.diff.slice(0, 2).join(", ")}`
                          : "")
                      }
                      tone={ACTION[t.action].tone}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
