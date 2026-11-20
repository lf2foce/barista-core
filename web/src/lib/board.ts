import type { ActionSpec, BoardCard, BoardColumn, BoardMove, BoardViewSpec, CalendarViewSpec, Input, OpenRef, Row } from "./types";

// Bảng kanban: kéo thẻ sang cột = chạy `move.command` (server kiểm quyền / luật). Cột trong `form_columns` cần hỏi thêm
// (vd "Kẹt" cần lý do) ⇒ mở form điền sẵn thay vì chạy luôn. `move` trống ⇒ thẻ chỉ chuyển cột theo dữ kiện (hồ sơ).

export function movePayload(move: BoardMove, cardId: string, column: string): Input {
  return { [move.id_key]: cardId, [move.column_key]: column };
}

export function needsForm(move: BoardMove | null, column: string): boolean {
  return !!move?.form_columns?.includes(column);
}

/** Hành động mở form cho cột cần hỏi thêm — như `CardView.move` bên Mac. */
export function formActionFor(move: BoardMove, card: BoardCard, column: BoardColumn | undefined): ActionSpec {
  return {
    command: move.command,
    title: `${card.title} → ${column?.title ?? ""}`.trim(),
    style: "primary",
    bind: {},
    preset: movePayload(move, card.id, column?.key ?? ""),
    when: {},
    importable: false,
  };
}

/** Cột hiển thị của từng thẻ sau khi áp các lần chuyển lạc quan (thẻ → cột mới, chờ trang tải lại). */
export function visibleColumns(columns: BoardColumn[], moved: Record<string, string>): BoardColumn[] {
  return columns.map((col) => {
    const stay = col.items.filter((c) => moved[c.id] === undefined || moved[c.id] === col.key);
    const incoming = columns
      .filter((o) => o.key !== col.key)
      .flatMap((o) => o.items)
      .filter((c) => moved[c.id] === col.key);
    return { ...col, items: [...stay, ...incoming] };
  });
}

export function columnOf(columns: BoardColumn[], moved: Record<string, string>, cardId: string): string | undefined {
  return moved[cardId] ?? columns.find((c) => c.items.some((i) => i.id === cardId))?.key;
}

/** Kanban từ CÙNG các dòng bảng (0087): cột theo thứ tự server gửi, thẻ mở đúng như bấm dòng (`openFor`). */
export function boardFromRows(rows: Row[], spec: BoardViewSpec, openFor: (row: Row) => OpenRef | undefined): BoardColumn[] {
  return spec.columns.map((c) => ({
    key: c.key,
    title: c.title,
    tone: c.tone,
    items: rows
      .filter((r) => String(r.values[spec.by] ?? "") === c.key)
      .map((r) => ({ id: r.id, title: String(r.values[spec.title] ?? ""), subtitle: spec.subtitle ? String(r.values[spec.subtitle] ?? "") : undefined, open: openFor(r) })),
  }));
}

/** Lịch: dòng xếp theo ngày (yyyy-mm-dd) của cột server khai — ngày tăng dần, dòng chưa có ngày để cuối (khoá ""). */
export function agendaDays(rows: Row[], spec: CalendarViewSpec): { day: string; rows: Row[] }[] {
  const groups = new Map<string, Row[]>();
  for (const r of rows) {
    const day = String(r.values[spec.date] ?? "").slice(0, 10);
    groups.set(day, [...(groups.get(day) ?? []), r]);
  }
  return [...groups.keys()].sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b))).map((day) => ({ day, rows: groups.get(day) ?? [] }));
}

const dayLabel = new Intl.DateTimeFormat("vi-VN", { weekday: "long", day: "2-digit", month: "2-digit", year: "numeric" });

/** Tiêu đề một ngày của góc nhìn lịch: hôm nay nổi lên; ngày đã qua là "Quá hạn" chỉ khi server khai lịch là hạn (``overdue``) — lịch sự kiện thì không. */
export function agendaHeader(day: string, today: string, overdue: boolean): { text: string; tone: "muted" | "today" | "bad" } {
  if (!day) return { text: "Chưa đặt ngày", tone: "muted" };
  const label = dayLabel.format(new Date(`${day}T00:00:00`));
  const text = label.charAt(0).toUpperCase() + label.slice(1);
  if (day === today) return { text: `Hôm nay · ${text}`, tone: "today" };
  return day < today && overdue ? { text: `Quá hạn · ${text}`, tone: "bad" } : { text, tone: "muted" };
}
