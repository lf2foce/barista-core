import { CHART_KINDS } from "./chart";
import { decodeBrief } from "./brief";
import { decodeWord } from "./word";
import type {
  ComposerData,
  ComposerKind,
  ComposerItem,
  ComposerLayout,
  ComposerRef,
  ComposerSection,
  ComposerOverflowPage,
  ComposerTurn,
  ActionSpec,
  AskSpec,
  Block,
  BoardColumn,
  CardPage,
  Column,
  Compact,
  DetailOriginal,
  ListAttach,
  DetailPage,
  Sheet,
  SheetCompose,
  Input,
  Paging,
  Json,
  Kpi,
  OpenRef,
  Page,
  Row,
  SectionTable,
  TablePage,
  TableViews,
  Tabs,
  ThreadBlockData,
  ThreadMsg, BlockSource, CalendarData, CalendarItem, TimelineData, TimelineGroup, TimelineItem, TimelineMilestone } from "./types";

// Giải mã dung thứ: server chỉ được THÊM trường, nên mọi trường thiếu đều có mặc định; khối lạ / hỏng ⇒ { type: "unknown" }, không vỡ thẻ.

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : v == null ? d : String(v));
const optStr = (v: unknown): string | undefined => (typeof v === "string" && v !== "" ? v : undefined);
const bool = (v: unknown): boolean => v === true;
const obj = (v: unknown): Obj => (isObj(v) ? v : {});

export function decodeKpi(raw: unknown): Kpi {
  const o = obj(raw);
  const d = o.drill && typeof o.drill === "object" ? obj(o.drill) : null;
  const drill = d && typeof d.query === "string" && d.query ? { query: d.query, input: obj(d.input) as Input, sum: typeof d.sum === "string" ? d.sum : null } : undefined;
  const edit = isObj(o.edit) && typeof o.edit.command === "string" ? decodeAction(o.edit) : undefined;
  return { label: str(o.label), value: (o.value ?? null) as Json, format: str(o.format, "number"), tone: typeof o.tone === "string" ? o.tone : null, hint: str(o.hint), ...(drill ? { drill } : {}), ...(edit ? { edit } : {}), ...(typeof o.why === "string" && o.why ? { why: o.why } : {}) };
}

export function decodeAction(raw: unknown): ActionSpec {
  const o = obj(raw);
  return {
    command: str(o.command),
    title: str(o.title),
    style: str(o.style, "default"),
    bind: stringMap(o.bind),
    preset: obj(o.preset) as Record<string, Json>,
    prefill: o.prefill ? stringMap(o.prefill) : undefined,
    ...(isObj(o.values) ? { values: o.values as Input } : {}),
    when: Object.fromEntries(Object.entries(obj(o.when)).map(([k, v]) => [k, arr(v) as Json[]])),
    importable: bool(o.importable),
    ...(o.import_only === true ? { import_only: true } : {}),
    ...(typeof o.upload === "string" && /^[a-z0-9]{2,5}(,[a-z0-9]{2,5})*$/.test(o.upload) ? { upload: o.upload } : {}),
    ...(typeof o.route === "string" && ROUTE.test(o.route) ? { route: o.route } : {}),
    ...(typeof o.basis === "string" && o.basis ? { basis: o.basis } : {}),
    ...(typeof o.confirm === "string" && o.confirm ? { confirm: o.confirm } : {}),
    ...(o.instant === true ? { instant: true } : {}),
  };
}

/** Đường riêng của gói chỉ nhận dạng /v1/<chữ thường>/<…> — không '..', không truy vấn (server khai, BFF vẫn lọc lại). */
export const ROUTE = /^\/v1\/[a-z0-9_]+(\/[a-z0-9_-]+)+$/;

const stringMap = (v: unknown): Record<string, string> =>
  Object.fromEntries(Object.entries(obj(v)).map(([k, x]) => [k, str(x)]));

const int = (v: unknown, d = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : d);

/** Khối "Trao đổi" (0046 / 0089 / 0115): dung thứ — trường thiếu có mặc định, trường lạ bỏ qua. */
export function decodeThread(o: Obj): ThreadBlockData {
  return {
    title: str(o.title, "Trao đổi"),
    subject_kind: str(o.subject_kind),
    subject_id: str(o.subject_id),
    subject_title: str(o.subject_title),
    thread_id: typeof o.thread_id === "string" ? o.thread_id : null,
    last_read: int(o.last_read),
    can_post: bool(o.can_post),
    people: arr(o.people).map((p) => ({ value: str(obj(p).value), label: str(obj(p).label) })),
    actions: arr(o.actions).map(decodeAction),
    messages: arr(o.messages).map((m): ThreadMsg => {
      const mo = obj(m);
      const card = obj(mo.card);
      const reply = isObj(mo.reply) ? mo.reply : null;
      return {
        id: int(mo.id),
        author: str(mo.author),
        mine: bool(mo.mine),
        body: str(mo.body),
        at: str(mo.at),
        mentions_me: bool(mo.mentions_me),
        card:
          typeof card.query === "string"
            ? {
                query: card.query,
                input: obj(card.input) as Record<string, Json>,
                title: str(card.title, card.query),
                live: card.live && typeof card.live === "object"
                  ? { gone: bool(obj(card.live).gone), subtitle: str(obj(card.live).subtitle), headline: str(obj(card.live).headline), actions: arr(obj(card.live).actions).map(decodeAction) }
                  : null,
              }
            : null,
        edited: bool(mo.edited),
        recalled: bool(mo.recalled),
        pinned: bool(mo.pinned),
        reply: reply ? { id: int(reply.id), author: str(reply.author), body: str(reply.body), recalled: bool(reply.recalled) } : null,
        attachments: arr(mo.attachments).map((f) => {
          const x = obj(f);
          // ADR 0249: hồ sơ của luồng nhận tệp ⇒ nút "Lưu vào hồ sơ" (lệnh do server mô tả)
          return { id: str(x.id), filename: str(x.filename, "tệp"), content_type: str(x.content_type), size_bytes: int(x.size_bytes), is_image: bool(x.is_image),
                   ...(isObj(x.keep) ? { keep: decodeAction(x.keep) } : {}) };
        }).filter((f) => f.id),
        reactions: arr(mo.reactions).map((r) => {
          const x = obj(r);
          return { emoji: str(x.emoji), count: int(x.count), mine: bool(x.mine), names: arr(x.names).map((n) => str(n)) };
        }).filter((r) => r.emoji && r.count > 0),
        can_edit: bool(mo.can_edit),
        history: decodeOpen(mo.history) ?? null,
        actions: arr(mo.actions).map(decodeAction).filter((a) => a.command),
        marks: arr(mo.marks).map((k) => {
          const x = obj(k);
          return { title: str(x.title), detail: str(x.detail), tone: str(x.tone, "muted"), open: decodeOpen(x.open) ?? null };
        }).filter((k) => k.title),
      };
    }),
    kind_label: str(o.kind_label),
    has_more: bool(o.has_more),
    has_newer: bool(o.has_newer),
    focus_id: typeof o.focus_id === "number" ? o.focus_id : null,
    pinned: arr(o.pinned).map((p) => {
      const x = obj(p);
      return { id: int(x.id), author: str(x.author), body: str(x.body), pinned_by: str(x.pinned_by), at: str(x.at) };
    }),
    muted: bool(o.muted),
    following: bool(o.following),
    reaction_choices: arr(o.reaction_choices).map((e) => str(e)).filter(Boolean),
    can_attach: bool(o.can_attach),
    file_max: int(o.file_max),
    file_kinds: str(o.file_kinds),
    assist: arr(o.assist).map((a) => ({ title: str(obj(a).title), prompt: str(obj(a).prompt) })).filter((a) => a.title && a.prompt),
    notice: str(o.notice),
    files: decodeOpen(o.files) ?? null,
  };
}

export function decodeOpen(raw: unknown): OpenRef | undefined {
  if (!isObj(raw) || typeof raw.query !== "string") return undefined;
  const title = optStr(raw.title);
  return { ...(title ? { title } : {}), query: raw.query, input: obj(raw.input) as Record<string, Json> };
}

function decodeColumn(raw: unknown): Column {
  const o = obj(raw);
  return {
    key: str(o.key),
    label: str(o.label),
    format: str(o.format, "text"),
    labels: o.labels ? stringMap(o.labels) : undefined,
    tones: o.tones ? stringMap(o.tones) : undefined,
    width: typeof o.width === "number" ? o.width : undefined,
  };
}

export function rowId(value: Json | undefined, index: number): string {
  if (value === null || value === undefined || value === "") return String(index);
  return String(value);
}

export function decodeTable(raw: unknown): TablePage {
  const o = obj(raw);
  const key = str(o.row_key, "id");
  return {
    kind: "table",
    title: str(o.title),
    kpis: arr(o.kpis).map(decodeKpi),
    columns: arr(o.columns).map(decodeColumn),
    rows: arr(o.rows).map((r, i): Row => {
      const values = obj(r) as Record<string, Json>;
      return { id: rowId(values[key], i), values };
    }),
    empty: str(o.empty, "Chưa có dữ liệu"),
    ...(Array.isArray(o.empty_actions) && o.empty_actions.length ? { empty_actions: arr(o.empty_actions).map(decodeAction) } : {}),
    note: str(o.note),
    views: decodeViews(o.views),
    ...decodeCompact(o.compact),
    ...(Array.isArray(o.row_actions) && o.row_actions.length ? { row_actions: arr(o.row_actions).map(decodeAction) } : {}),
    ...(isObj(o.open) && typeof o.open.query === "string" ? { open: { query: o.open.query, bind: stringMap(o.open.bind), title: str(o.open.title) } } : {}),
    ...(Array.isArray(o.header) && o.header.length ? { header: arr(o.header).map(decodeBlock).filter((b) => b.type !== "unknown") } : {}),
    ...decodePaging(o.paging),
  };
}

/** Trang phía server: thiếu / sai ⇒ không có (bảng đủ dòng như cũ). */
function decodePaging(raw: unknown): { paging?: Paging } {
  const o = obj(raw);
  const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : null);
  const offset = n(o.offset), limit = n(o.limit), total = n(o.total);
  return offset === null || limit === null || total === null ? {} : { paging: { offset, limit, total, complete: o.complete !== false } };
}

/** Phần "thẻ gọn" (0094): thiếu / sai ⇒ không có (trang thường). `total` không phải số ⇒ null — không biết tổng thì không đoán. */
export function decodeCompact(raw: unknown): { compact?: Compact } {
  if (!raw || typeof raw !== "object") return {};
  const o = obj(raw);
  const shown = typeof o.shown === "number" ? o.shown : 0;
  return { compact: { shown, total: typeof o.total === "number" ? o.total : null } };
}

const keyedOpts = (v: unknown) => arr(v).map((x) => obj(x)).map((x) => ({ key: str(x.key), title: str(x.title) })).filter((x) => x.key);

/** Lịch (0240): thiếu / sai ⇒ null (khối bị bỏ). Mục thiếu giờ ⇒ bỏ mục đó; chế độ lạ ⇒ Ngày. */
export function decodeCalendar(raw: unknown): CalendarData | null {
  if (!isObj(raw)) return null;
  const day = (v: unknown) => (typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v) ? v.slice(0, 10) : "");
  const start = day(raw.start);
  const end = day(raw.end);
  if (!start || !end || end < start) return null;
  const view = raw.view === "week" || raw.view === "agenda" ? raw.view : "day";
  const hrs = arr(raw.hours).map((h) => (typeof h === "number" ? h : NaN));
  const hours: [number, number] = hrs.length === 2 && hrs[0] >= 0 && hrs[1] <= 24 && hrs[0] < hrs[1] ? [hrs[0], hrs[1]] : [7, 20];
  const mv = (v: unknown) => {
    const m = obj(v);
    return typeof m.command === "string" && m.command ? { command: m.command, preset: obj(m.preset) as Input, fields: stringMap(m.fields) } : undefined;
  };
  const items = arr(raw.items)
    .map((i) => obj(i))
    .filter((i) => typeof i.start === "string" && typeof i.end === "string" && i.start !== "")
    .map((i): CalendarItem => ({
      id: str(i.id),
      lane: str(i.lane),
      title: str(i.title),
      subtitle: str(i.subtitle),
      start: str(i.start),
      end: str(i.end),
      all_day: bool(i.all_day),
      kind: str(i.kind),
      tone: str(i.tone, "blue"),
      status: str(i.status, "booked"),
      busy: bool(i.busy),
      repeats: bool(i.repeats),
      open: decodeOpen(i.open),
      links: arr(i.links).map((l) => obj(l)).filter((l) => typeof l.query === "string").map((l) => ({ title: str(l.title), query: str(l.query), input: obj(l.input) as Input })),
      ...(mv(i.move) ? { move: mv(i.move) } : {}),
      ...(isObj(i.cancel) && typeof i.cancel.command === "string" ? { cancel: { command: i.cancel.command, preset: obj(i.cancel.preset) as Input } } : {}),
      ...(optStr(i.organizer) ? { organizer: optStr(i.organizer) } : {}),
      ...(Array.isArray(i.people) ? { people: arr(i.people).map((x) => obj(x)).map((x) => ({ name: str(x.name), rsvp: str(x.rsvp), label: str(x.label), organizer: bool(x.organizer) })) } : {}),
      ...(isObj(i.respond) && typeof i.respond.command === "string"
        ? { respond: { command: i.respond.command, preset: obj(i.respond.preset) as Input, param: str(i.respond.param, "answer"), current: str(i.respond.current), options: keyedOpts(i.respond.options) } }
        : {}),
    }));
  const nav = isObj(raw.nav) && typeof raw.nav.prev === "string" ? obj(raw.nav) : null;
  const who = nav && isObj(nav.who) ? obj(nav.who) : null;
  const keyed = (v: unknown) => arr(v).map((x) => obj(x)).map((x) => ({ key: str(x.key), title: str(x.title) })).filter((x) => x.key);
  const cr = isObj(raw.create) ? obj(raw.create) : null;
  return {
    title: str(raw.title),
    view,
    start,
    end,
    today: day(raw.today),
    now: str(raw.now),
    tz: str(raw.tz),
    hours,
    days: arr(raw.days).map((d) => obj(d)).filter((d) => day(d.on)).map((d) => ({ on: day(d.on), label: str(d.label), off: str(d.off) })),
    lanes: arr(raw.lanes).map((l) => obj(l)).map((l) => ({ key: str(l.key), title: str(l.title), subtitle: str(l.subtitle), me: bool(l.me), email: str(l.email), ...(isObj(l.open) ? { open: l.open as Input } : {}) })),
    kinds: arr(raw.kinds).map((k) => obj(k)).map((k) => ({ key: str(k.key), label: str(k.label), tone: str(k.tone, "blue") })),
    items,
    nav: nav
      ? {
          param_view: str(nav.param_view, "view"),
          param_on: str(nav.param_on, "on"),
          prev: str(nav.prev),
          next: str(nav.next),
          views: keyed(nav.views),
          ...(who ? { who: { param: str(who.param, "who"), current: str(who.current), options: keyed(who.options) } } : {}),
          ...(isObj(nav.unit) ? { unit: { param: str(obj(nav.unit).param, "team_code"), current: str(obj(nav.unit).current), options: arr(obj(nav.unit).options).map((x) => obj(x)).map((x) => ({ key: str(x.key), title: str(x.title) })) } } : {}),
        }
      : null,
    create: cr
      ? {
          understand: str(cr.understand),
          me: str(cr.me),
          invite: isObj(cr.invite)
            ? { options: str(obj(cr.invite).options), groups: arr(obj(cr.invite).groups).map((g) => obj(g)).map((g) => ({ title: str(g.title), emails: arr(g.emails).map((e) => str(e)) })).filter((g) => g.emails.length) }
            : null,
          creators: arr(cr.creators)
            .map((c) => obj(c))
            .filter((c) => typeof c.command === "string" && c.command)
            .map((c) => {
              const sj = isObj(c.subject) && typeof c.subject.field === "string" ? obj(c.subject) : null;
              return {
                key: str(c.key),
                label: str(c.label),
                kind: str(c.kind),
                tone: str(c.tone, "blue"),
                command: str(c.command),
                preset: obj(c.preset) as Input,
                fields: stringMap(c.fields),
                ...(sj ? { subject: { field: str(sj.field), label: str(sj.label, "Khách"), search: str(sj.search), required: bool(sj.required) } } : {}),
                ...(optStr(c.place) ? { place: optStr(c.place) } : {}),
              };
            }),
        }
      : null,
    empty: str(raw.empty),
    note: str(raw.note),
  };
}

/** Dòng thời gian (0222): thiếu / sai ⇒ null (khối bị bỏ, không vỡ trang). Ngày sai định dạng ⇒ bỏ dòng đó. */
export function decodeTimeline(raw: unknown): TimelineData | null {
  if (!isObj(raw)) return null;
  const day = (v: unknown) => (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : "");
  const start = day(raw.start);
  const end = day(raw.end);
  const today = day(raw.today);
  if (!start || !end || end < start) return null;
  const miles = (v: unknown): TimelineMilestone[] => arr(v).map((m) => obj(m)).filter((m) => day(m.on)).map((m) => ({ on: day(m.on), title: str(m.title), tone: str(m.tone, "accent") }));
  const groups = arr(raw.groups).map((g): TimelineGroup => {
    const go = obj(g);
    return {
      key: str(go.key),
      title: str(go.title),
      subtitle: str(go.subtitle),
      ...(day(go.start) && day(go.end) ? { start: day(go.start), end: day(go.end) } : {}),
      collapsed: go.collapsed === true,
      open: decodeOpen(go.open),
      milestones: miles(go.milestones),
      items: arr(go.items)
        .map((i) => obj(i))
        .filter((i) => day(i.start))
        .map((i): TimelineItem => ({
          id: str(i.id),
          title: str(i.title),
          start: day(i.start),
          end: day(i.end) || null,
          kind: i.kind === "milestone" ? "milestone" : "bar",
          tone: str(i.tone, "muted"),
          status: str(i.status),
          who: str(i.who),
          dates: str(i.dates),
          flag: str(i.flag),
          open: decodeOpen(i.open),
          after: arr(i.after).map((x) => str(x)),
        })),
    };
  });
  return {
    title: str(raw.title),
    today,
    start,
    end,
    groups,
    milestones: miles(raw.milestones),
    off: arr(raw.off).map((o) => obj(o)).filter((o) => day(o.from) && day(o.to)).map((o) => ({ from: day(o.from), to: day(o.to), kind: str(o.kind, "weekend") })),
    note: str(raw.note),
  };
}

function decodeViews(raw: unknown): TableViews | null {
  if (!raw || typeof raw !== "object") return null;
  const o = obj(raw);
  const b = o.board ? obj(o.board) : null;
  const c = o.calendar ? obj(o.calendar) : null;
  const mv = b?.move ? obj(b.move) : null;
  return {
    board: b
      ? {
          by: str(b.by),
          title: str(b.title),
          subtitle: str(b.subtitle),
          columns: arr(b.columns).map((x) => {
            const col = obj(x);
            return { key: str(col.key), title: str(col.title), tone: col.tone ? str(col.tone) : undefined };
          }),
          move: mv
            ? { command: str(mv.command), id_key: str(mv.id_key), column_key: str(mv.column_key), form_columns: arr(mv.form_columns).map((v) => str(v)) }
            : null,
        }
      : null,
    calendar: c ? { date: str(c.date), title: str(c.title), subtitle: str(c.subtitle), overdue: c.overdue !== false } : null,
    timeline: decodeTimeline(o.timeline),
  };
}

function decodeSectionTable(raw: unknown): SectionTable | null {
  if (raw === null || typeof raw !== "object") return null;
  const t = obj(raw);
  const columns = arr(t.columns).map((c) => str(c));
  if (!columns.length) return null;
  return { columns, rows: arr(t.rows).map((r) => arr(r).map((c) => str(c))), total: arr(t.total).map((c) => str(c)) };
}

/** Khối "Tệp" của hồ sơ (ADR 0249): chỉ đường của chính core (/v1/…) — không gửi tệp tới chỗ server lạ. */
export function decodeAttach(raw: unknown): ListAttach | null {
  if (!isObj(raw)) return null;
  const path = str(raw.path);
  if (!path.startsWith("/v1/") || !str(raw.subject_kind) || !str(raw.subject_id)) return null;
  return {
    subject_kind: str(raw.subject_kind), subject_id: str(raw.subject_id), subject_title: str(raw.subject_title), path,
    file_max: int(raw.file_max), file_kinds: str(raw.file_kinds), labels: arr(raw.labels).map((l) => str(l)).filter(Boolean),
  };
}

function decodeOriginal(raw: unknown): DetailOriginal | null {
  if (raw === null || typeof raw !== "object") return null;
  const o = obj(raw);
  if (!str(o.path)) return null;
  const page = typeof o.page === "number" && o.page > 0 ? Math.floor(o.page) : undefined;
  return { title: str(o.title, "Bản gốc"), path: str(o.path), filename: optStr(o.filename), ...(page ? { page } : {}) };
}

function decodeDetail(raw: unknown): DetailPage {
  const o = obj(raw);
  const ask = obj(o.ask);
  return {
    kind: "detail",
    title: str(o.title),
    subtitle: str(o.subtitle),
    sections: arr(o.sections).map((s) => {
      const so = obj(s);
      return {
        title: str(so.title),
        fields: arr(so.fields).map((f) => {
          const fo = obj(f);
          const open = obj(fo.open);
          return {
            label: str(fo.label),
            value: (fo.value ?? null) as Json,
            format: str(fo.format, "text"),
            ...(typeof open.query === "string" && open.query ? { open: { query: open.query, input: obj(open.input) as Input, ...(typeof open.title === "string" ? { title: open.title } : {}) } } : {}),
            ...(isObj(fo.action) && typeof fo.action.command === "string" ? { action: decodeAction(fo.action) } : {}),
            ...(typeof fo.placeholder === "string" && fo.placeholder ? { placeholder: fo.placeholder } : {}),
          };
        }),
        text: typeof so.text === "string" ? so.text : null,
        table: decodeSectionTable(so.table),
      };
    }),
    links: arr(o.links).map((l) => {
      const lo = obj(l);
      const page = typeof lo.page === "number" && lo.page > 0 ? Math.floor(lo.page) : undefined;
      return { title: str(lo.title), path: str(lo.path), filename: optStr(lo.filename), ...(page ? { page } : {}) };
    }),
    actions: arr(o.actions).map(decodeAction),
    style: typeof o.style === "string" ? o.style : null,
    ask: decodeAsk(ask),
    original: decodeOriginal(o.original),
    textRole: typeof o.text_role === "string" ? o.text_role : null,
    sheet: decodeSheet(o.sheet),
  };
}

/** Tờ giấy (0251): dung thứ — thiếu `blocks` ⇒ không có tờ giấy (app vẽ như trang chi tiết thường). */
export function decodeSheet(raw: unknown): Sheet | null {
  if (!isObj(raw) || !Array.isArray(raw.blocks)) return null;
  const strs = (v: unknown) => arr(v).filter((x): x is string => typeof x === "string");
  return {
    title: str(raw.title),
    number: str(raw.number),
    draftMark: str(raw.draft_mark),
    blocks: arr(raw.blocks).map((b) => {
      const bo = obj(b);
      return {
        type: str(bo.type, "para"),
        text: str(bo.text),
        ...(Array.isArray(bo.runs) ? { runs: arr(bo.runs).map((r) => { const ro = obj(r); return { t: str(ro.t), ...(typeof ro.k === "string" ? { k: ro.k, layer: str(ro.layer, "record") } : {}) }; }) } : {}),
        ...(Array.isArray(bo.columns) ? { columns: strs(bo.columns), rows: arr(bo.rows).map(strs), total: strs(bo.total), align: strs(bo.align) } : {}),
        ...(typeof bo.slot === "string" && bo.slot ? { slot: bo.slot, hint: str(bo.hint), ...(bo.empty === true ? { empty: true } : {}) } : {}),
      };
    }),
    signers: strs(raw.signers),
    notes: strs(raw.notes),
    actions: arr(raw.actions).map(decodeAction),
    legend: arr(raw.legend).map((l) => ({ layer: str(obj(l).layer), label: str(obj(l).label) })),
    ...(isObj(raw.open) && typeof raw.open.query === "string" && raw.open.query
      ? { open: { query: raw.open.query, input: obj(raw.open.input) as Input, ...(typeof raw.open.title === "string" ? { title: raw.open.title } : {}) } }
      : {}),
    ...(decodeSheetCompose(raw.compose) ? { compose: decodeSheetCompose(raw.compose)! } : {}),
  };
}

/** Phần "Soạn với agent" của tờ giấy (0251 lát 2) — thiếu ngữ cảnh ⇒ không có (tờ giấy vẫn vẽ như cũ). */
export function decodeSheetCompose(raw: unknown): SheetCompose | null {
  if (!isObj(raw) || !isObj(raw.context) || typeof raw.context.query !== "string" || !raw.context.query) return null;
  return {
    about: str(raw.about),
    status: str(raw.status),
    chips: arr(raw.chips).map(obj).filter((c) => str(c.label) && (typeof c.ask === "string" || isObj(c.action)))
      .map((c) => ({ label: str(c.label), ...(typeof c.ask === "string" && c.ask ? { ask: c.ask } : {}), ...(isObj(c.action) ? { action: decodeAction(c.action) } : {}) })),
    ...(isObj(raw.write) ? { write: decodeAction(raw.write) } : {}),
    context: { query: raw.context.query, input: obj(raw.context.input) as Input, title: str(raw.context.title) },
  };
}

export function decodeBlock(raw: unknown): Block {
  const b = decodeBlockBody(raw);
  const e = obj(raw).edit;
  const w = decodeSpan(obj(raw).w);
  const source = decodeSource(obj(raw).source);
  const withEdit = isObj(e) && typeof e.command === "string" ? { ...b, edit: decodeAction(e) } : b;
  const why = obj(raw).why;
  const withSource0 = source ? { ...withEdit, source } : withEdit;
  const withSource = typeof why === "string" && why ? { ...withSource0, why } : withSource0;
  return w === undefined ? withSource : { ...withSource, w };
}

/** Nguồn gốc của số (0220). Không có nhãn nguồn ⇒ bỏ (khối vẫn vẽ như cũ). */
export function decodeSource(v: unknown): BlockSource | undefined {
  const o = obj(v);
  const label = str(o.label);
  if (!isObj(v) || !label) return undefined;
  return { kind: str(o.kind), label, scope: str(o.scope), through: str(o.through), reliableFrom: str(o.reliable_from), warn: str(o.warn) };
}

/** Lọc chéo của biểu đồ (0173): cần `param` và `values` cùng độ dài nhãn; thiếu / lệch ⇒ bỏ (biểu đồ không bấm được, vẫn vẽ). */
function decodePick(v: unknown, n: number): { pick?: { param: string; values: string[] } } {
  const o = obj(v);
  const values = arr(o.values).map((x) => str(x));
  return typeof o.param === "string" && o.param !== "" && values.length === n ? { pick: { param: o.param, values } } : {};
}

/** Bề rộng khối trên lưới 12 cột (0166). Không phải số nguyên 1–12 ⇒ coi như không khai (khối chiếm cả hàng như cũ). */
function decodeSpan(v: unknown): number | undefined {
  return typeof v === "number" && Number.isInteger(v) && v >= 1 && v <= 12 ? v : undefined;
}

function decodeBlockBody(raw: unknown): Block {
  try {
    const o = obj(raw);
    switch (o.type) {
      case "kpis":
        return { type: "kpis", items: arr(o.items).map(decodeKpi) };
      case "steps":
        return {
          type: "steps",
          title: str(o.title),
          steps: arr(o.steps).map((s) => {
            const so = obj(s);
            return { label: str(so.label), state: str(so.state, "todo"), at: so.at as Json, note: optStr(so.note), owner: optStr(so.owner) };
          }),
        };
      case "stages":
        return {
          type: "stages",
          title: str(o.title),
          stages: arr(o.stages).map((s) => {
            const so = obj(s);
            return {
              key: str(so.key),
              name: str(so.name),
              owner: str(so.owner),
              state: str(so.state, "todo"),
              items: arr(so.items).map((i) => {
                const io = obj(i);
                return {
                  label: str(io.label),
                  status: str(io.status),
                  required: bool(io.required),
                  hidden: bool(io.hidden),
                  value: optStr(io.value),
                  note: optStr(io.note),
                };
              }),
            };
          }),
        };
      case "brief": {   // bản chạy thử chuẩn (ADR 0253) — phiếu duyệt vẽ cùng khối với thẻ đề xuất
        const b = decodeBrief(o);
        return b ? { type: "brief", ...b } : { type: "text", title: "", markdown: "" };
      }
      case "callout":
        return {
          type: "callout",
          tone: str(o.tone, "muted"),
          title: str(o.title),
          text: str(o.text),
          facts: arr(o.facts).map((f) => str(f)),
          draft: str(o.draft),
          actions: arr(o.actions).map(decodeAction),
        };
      case "finding":   // phát hiện có bằng chứng mở được (0072) — kernel/ui.py b_finding
        return {
          type: "finding",
          tone: str(o.tone, "muted"),
          title: str(o.title),
          badge: str(o.badge),
          text: str(o.text),
          facts: arr(o.facts).map((f) => str(f)),
          evidence: arr(o.evidence).map((e) => {
            const x = obj(e);
            const page = typeof x.page === "number" && Number.isInteger(x.page) && x.page > 0 ? x.page : null;
            return { label: str(x.label), quote: str(x.quote), value: optStr(x.value) ?? null, page, document_id: optStr(x.document_id) ?? null, scan: bool(x.scan), filename: str(x.filename), note: str(x.note) };
          }),
          actions: arr(o.actions).map(decodeAction),
        };
      case "list":
        return {
          type: "list",
          title: str(o.title),
          ...(decodeAttach(o.attach) ? { attach: decodeAttach(o.attach)! } : {}),
          items: arr(o.items).map((i) => {
            const io = obj(i);
            const f = isObj(io.file) && typeof io.file.id === "string" && io.file.id ? io.file : null;
            return {
              title: str(io.title), subtitle: optStr(io.subtitle), tone: optStr(io.tone), badge: optStr(io.badge), open: decodeOpen(io.open), ask: optStr(io.ask),
              ...(f ? { file: { id: str(f.id), filename: str(f.filename, "tệp"), content_type: str(f.content_type), size_bytes: int(f.size_bytes), is_image: bool(f.is_image) } } : {}),
            };
          }),
        };
      case "tiles":
        return {
          type: "tiles",
          title: str(o.title),
          items: arr(o.items).map((i) => {
            const io = obj(i);
            return { title: str(io.title), subtitle: optStr(io.subtitle), badge: optStr(io.badge), tone: optStr(io.tone), module: optStr(io.module), logo: optStr(io.logo), open: decodeOpen(io.open) };
          }),
          ...(o.style === "rows" ? { style: "rows" as const } : {}),
        };
      case "text":
        return { type: "text", title: str(o.title), markdown: str(o.markdown) };
      case "image": {
        // ảnh (ui.b_image): link ký của kho tệp (http/https) hoặc đường /v1/… của core; khác ⇒ coi như chưa có ảnh (hiện `note`), không vẽ link lạ
        const src = str(o.src);
        const safe = /^https?:\/\/[^\s"'<>]+$/.test(src) || /^\/v1(\/[A-Za-z0-9_-][A-Za-z0-9_.-]*)+(\?[^\s"'<>]*)?$/.test(src) ? src : "";
        return { type: "image", src: safe, caption: str(o.caption), alt: str(o.alt), note: str(o.note) };
      }
      case "split": {
        // chia hai cột (ui.b_split): giải mã đệ quy từng cột, bỏ khối lạ; tỉ lệ sai dạng ⇒ 1:1
        const ratio = /^[1-9]:[1-9]$/.test(str(o.ratio)) ? str(o.ratio) : "1:1";
        return { type: "split", ratio, left: arr(o.left).map(decodeBlock).filter((b) => b.type !== "unknown"), right: arr(o.right).map(decodeBlock).filter((b) => b.type !== "unknown") };
      }
      case "audio": {
        // chỉ đường của chính core (/v1/…, đi qua BFF có phiên) — không phát link ngoài server gửi nhầm
        const src = str(o.src);
        if (!/^\/v1(\/[A-Za-z0-9_-][A-Za-z0-9_.-]*)+$/.test(src)) return { type: "unknown" };
        return { type: "audio", title: str(o.title), src, duration_s: typeof o.duration_s === "number" ? o.duration_s : null };
      }
      case "choices":
        return {
          type: "choices",
          title: str(o.title),
          submit: str(o.submit, "Gửi"),
          prefix: str(o.prefix),
          questions: arr(o.questions)
            .map((q) => {
              const x = obj(q);
              return { key: str(x.key), question: str(x.question), options: arr(x.options).map((v) => str(v)).filter(Boolean), multi: bool(x.multi) };
            })
            .filter((q) => q.question && q.options.length),
        };
      case "table":
        return { type: "table", page: decodeTable(o.page) };
      case "picker":   // chọn mức đặt sẵn (0144) — kernel/ui.py b_picker
        return {
          type: "picker",
          title: str(o.title),
          current: str(o.current),
          custom: str(o.custom, "Tuỳ chỉnh"),
          note: str(o.note),
          pending: str(o.pending),
          items: arr(o.items).map((i) => {
            const x = obj(i);
            return { key: str(x.key), title: str(x.title), text: str(x.text), facts: arr(x.facts).map((f) => str(f)), action: isObj(x.action) && typeof x.action.command === "string" ? decodeAction(x.action) : null };
          }).filter((i) => i.key && i.title),
        };
      case "board": {
        const mv = obj(o.move);
        const move =
          typeof mv.command === "string" && typeof mv.id_key === "string" && typeof mv.column_key === "string"
            ? { command: mv.command, id_key: mv.id_key, column_key: mv.column_key, form_columns: Array.isArray(mv.form_columns) ? mv.form_columns.map((x) => str(x)) : undefined }
            : null;
        const columns: BoardColumn[] = arr(o.columns).map((c) => {
          const co = obj(c);
          return {
            key: str(co.key),
            title: str(co.title),
            subtitle: optStr(co.subtitle),
            tone: optStr(co.tone),
            items: arr(co.items).map((i) => {
              const io = obj(i);
              return { id: str(io.id), title: str(io.title), subtitle: optStr(io.subtitle), tone: optStr(io.tone), badge: optStr(io.badge), open: decodeOpen(io.open) };
            }),
          };
        });
        return { type: "board", title: str(o.title), columns, move };
      }
      case "calendar": {
        const c = decodeCalendar(o);
        return c ? { type: "calendar", ...c } : { type: "unknown" };
      }
      case "timeline": {
        const t = decodeTimeline(o);
        return t ? { type: "timeline", ...t } : { type: "unknown" };
      }
      case "thread":
        return { type: "thread", ...decodeThread(o) };
      case "chart": {
        const kind = str(o.kind, "bar");
        return {
          type: "chart",
          title: str(o.title),
          kind: (CHART_KINDS as readonly string[]).includes(kind) ? kind : "bar",
          unit: str(o.unit, "count"),
          ...(optStr(o.unit_right) ? { unit_right: optStr(o.unit_right) } : {}),
          labels: arr(o.labels).map((l) => str(l)),
          series: arr(o.series).map((x) => {
            const so = obj(x);
            return {
              name: str(so.name),
              values: arr(so.values).map((v) => (typeof v === "number" && Number.isFinite(v) ? v : null)),
              tone: optStr(so.tone),
              ...(so.as === "bar" || so.as === "line" ? { as: so.as } : {}),
              ...(so.axis === "left" || so.axis === "right" ? { axis: so.axis } : {}),
              ...(Array.isArray(so.x) ? { x: arr(so.x).map((v) => (typeof v === "number" && Number.isFinite(v) ? v : null)) } : {}),
            };
          }),
          note: str(o.note),
          ...decodePick(o.pick, arr(o.labels).length),
        };
      }
      case "composer":
        return { type: "composer", ...decodeComposer(o) };
      case "word":
        return { type: "word", ...decodeWord(o) };
      case "studio": {
        const d = obj(o.doc);
        if (!str(d.create) || !str(d.view) || !str(d.assist)) return { type: "unknown" };
        return {
          type: "studio", title: str(o.title), subtitle: str(o.subtitle), examples: arr(o.examples).filter((x): x is string => typeof x === "string"),
          doc: { create: str(d.create), view: str(d.view), assist: str(d.assist), home: str(d.home), label: str(d.label, "Văn bản mới"),
                 ...(str(d.kb_documents) ? { kb_documents: str(d.kb_documents) } : {}) },
          forms: arr(o.forms).map(obj).filter((f) => str(f.key) && str(f.label)).map((f) => ({ key: str(f.key), label: str(f.label) })),
          kinds: arr(o.kinds).map(obj).filter((k) => str(k.label)).map((k) => ({
            label: str(k.label), ...(str(k.subtitle) ? { subtitle: str(k.subtitle) } : {}), ...(k.current === true ? { current: true } : {}),
            ...(isObj(k.open) && str(k.open.query) ? { open: { query: str(k.open.query), input: obj(k.open.input) as Input } } : {}),
          })),
        };
      }
      case "later":
        return typeof o.query === "string" && o.query
          ? { type: "later", query: o.query, input: obj(o.input) as Input, title: str(o.title), ...(typeof o.part === "string" && o.part ? { part: o.part } : {}) }
          : { type: "unknown" };
      default:
        return { type: "unknown" };
    }
  } catch {
    return { type: "unknown" };
  }
}

/** Soạn với AI (thử nghiệm): mọi trường thiếu có mặc định; khối con lạ giữ nguyên `type` để bộ vẽ bỏ qua. */
/** Một khối của bản soạn (thẻ, hoặc sự kiện ``data`` / ``blocks`` của luồng soạn). */
export function decodeComposerItem(x: unknown): ComposerItem {
  const b = obj(x);
  const src = isObj(b.source)
    ? { query: str(b.source.query), title: str(b.source.title), at: str(b.source.at), input: isObj(b.source.input) ? (b.source.input as Input) : {} }
    : null;
  return {
    id: str(b.id),
    type: str(b.type),
    origin: str(b.origin),
    flags: arr(b.flags).map((f) => str(f)),
    text: str(b.text),
    level: int(b.level, 1),
    placeholders: arr(b.placeholders).map((f) => str(f)),
    page: isObj(b.page) ? decodeTable(b.page) : null,
    source: src,
    cite: str(b.cite),
    document_id: str(b.document_id),
    caption: str(b.caption),
    filename: str(b.filename),
    slot: str(b.slot),
    texts: arr(b.texts).map((t) => ({ title: str(obj(t).title), text: str(obj(t).text) })).filter((t) => t.text),
    derived: b.derived === true,
    thumb: str(b.thumb),
    illustration: str(b.illustration),
  };
}

const strs = (v: unknown): string[] => arr(v).map((x) => str(x));

/** Phần bố cục: loại lạ ⇒ bỏ (server thêm loại mới sau — web cũ vẫn vẽ phần còn lại). */
export function decodeComposerSection(x: unknown): ComposerSection | null {
  const s = obj(x);
  switch (str(s.kind)) {
    case "cover":
      return { kind: "cover", style: str(s.style, "band"), hero: str(s.hero), text: strs(s.text) };
    case "flow":
      return { kind: "flow", ids: strs(s.ids) };
    case "columns":
      return { kind: "columns", cols: Math.min(3, Math.max(1, int(s.cols, 2))), columns: arr(s.columns).map(strs) };
    case "tiles":
      return {
        kind: "tiles", cols: Math.min(4, Math.max(1, int(s.cols, 3))), ids: strs(s.ids),
        items: arr(s.items).map((i) => ({ block: str(obj(i).block), label: str(obj(i).label), value: str(obj(i).value) })),
      };
    case "grid":
      return { kind: "grid", cols: Math.min(4, Math.max(1, int(s.cols, 2))), ids: strs(s.ids) };
    case "footer":
      return { kind: "footer", ids: strs(s.ids), lines: Object.fromEntries(Object.entries(obj(s.lines)).map(([k, v]) => [k, strs(v)])) };
    default:
      return null;
  }
}

function decodeComposerLayout(v: unknown): ComposerLayout {
  const l = obj(v);
  return {
    template: str(l.template),
    templates: arr(l.templates).map((t) => ({ key: str(obj(t).key), label: str(obj(t).label), about: str(obj(t).about) })),
    slots: arr(l.slots).map((t) => ({ key: str(obj(t).key), label: str(obj(t).label) })),
    sections: arr(l.sections).map(decodeComposerSection).filter((x): x is ComposerSection => x !== null),
    html: str(l.html),
  };
}

/** Một lượt trò chuyện của bản soạn (người nói / AI trả lời + các bước). */
export function decodeComposerTurn(x: unknown): ComposerTurn {
  const t = obj(x);
  return {
    role: str(t.role) === "user" ? "user" : "assistant",
    text: str(t.text),
    at: str(t.at),
    block: str(t.block),
    mode: str(t.mode),
    status: str(t.status, "ready"),
    version: typeof t.version === "number" ? t.version : null,
    steps: arr(t.steps).map((s) => ({ title: str(obj(s).title), status: str(obj(s).status, "ok"), ...(str(obj(s).reason) ? { reason: str(obj(s).reason) } : {}) })),
    partial: t.partial === true,
    overflow: arr(t.overflow).map(decodeOverflowPage).filter((pg) => pg.page > 0),
  };
}

/** Trang còn tràn khung do server đo — khối không có mã bị bỏ; hành động chỉ nhận khi là lệnh sửa khối (đuôi `.revise` — web không nhắc tên module của gói, app chỉ vẽ) với đủ mã khối + lời dặn; lệnh khác ⇒ không nút. */
function decodeOverflowPage(x: unknown): ComposerOverflowPage {
  const o = obj(x);
  return {
    page: int(o.page),
    lines: int(o.lines),
    axis: str(o.axis, "h"),
    blocks: arr(o.blocks).map((b) => {
      const r = obj(b);
      const a = obj(r.action);
      const inp = obj(a.input);
      const ok = str(a.command).endsWith(".revise") && str(inp.session_id) && str(inp.block) && str(inp.instruction);
      return {
        id: str(r.id), kind: str(r.kind), label: str(r.label), culprit: r.culprit === true,
        action: ok ? { command: str(a.command), label: str(a.label, "Rút gọn khối này"), input: { session_id: str(inp.session_id), block: str(inp.block), mode: str(inp.mode, "rewrite"), instruction: str(inp.instruction) } } : null,
      };
    }).filter((b) => b.id),
  };
}

/** Hồ sơ / khách của ấn phẩm — thiếu ⇒ rỗng (bản cũ chưa gắn). */
export function decodeComposerRef(v: unknown): ComposerRef {
  const r = obj(v);
  return { kind: str(r.kind), id: str(r.id), label: str(r.label) };
}

export function decodeComposer(o: Obj): ComposerData {
  const pairs = (v: unknown) => arr(v).map((x) => ({ key: str(obj(x).key), label: str(obj(x).label) }));
  const kinds = arr(o.kinds).map((x): ComposerKind => {
    const k = obj(x);
    return { key: str(k.key), label: str(k.label), templates: pairs(k.templates), formats: pairs(k.formats), paper: k.paper === "deck" ? "deck" : "page", example: str(k.example) };
  }).filter((k) => k.key);
  // máy chủ cũ chưa gửi `default_kind` ⇒ loại đầu được mời chọn (web không tự biết khoá loại nào)
  const defaultKind = str(o.default_kind) || kinds[0]?.key || "";
  const st = obj(o.stats);
  const br = obj(o.brand);
  return {
    session_id: str(o.session_id),
    kind: str(o.kind) || defaultKind,
    kind_label: str(o.kind_label),
    title: str(o.title),
    brief: str(o.brief),
    version: int(o.version, 1),
    status: str(o.status, "ready"),
    status_label: str(o.status_label),
    error: str(o.error),
    kinds,
    default_kind: defaultKind,
    formats: pairs(o.formats),
    subject: decodeComposerRef(o.subject),
    customer: decodeComposerRef(o.customer),
    from_record: o.from_record === true,
    recipes: arr(o.recipes).map((x) => {
      const r = obj(x);
      return { key: str(r.key), label: str(r.label), about: str(r.about), piece: str(r.piece) || defaultKind, needs_subject: r.needs_subject === true, customer: r.customer === true };
    }).filter((r) => r.key),
    blocks: arr(o.blocks).map(decodeComposerItem),
    stats: { flagged: int(st.flagged), placeholders: int(st.placeholders), data: int(st.data) },
    layout: decodeComposerLayout(o.layout),
    shares: arr(o.shares).map((x) => {
      const h = obj(x);
      return { id: str(h.id), recipient: str(h.recipient), version: int(h.version), at: str(h.at), expires_at: str(h.expires_at), views: int(h.views), last_viewed_at: str(h.last_viewed_at), active: h.active === true };
    }),
    versions: arr(o.versions).map((x) => {
      const v = obj(x);
      return { version: int(v.version), via: str(v.via), note: str(v.note), at: str(v.at) };
    }),
    exports: arr(o.exports).map((x) => {
      const e = obj(x);
      return { id: str(e.id), format: str(e.format), filename: str(e.filename), version: int(e.version), at: str(e.at) };
    }),
    turns: arr(o.turns).map(decodeComposerTurn),
    fitting: str(o.fitting),
    history: arr(o.history).map((x) => {
      const h = obj(x);
      return { id: str(h.id), title: str(h.title), kind: str(h.kind), kind_label: str(h.kind_label), status: str(h.status, "ready"), status_label: str(h.status_label), at: str(h.at) };
    }),
    note: str(o.note),
    brand: { color: /^[0-9A-Fa-f]{6}$/.test(str(br.color)) ? str(br.color) : "1F4E79", font: str(br.font, "Arial"), footer: str(br.footer), logo: str(br.logo).startsWith("data:image/") ? str(br.logo) : "" },
    commands: stringMap(o.commands),
    stream: str(o.stream).startsWith("/v1/") ? str(o.stream) : "",
  };
}

function decodeCard(raw: unknown): CardPage {
  const o = obj(raw);
  const ask = obj(o.ask);
  return {
    kind: "card",
    title: str(o.title),
    subtitle: str(o.subtitle),
    icon: str(o.icon),
    blocks: arr(o.blocks).map(decodeBlock),
    actions: arr(o.actions).map(decodeAction),
    open: arr(o.open).map(decodeOpen).filter((x): x is OpenRef => !!x),
    ask: decodeAsk(ask),
    ...decodeCompact(o.compact),
    ...decodeTabs(o.tabs),
  };
}

/** Nút "hỏi trợ lý" của trang; `mode` (vd "builder" — trình tạo app, 0098) + `app_id` chỉ thêm khi có. */
export function decodeAsk(ask: Record<string, unknown>): AskSpec | null {
  if (typeof ask.prompt !== "string") return null;
  return { title: str(ask.title, "Hỏi trợ lý"), prompt: ask.prompt, ...(typeof ask.mode === "string" && ask.mode ? { mode: ask.mode } : {}),
           ...(typeof ask.app_id === "string" && ask.app_id ? { app_id: ask.app_id } : {}) };
}

/** Tab của thẻ nhiều trang (0098): thiếu / dưới hai tab ⇒ không có. */
export function decodeTabs(raw: unknown): { tabs?: Tabs } {
  if (!isObj(raw) || typeof raw.param !== "string") return {};
  const items = arr(raw.items).map((i) => ({ key: str(obj(i).key), title: str(obj(i).title) })).filter((i) => i.key);
  return items.length > 1 ? { tabs: { param: raw.param, items, current: str(raw.current, items[0].key) } } : {};
}

/** Trang theo `kind` (thiếu / lạ ⇒ bảng, như app Mac). */
export function decodePage(raw: unknown): Page {
  const kind = obj(raw).kind;
  if (kind === "detail") return decodeDetail(raw);
  if (kind === "card") return decodeCard(raw);
  return decodeTable(raw);
}
