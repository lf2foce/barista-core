"use client";

import { Check, ChevronRight, ExternalLink, Pencil, Sparkles } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api } from "@/lib/api";
import { buildInput, callPage, coerce, fixedInput, hiddenInitial, initialValues, missingRequired, openLink, prefillInput, rowsOf, savedWarning, splitGroups, setCell, visibleFields, type SavedWarning } from "@/lib/form";
import { caretAfter, dependentsOf, groupMoney, isBlank, markMode, optionsParams, pairFields, shownValue } from "@/lib/form-ui";
import { parseDay, vnLong } from "@/lib/dates";
import { text } from "@/lib/format";
import { newKey, nextKey } from "@/lib/idempotency";
import type { ActionSpec, FieldSpec, FormSpec, Input, Json, Option, Row } from "@/lib/types";
import { Input as TextInput } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useCallHub } from "@/components/call/call-provider";
import { SwitchQuestion } from "@/components/call/switch-question";
import { startsCall, undoOf } from "@/lib/call-hub";
import { actionClass, ErrorLine, Spinner } from "@/components/primitives";
import { cn } from "@/lib/utils";
import { Combobox } from "./combobox";
import { DatePicker } from "./date-picker";
import { MonthPicker } from "./month-picker";
import { TimePicker } from "./time-picker";

const PLACEHOLDER: Record<string, string> = {
  money: "VD 4,5 tỷ hoặc 4.500.000.000",
  phone: "0912345678",
  percent: "VD 2,5",
};

/** Đơn vị dán sau ô gõ (không phải một phần giá trị). */
const UNIT: Record<string, string> = { money: "đ", percent: "%" };

export const selectClass =
  "t14 h-8 w-full rounded-lg border border-input bg-field px-2 outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40";

/** Danh sách chọn tải từng lượt tối đa chừng này dòng; đủ ⇒ có thể còn nữa ⇒ ô chọn hỏi lại server theo chữ gõ. */
const OPTIONS_LIMIT = 100;

/** Trạng thái chip của ô điền sẵn: máy đoán (✨, chờ xem) · đã chấp nhận · đang sửa. */
type Chip = "guess" | "ok" | "edit";

/** Ô nào vẽ được dạng chip (một dòng ngắn): không phải ô nhiều dòng / nhiều chọn / bảng / công tắc. */
const chipable = (f: FieldSpec): boolean => !["textarea", "multiselect", "rows", "bool"].includes(f.kind);

/**
 * Form của một lệnh — dựng từ FormSpec server gửi (sinh từ model Pydantic của lệnh). Trường gắn với dòng (`bind`) và giá trị cố định
 * (`preset`) không hiện. Tiền / ngày gửi đúng chữ người gõ ("4,5 tỷ", "05/11/2026") — server hiểu. Khoá chống lặp: một khoá cho mỗi
 * hộp thoại, đổi khi lệnh bị từ chối conflict / invalid (sửa rồi gửi lại = yêu cầu mới).
 *
 * Lớp vẽ chung (18/11/2026): ô trống chưa chạm KHÔNG báo lỗi (lỗi chỉ hiện sau khi rời ô / bấm gửi; nút gửi mờ tới khi đủ ô bắt buộc); ô chọn từ
 * truy vấn là ô chọn có tìm; ô phụ thuộc (`options_with` / `options_args`) tải lại khi ô kia đổi; giá trị máy điền sẵn là chip ✨ sửa được; nhóm gấp tự mở khi
 * có giá trị / lỗi; hai ô ngắn liền nhau chung một hàng.
 */
export function FormBody({
  action,
  row = null,
  initial = {},
  prefetched,
  embedded = false,
  onEdit,
  onDone,
  onOpenSimilar,
  onCancel,
  submitter,
}: {
  action: ActionSpec;
  row?: Row | null;
  initial?: Input;
  prefetched?: FieldSpec[];
  embedded?: boolean;
  onEdit?: (values: Input) => void;
  /** `output`: kết quả lệnh (vd paper_id ⇒ mở thẻ kết quả); `spec`: form đã dùng (để biết `shows`). */
  onDone: (message: string, output?: Json, spec?: FormSpec | null) => void;
  /** Mở một tài liệu gần giống từ cảnh báo sau khi lưu (vd kb.add_document ⇒ người chọn "Thay bằng bản mới" trên bản cũ). */
  onOpenSimilar?: (id: string, title: string) => void;
  onCancel?: () => void;
  /** Gửi thay cho gọi lệnh trực tiếp (thẻ đề xuất của lượt đang dừng — 0049: lệnh chạy qua `proposals/decide`, trợ lý chạy tiếp).
   * `done` ⇒ xong với `message`; không ⇒ form giữ, hiện lỗi, khoá chống lặp mới (sửa rồi bấm lại = yêu cầu mới). */
  submitter?: (input: Input, key: string) => Promise<{ done: boolean; message: string; status: string }>;
}) {
  const [spec, setSpec] = useState<FormSpec | null>(null);
  const [values, setValues] = useState<Input>({});
  const [dyn, setDyn] = useState<Record<string, Option[]>>({});
  const [more, setMore] = useState<Record<string, boolean>>({});   // danh sách của ô này đã cắt ở giới hạn ⇒ gõ tìm hỏi server
  const [error, setError] = useState<string | null>(null);
  const callHub = useCallHub();
  const hubView = useSyncExternalStore(callHub?.subscribe ?? noSubscribe, callHub?.getSnapshot ?? noSnapshot, callHub?.getSnapshot ?? noSnapshot);
  const [asking, setAsking] = useState(false);
  // hộp thoại form đóng khi câu hỏi còn đó ⇒ coi như "Ở lại" (không để câu hỏi treo ở hub)
  const askingNow = useRef(false);
  useEffect(() => () => {
    if (askingNow.current) callHub?.cancelSwitch();
  }, [callHub]);
  const [sending, setSending] = useState(false);
  const [key, setKey] = useState(newKey);
  const [invalid, setInvalid] = useState<string[]>([]);
  const [touched, setTouched] = useState<ReadonlySet<string>>(new Set());   // ô đã chạm rồi rời đi — chỉ ô này (hoặc sau khi bấm gửi) mới hiện lỗi
  const [tried, setTried] = useState(false);
  const [chips, setChips] = useState<Record<string, Chip>>({});
  const [opened, setOpened] = useState<Record<string, boolean>>({});   // nhóm gấp người đã tự mở / đóng (không có ⇒ theo giá trị / lỗi)
  const [after, setAfter] = useState<{ message: string; output: Json; warn: SavedWarning } | null>(null);
  const [link, setLink] = useState<{ message: string; output: Json; url: string; host: string } | null>(null);
  const [fixed0] = useState(() => fixedInput(action, row));
  const [initial0] = useState(initial);
  // giá trị cố định = preset + cột của dòng + trường ẩn đã được điền sẵn (thẻ đề xuất mang id)
  const fixed = useMemo(() => ({ ...hiddenInitial(spec, initial0), ...fixed0 }), [spec, initial0, fixed0]);
  const visible = useMemo(() => visibleFields(spec, fixed), [spec, fixed]);
  const visibleKeys = useMemo(() => new Set(visible.map((f) => f.key)), [visible]);
  const { main, groups } = useMemo(() => splitGroups(visible), [visible]);
  const mark = useMemo(() => markMode(visible), [visible]);
  const missing = useMemo(() => missingRequired(visible, values), [visible, values]);
  const loaded = useRef(false);
  const formRef = useRef<HTMLFormElement>(null);
  const focused = useRef(false);
  const optSeq = useRef<Record<string, number>>({});
  // báo phần người đang sửa ra ngoài (thẻ đề xuất giữ khi chuyển thẻ) — SAU khi vẽ, không gọi setState của cha trong lúc vẽ
  const onEditRef = useRef(onEdit);
  useEffect(() => {
    onEditRef.current = onEdit;
  });
  const edited = useRef(false);
  useEffect(() => {
    if (edited.current) onEditRef.current?.(values);
  }, [values]);

  /** Tải danh sách của MỘT ô (chống lượt về trễ: chỉ lượt mới nhất của ô được nhận). */
  const fetchOptions = useCallback(async (f: FieldSpec, params: Record<string, string>) => {
    const mine = (optSeq.current[f.key] = (optSeq.current[f.key] ?? 0) + 1);
    const rows = await api.options(f.options_query!, { ...params, limit: String(OPTIONS_LIMIT) }).catch(() => [] as Option[]);
    if (mine !== optSeq.current[f.key]) return;
    setDyn((cur) => ({ ...cur, [f.key]: rows }));
    setMore((cur) => ({ ...cur, [f.key]: rows.length >= OPTIONS_LIMIT }));
  }, []);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    (async () => {
      try {
        const s: FormSpec = prefetched
          ? { command: action.command, title: action.title, description: "", importable: false, fields: prefetched }
          : await api.form(action.command);
        // `values` của nút: ô server điền sẵn mà người vẫn sửa được (vd tiêu đề việc lấy từ tin nhắn)
        const filled = { ...prefillInput(action, row), ...(action.values ?? {}) };
        const vals = initialValues(s.fields, initial, filled);
        setValues(vals);
        setSpec(s);
        // danh sách tuỳ trường khác của form (`options_with` / `options_args`, vd mã tin nhắn, dự án): gửi kèm giá trị cố định / điền sẵn
        const known = { ...vals, ...hiddenInitial(s, initial), ...fixedInput(action, row) };
        const fx = fixedInput(action, row);
        const vk = new Set(s.fields.filter((f) => !f.hidden && !(f.key in fx)).map((f) => f.key));
        // chip máy đoán: ô server điền sẵn (`values` của nút) + ô người gọi truyền vào (không tính `preset`); hộp trong thẻ đề xuất vẽ ô thường
        if (!embedded) {
          const guessed = [...Object.keys(action.values ?? {}), ...Object.keys(initial).filter((k) => !(k in action.preset))];
          const chip: Record<string, Chip> = {};
          for (const k of guessed) {
            const f = s.fields.find((x) => x.key === k);
            if (f && vk.has(k) && !f.hidden && chipable(f) && !isBlank(vals[k])) chip[k] = "guess";
          }
          setChips(chip);
        }
        // ô con của bảng nhiều dòng (kind "rows") có danh sách riêng ⇒ khoá "trường.ô_con"
        const wanted = s.fields.flatMap((f) => [
          ...(!f.options && f.options_query ? [{ k: f.key, f, sub: false }] : []),
          ...(f.fields ?? []).filter((c) => !c.options && c.options_query).map((c) => ({ k: `${f.key}.${c.key}`, f: c, sub: true })),
        ]);
        const entries = await Promise.all(
          wanted.map(async ({ k, f, sub }) => {
            const { params, waiting } = sub ? { params: {}, waiting: false } : optionsParams(f, known, vk);
            if (waiting) return [k, null] as const;   // ô phụ thuộc một ô người chưa chọn: chờ
            const rows = await api.options(f.options_query!, { ...params, limit: String(OPTIONS_LIMIT) }).catch(() => [] as Option[]);
            return [k, rows] as const;
          }),
        );
        const got: Record<string, Option[]> = Object.fromEntries(entries.filter((e): e is readonly [string, Option[]] => e[1] !== null));
        // máy đoán: ô bắt buộc còn trống mà danh sách chỉ có ĐÚNG MỘT dòng ⇒ điền luôn (chip ✨, sửa được); điền xong thì ô phụ thuộc nó tải tiếp (tối đa vài vòng)
        let cur = vals;
        const picked: Record<string, Chip> = {};
        for (let round = 0; round < 3 && !embedded; round++) {
          const pick: Input = {};
          for (const f of s.fields) {
            if (!vk.has(f.key) || !f.required || !isBlank(cur[f.key]) || !chipable(f)) continue;
            const opts = f.options ?? got[f.key];
            if (opts && opts.length === 1) {
              pick[f.key] = opts[0].value;
              picked[f.key] = "guess";
            }
          }
          if (!Object.keys(pick).length) break;
          cur = { ...cur, ...pick };
          for (const f of s.fields) {
            if (!f.options_query || f.options || got[f.key] !== undefined) continue;
            const { params, waiting } = optionsParams(f, { ...cur, ...hiddenInitial(s, initial), ...fx }, vk);
            if (!waiting) got[f.key] = await api.options(f.options_query, { ...params, limit: String(OPTIONS_LIMIT) }).catch(() => [] as Option[]);
          }
        }
        setDyn(got);
        setMore(Object.fromEntries(Object.entries(got).map(([k, r]) => [k, r.length >= OPTIONS_LIMIT])));
        if (cur !== vals) {
          setValues(cur);
          setChips((c) => ({ ...c, ...picked }));
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Không tải được form");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, initial, prefetched, row]);

  // tự focus ô đầu khi form đã vẽ và các danh sách chọn đã về (ô chọn đang tải bị khoá, không focus được) — người mở hộp là gõ luôn
  const listsPending = visible.some((f) => f.options_query && !f.options && dyn[f.key] === undefined && !optionsParams(f, { ...fixed, ...values }, visibleKeys).waiting);
  useEffect(() => {
    if (focused.current || !spec || listsPending || !formRef.current) return;
    focused.current = true;
    const first = formRef.current.querySelector<HTMLElement>("input:not([type=hidden]):not([type=checkbox]):not([disabled]), textarea:not([disabled]), button[role=combobox]:not([disabled]), select:not([disabled])");
    first?.focus({ preventScroll: true });
  }, [spec, listsPending]);

  // nút "instant" (vd chọn đáp án) không còn gì để điền ⇒ chạy luôn, không hiện hộp xác nhận rỗng. Nút khác vẫn hỏi xác nhận.
  const autoRan = useRef(false);
  useEffect(() => {
    if (autoRan.current || !spec || visible.length || action.style !== "instant" || submitter || embedded) return;
    autoRan.current = true;
    void submit();
  });

  const touch = (k: string) => setTouched((cur) => (cur.has(k) ? cur : new Set(cur).add(k)));

  const set = (k: string, v: Json | undefined) => {
    edited.current = true;
    const next = { ...values };
    if (v === undefined) delete next[k];
    else next[k] = v;
    setValues(next);
    setInvalid((i) => i.filter((x) => x !== k));
    // ô khác phụ thuộc ô này: xoá giá trị (có thể không còn thuộc danh sách mới) + tải lại danh sách theo giá trị mới
    for (const d of dependentsOf(spec?.fields ?? [], k)) {
      if (!visibleKeys.has(d.key) || d.key === k) continue;
      setValues((cur) => {
        const n = { ...cur };
        delete n[d.key];
        return n;
      });
      setChips((cur) => (d.key in cur ? Object.fromEntries(Object.entries(cur).filter(([x]) => x !== d.key)) : cur));
      setDyn((cur) => {
        const n = { ...cur };
        delete n[d.key];
        return n;
      });
      const { params, waiting } = optionsParams(d, { ...fixed, ...next }, visibleKeys);
      optSeq.current[d.key] = (optSeq.current[d.key] ?? 0) + 1;   // lượt đang bay của danh sách cũ bị bỏ
      if (!waiting) void fetchOptions(d, params);
    }
  };

  /** Chip: bấm sửa ⇒ hiện ô thật và đặt con trỏ vào đó. */
  const editChip = (k: string) => {
    setChips((cur) => ({ ...cur, [k]: "edit" }));
    requestAnimationFrame(() => document.getElementById(`f-${k}`)?.focus());
  };
  const guessKeys = Object.keys(chips).filter((k) => chips[k] === "guess" && visibleKeys.has(k));
  const acceptAll = () => setChips((cur) => Object.fromEntries(Object.entries(cur).map(([k, c]) => [k, c === "guess" ? "ok" : c])));

  async function submit() {
    const miss = missingRequired(visible, values);
    setTried(true);
    if (miss.length) {
      setInvalid(miss.map((f) => f.key));
      setError(`Còn thiếu: ${miss.map((f) => f.label).join(", ")}`);
      return;
    }
    setSending(true);
    setError(null);
    try {
      if (submitter) {
        const o = await submitter(buildInput(values, visible, fixed), key);
        if (o.done) onDone(o.message);
        else {
          setError(o.message || o.status);
          setKey(newKey());
        }
        return;
      }
      const input = buildInput(values, visible, fixed);
      // một người một cuộc gọi: đang trong cuộc gọi mà bấm gọi / tham gia cuộc khác ⇒ hỏi rời cuộc cũ TRƯỚC khi server tạo / ngồi vào cuộc mới
      if (callHub && startsCall(action.command, input, callHub.getSnapshot().callId)) {
        // câu hỏi vẽ NGAY TRONG form (hộp thoại form giữ tiêu điểm); chữ đã nhập giữ nguyên trong state của form
        setAsking((askingNow.current = true));
        const go = await callHub.ask(true);
        setAsking((askingNow.current = false));
        if (!go) {
          if (action.style === "instant") onCancel?.();   // nút một chạm: ở lại cuộc cũ thì đóng luôn hộp, không để form rỗng
          return;
        }
      }
      const r = await api.command(action.command, input, key, action.route);
      if (r.status === "ok") {
        const msg = r.replayed ? "Đã gửi trước đó — không chạy lại" : `Đã xong: ${action.title}`;
        const warn = savedWarning(r.output);
        const page = callPage(r.output);
        const open = page ? { url: page, host: "cuộc gọi" } : openLink(r.output);   // `page` ở đây chỉ còn là đường lùi khi chạy ngoài khung (không có bảng nổi)
        // đã lưu nhưng có điều cần người quyết (tài liệu gần giống đang hiệu lực…) ⇒ giữ hộp thoại, hiện cảnh báo
        if (warn) setAfter({ message: msg, output: r.output, warn });
        // gọi trực tiếp một chạm: cuộc gọi chạy trong bảng nổi ngay trong trang (call-dock.tsx), không mở thẻ mới — trang này vẫn dùng được. Không có bảng nổi
        // (chạy ngoài khung) ⇒ mở trang gọi ở thẻ mới; trình duyệt chặn cửa sổ ⇒ rơi xuống liên kết thật bên dưới
        else if (page && (callHub?.openPage(page, undoOf(r.output)) ?? (openCallPage(page) ? "opened" : null))) onDone(msg, r.output, spec);
        // lệnh trả link cần mở (tham gia cuộc gọi…) ⇒ giữ hộp thoại với một liên kết thật: người bấm mở, không bị trình duyệt chặn cửa sổ
        else if (open) setLink({ message: summaryOf(r.output) || msg, output: r.output, ...open });
        else onDone(msg, r.output, spec);
      }
      else if (r.status === "pending_approval") onDone("Đã gửi phiếu duyệt");
      else {
        setError(r.error || r.status);
        setKey((k) => nextKey(k, r.status));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không gửi được");
    } finally {
      setSending(false);
    }
  }

  const labelOf = (k: string) => spec?.fields.find((f) => f.key === k)?.label ?? k;
  const renderField = (f: FieldSpec) => {
    if (f.kind === "rows") return <RowsField key={f.key} f={f} value={values[f.key]} dyn={dyn} bad={invalid.includes(f.key)} onChange={(v) => set(f.key, v)} />;
    const { waiting } = optionsParams(f, { ...fixed, ...values }, visibleKeys);
    const opts = f.options ?? dyn[f.key];
    const q = f.options_query;
    return (
      <Field
        key={f.key}
        f={f}
        value={values[f.key]}
        options={opts}
        loading={!!q && !f.options && !waiting && dyn[f.key] === undefined}
        waitingFor={waiting ? waitLabel(f, visibleKeys, labelOf) : ""}
        remote={!!more[f.key]}
        search={q && !f.options ? (term) => api.options(q, { ...optionsParams(f, { ...fixed, ...values }, visibleKeys).params, q: term, limit: String(OPTIONS_LIMIT) }).catch(() => []) : undefined}
        bad={invalid.includes(f.key) || ((tried || touched.has(f.key)) && f.required && f.kind !== "bool" && isBlank(values[f.key]))}
        mark={mark}
        chip={chips[f.key]}
        onChip={() => editChip(f.key)}
        onChange={(v) => set(f.key, v)}
        onBlur={() => touch(f.key)}
      />
    );
  };
  /** Hàng ô: hai ô ngắn liền nhau chung một hàng (ngày + giờ, từ + đến); còn lại một ô một hàng. */
  const renderRows = (list: FieldSpec[]) =>
    pairFields(list).map((pair) =>
      pair.length === 2 ? (
        <div key={pair[0].key} className={cn("grid items-start gap-3 sm:grid-cols-2", pair.every((p) => ["date", "time", "month"].includes(p.kind)) && "max-sm:grid-cols-2")}>
          {pair.map(renderField)}
        </div>
      ) : (
        renderField(pair[0])
      ),
    );

  if (asking && callHub && hubView?.conflict?.kind === "ask") return <SwitchQuestion hub={callHub} switching={hubView.switching} />;

  if (link) {
    return (
      <div className="flex flex-col gap-3">
        <p className="t14m text-ok">✓ {link.message}</p>
        <a href={link.url} target="_blank" rel="noopener noreferrer" className={cn(actionClass("primary"), "h-10 w-full")}>
          <ExternalLink className="size-4" aria-hidden />
          {link.url.startsWith("/goi/") ? "Vào cuộc gọi" : <>Mở {link.host}</>}
        </a>
        <p className="t12 text-muted-foreground">
          {link.url.startsWith("/goi/") ? "Mở ở thẻ mới: gọi trực tiếp trong trình duyệt, cần cho phép micro." : "Mở ở thẻ mới. Xong việc quay lại đây để bấm Rời hoặc Kết thúc."}
        </p>
        <div className="flex justify-end">
          <button type="button" className={actionClass("default")} onClick={() => onDone(link.message, link.output, spec)}>
            Xong
          </button>
        </div>
      </div>
    );
  }

  if (after) {
    return (
      <div className="flex flex-col gap-3">
        <p className="t14m text-ok">✓ {after.message}</p>
        <SavedWarningPanel warn={after.warn} onOpen={onOpenSimilar} />
        <div className="flex justify-end">
          <button type="button" className="t14m h-8 rounded-lg bg-primary px-3 text-primary-foreground hover:bg-primary/90" onClick={() => onDone(after.message, after.output, spec)}>
            Xong
          </button>
        </div>
      </div>
    );
  }

  const blocked = !!spec && missing.length > 0;
  return (
    <form
      ref={formRef}
      className="flex min-h-0 flex-1 flex-col gap-4"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (!blocked) void submit();
      }}
    >
      {!embedded && spec?.description ? <p className="t12 text-muted-foreground">{spec.description}</p> : null}
      {!spec && !error ? <Spinner /> : null}
      {visible.length ? (
        <div className="-mx-1.5 flex min-h-0 flex-col gap-3 overflow-y-auto px-1.5 py-1">   {/* lề cho viền chọn ô (ring 2–3px) không bị khung cuộn cắt, cả bốn phía */}
          {guessKeys.length >= 2 ? (
            <div className="t12 flex items-center justify-between gap-2 rounded-lg bg-primary/5 px-2.5 py-1.5 text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <Sparkles className="size-3.5 text-primary" aria-hidden />
                Máy điền sẵn {guessKeys.length} ô — xem lại, bấm vào ô để sửa
              </span>
              <button type="button" className="t12m shrink-0 rounded-md px-2 py-1 text-primary hover:bg-primary/10" onClick={acceptAll}>
                Chấp nhận tất cả
              </button>
            </div>
          ) : null}
          {renderRows(main)}
          {groups.map((g) => {
            // ô hiếm cần (vd dán link Meet / Zoom): gấp lại, người dùng hằng ngày không phải đọc; tự mở khi có ô đã có giá trị (điền sẵn / sửa), ô bắt buộc còn trống, hoặc có lỗi
            const auto = g.fields.some((f) => invalid.includes(f.key) || (f.required && f.kind !== "bool" && isBlank(values[f.key])) || (!isBlank(values[f.key]) && !sameJson(values[f.key], f.default)));
            const open = opened[g.name] ?? auto;
            return (
              <details key={g.name} className="group flex flex-col gap-3" open={open} onToggle={(e) => { const o = (e.currentTarget as HTMLDetailsElement).open; if (o !== open) setOpened((cur) => ({ ...cur, [g.name]: o })); }}>
                <summary className="t14 flex min-h-9 cursor-pointer list-none items-center gap-1 text-muted-foreground hover:text-foreground">
                  <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden />
                  {g.name}
                  <span className="t12 text-muted-foreground/70">· {g.fields.length} ô</span>
                </summary>
                <div className="flex flex-col gap-3">{renderRows(g.fields)}</div>
              </details>
            );
          })}
        </div>
      ) : spec ? (
        <p className="t14 text-muted-foreground">Không cần điền gì thêm — bấm “{action.title}” để xác nhận.</p>
      ) : null}
      {error ? <ErrorLine>{error}</ErrorLine> : null}
      <div className="flex flex-col gap-2">
        {blocked ? <p className="t12 text-muted-foreground">Cần điền: {missing.map((f) => f.label).join(", ")}</p> : null}
        <div className="flex items-center justify-end gap-2">
          {!embedded && onCancel ? (
            <button type="button" onClick={onCancel} className="t14m h-8 rounded-lg border border-border bg-surface px-3 hover:bg-accent max-sm:h-10 max-sm:flex-1">
              Huỷ
            </button>
          ) : null}
          <button
            type="submit"
            disabled={sending || !spec || blocked}
            className={cn("t14m h-8 rounded-lg px-3 text-white disabled:opacity-50 max-sm:h-10 max-sm:flex-[2]", action.style === "destructive" ? "bg-bad hover:bg-bad/90" : "bg-primary hover:bg-primary/90")}
          >
            {sending ? "Đang gửi…" : action.title}
          </button>
        </div>
      </div>
    </form>
  );
}

/** Chữ nhắc khi ô chờ ô khác: "Chọn Dự án trước". */
function waitLabel(f: FieldSpec, visibleKeys: ReadonlySet<string>, labelOf: (k: string) => string): string {
  const src = Object.values(f.options_args ?? {}).concat(f.options_with ?? []).find((k) => visibleKeys.has(k));
  return src ? `Chọn ${labelOf(src).toLowerCase()} trước` : "Chọn…";
}

const sameJson = (a: Json | undefined, b: Json | undefined): boolean => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

const noSubscribe = () => () => undefined;
const noSnapshot = () => null;

/** Mở trang gọi (đường tương đối trong web) ở thẻ mới; false = trình duyệt chặn cửa sổ. */
function openCallPage(page: string): boolean {
  const w = window.open(page, "_blank");
  if (!w) return false;
  w.opener = null;
  return true;
}

/** Bảng nhiều dòng (kind "rows", vd các việc của kế hoạch): dòng 1 = ô đầu (tên) + nút bỏ; dòng 2 = các ô còn lại (người nhận, hạn).
 *  Ghi chú dài (textarea) không vẽ trong dòng gọn — trợ lý / form riêng điền. Như rowsView của app Mac. */
function RowsField({ f, value, dyn, bad, onChange }: { f: FieldSpec; value: Json | undefined; dyn: Record<string, Option[]>; bad: boolean; onChange: (v: Json | undefined) => void }) {
  const rows = rowsOf(value);
  const subs = (f.fields ?? []).filter((c) => !c.hidden);
  const [head, ...rest] = subs;
  const tail = rest.filter((c) => c.kind !== "textarea");
  const cell = (i: number, c: FieldSpec) => {
    const opts = c.options ?? dyn[`${f.key}.${c.key}`] ?? [];
    const cur = text(rows[i]?.[c.key]);
    const put = (v: Json | undefined) => onChange(setCell(rows, i, c.key, v));
    if (opts.length)
      return (
        <select key={c.key} aria-label={c.label} className={cn(selectClass, "min-w-0 flex-1")} value={cur} onChange={(e) => put(e.target.value || undefined)}>
          {!cur || !opts.some((o) => o.value === cur) ? <option value={cur}>{cur || "—"}</option> : null}
          {opts.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      );
    if (c.kind === "date")
      return <DatePicker key={c.key} label={c.label} placeholder={c.label} value={cur} size="sm" className="min-w-0 flex-1" onChange={(v) => put(v)} />;
    if (c.kind === "time")
      return <TimePicker key={c.key} label={c.label} placeholder={c.label} value={cur} size="sm" className="min-w-0 flex-1" onChange={(v) => put(v)} />;
    if (c.kind === "month")
      return <MonthPicker key={c.key} label={c.label} placeholder={c.label} value={cur} size="sm" className="min-w-0 flex-1" onChange={(v) => put(v)} />;
    return (
      <TextInput
        key={c.key}
        aria-label={c.label}
        className="min-w-0 flex-1"
        value={cur}
        placeholder={c.label}
        onChange={(e) => put(coerce(c, e.target.value))}
      />
    );
  };
  return (
    <div className="flex flex-col gap-2" aria-invalid={bad}>
      <span className="t12m flex items-center gap-1.5 text-muted-foreground">
        {f.label}
        <span className="t12m rounded-full bg-foreground/10 px-1.5 tabular-nums">{rows.length}</span>
      </span>
      {f.required && !rows.length ? <p className="t12 text-warn">Cần ít nhất một dòng</p> : null}
      {rows.map((_, i) => (
        <div key={i} className="flex flex-col gap-1.5 rounded-lg bg-accent/60 p-2">
          <div className="flex items-center gap-2">
            <span className="t12m w-5 shrink-0 text-right text-muted-foreground">{i + 1}</span>
            {head ? cell(i, head) : null}
            <button type="button" aria-label="Bỏ dòng này" className="t12m shrink-0 rounded-md px-1.5 text-bad hover:bg-bad/10" onClick={() => onChange(rows.filter((__, j) => j !== i))}>
              Bỏ
            </button>
          </div>
          {tail.length ? <div className="flex gap-2 pl-7">{tail.map((c) => cell(i, c))}</div> : null}
        </div>
      ))}
      <button type="button" className="t14m self-start rounded-md px-1 text-primary hover:underline" onClick={() => onChange([...rows, {}])}>
        + Thêm dòng
      </button>
      {f.help ? <p className="t12 text-muted-foreground">{f.help}</p> : null}
    </div>
  );
}

/** Ô gõ số / tiền: tiền có dấu chấm nghìn khi gõ (giữ con trỏ), đơn vị đ / % dán bên phải ô. */
function NumberInput({ id, f, value, bad, onChange, onBlur }: { id: string; f: FieldSpec; value: Json | undefined; bad: boolean; onChange: (v: Json | undefined) => void; onBlur: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const caret = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (caret.current !== null && ref.current) ref.current.setSelectionRange(caret.current, caret.current);
    caret.current = null;
  });
  const money = f.kind === "money";
  const unit = UNIT[f.kind];
  const shown = money ? groupMoney(text(value)) : text(value);
  return (
    <div className="relative">
      <TextInput
        ref={ref}
        id={id}
        value={shown}
        inputMode={money || f.kind === "percent" || f.kind === "number" ? "decimal" : undefined}
        placeholder={PLACEHOLDER[f.kind] ?? (f.example !== undefined ? text(f.example) : "")}
        className={cn(unit && "pr-8", money && "tabular-nums")}
        onChange={(e) => {
          let raw = e.target.value;
          if (money) {
            const g = groupMoney(raw);
            if (g !== raw) caret.current = caretAfter(g, raw.slice(0, e.target.selectionStart ?? raw.length).replace(/\D/g, "").length);
            raw = g;
          }
          onChange(coerce(f, raw) ?? (raw.trim() === "" ? undefined : raw));
        }}
        onBlur={onBlur}
        aria-invalid={bad}
      />
      {unit ? <span className="t12 pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground" aria-hidden>{unit}</span> : null}
    </div>
  );
}

function Field({
  f, value, options, loading = false, waitingFor = "", remote = false, search, bad, mark, chip, onChip, onChange, onBlur,
}: {
  f: FieldSpec;
  value: Json | undefined;
  options?: Option[];
  loading?: boolean;
  /** Chữ nhắc khi danh sách chờ một ô khác ("Chọn dự án trước"); trống ⇒ không chờ. */
  waitingFor?: string;
  remote?: boolean;
  search?: (q: string) => Promise<Option[]>;
  bad: boolean;
  mark: "none" | "star" | "optional";
  chip?: Chip;
  onChip: () => void;
  onChange: (v: Json | undefined) => void;
  onBlur: () => void;
}) {
  const id = `f-${f.key}`;
  const str = text(value);
  let control: React.ReactNode;
  // ô chữ có danh sách chọn từ server (options_query, vd kệ tài liệu, mã dự án) ⇒ ô chọn có tìm; danh sách cố định (Literal) ⇒ select thường
  const fromQuery = !!f.options_query && !f.options && f.kind !== "multiselect" && f.kind !== "bool";
  const hasList = !!options && options.length > 0;
  const kind = f.kind !== "multiselect" && f.kind !== "bool" && (hasList || (fromQuery && (!!waitingFor || loading))) ? "select" : f.kind;
  const doneChip = chip === "guess" || chip === "ok";
  if (doneChip && chipable(f)) {
    const day = f.kind === "date" ? parseDay(value) : null;
    const shown = day ? vnLong(day) : shownValue(f, value, options);
    control = (
      <button
        type="button"
        id={id}
        onClick={onChip}
        title="Bấm để sửa"
        className={cn(
          "t14 flex h-8 w-full items-center gap-2 rounded-lg border px-2.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
          chip === "guess" ? "border-primary/30 bg-primary/5 hover:bg-primary/10" : "border-border bg-surface hover:bg-accent",
        )}
      >
        {chip === "guess" ? <Sparkles className="size-3.5 shrink-0 text-primary" aria-label="Máy đoán" /> : <Check className="size-3.5 shrink-0 text-ok" aria-hidden />}
        <span className="min-w-0 flex-1 truncate">{shown}</span>
        <Pencil className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      </button>
    );
  } else {
    switch (kind) {
      case "select": {
        const opts = options ?? [];
        if (fromQuery) {
          control = (
            <Combobox
              id={id}
              value={str}
              options={opts}
              optional={!f.required}
              query={f.options_query}
              remote={remote}
              search={search}
              label={f.label}
              invalid={bad}
              disabled={!!waitingFor || (loading && !opts.length)}
              placeholder={waitingFor || (loading ? "Đang tải…" : f.required ? "Chọn…" : "—")}
              onChange={onChange}
              onClose={onBlur}
            />
          );
          break;
        }
        control = (
          <select id={id} className={selectClass} value={str} onChange={(e) => onChange(e.target.value === "" && !f.required ? undefined : e.target.value)} onBlur={onBlur} aria-invalid={bad}>
            {!f.required && !opts.some((o) => o.value === "") ? <option value="">—</option> : null}
            {/* ô bắt buộc chưa chọn: có dòng "Chọn…" thật, không để trình duyệt vẽ dòng đầu danh sách như đã chọn trong khi dữ liệu còn trống */}
            {f.required && !opts.some((o) => o.value === "") ? <option value="" disabled>Chọn…</option> : null}
            {opts.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        );
        break;
      }
      case "multiselect": {
        const cur = Array.isArray(value) ? value.map(text) : [];
        control = (
          <div className="flex max-h-44 flex-col gap-1 overflow-y-auto rounded-lg border border-border bg-field px-2.5 py-2" role="group" aria-labelledby={`${id}-l`}>
            {(options ?? []).map((o) => (
              <label key={o.value} className="t14 flex items-center gap-2">
                <input
                  type="checkbox"
                  className="size-4 accent-[var(--primary)]"
                  checked={cur.includes(o.value)}
                  onChange={(e) => onChange([...cur.filter((x) => x !== o.value), ...(e.target.checked ? [o.value] : [])])}
                />
                {o.label}
              </label>
            ))}
          </div>
        );
        break;
      }
      case "bool":
        if (f.required || (f.default !== undefined && f.default !== null)) {
          control = (
            <label className="t14 flex items-center gap-2">
              <input id={id} type="checkbox" className="size-4 accent-[var(--primary)]" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
              {f.label}
            </label>
          );
        } else {
          control = (
            <select id={id} className={selectClass} value={value === true ? "1" : value === false ? "0" : ""} onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value === "1")}>
              <option value="">Không đổi</option>
              <option value="1">Có</option>
              <option value="0">Không</option>
            </select>
          );
        }
        break;
      case "textarea":
        control = <Textarea id={id} value={str} rows={3} placeholder={f.example !== undefined ? text(f.example) : undefined} onChange={(e) => onChange(coerce(f, e.target.value) === undefined ? undefined : e.target.value)} onBlur={onBlur} aria-invalid={bad} />;
        break;
      case "date":
        control = <DatePicker id={id} value={str} invalid={bad} placeholder={f.placeholder || "Chọn ngày"} onChange={(v) => { onChange(v); onBlur(); }} />;
        break;
      case "time":
        control = <TimePicker id={id} value={str} invalid={bad} placeholder={f.placeholder || (f.example !== undefined ? `VD ${text(f.example)}` : "HH:MM")} onChange={(v) => { onChange(v); onBlur(); }} />;
        break;
      case "month":
        control = <MonthPicker id={id} value={str} invalid={bad} placeholder={f.placeholder || "Chọn tháng"} onChange={(v) => { onChange(v); onBlur(); }} />;
        break;
      case "money":
      case "percent":
      case "number":
        control = <NumberInput id={id} f={f} value={value} bad={bad} onChange={onChange} onBlur={onBlur} />;
        break;
      default:
        control = (
          <TextInput
            id={id}
            value={str}
            inputMode={f.kind === "phone" ? "tel" : undefined}
            type={f.kind === "email" ? "email" : "text"}
            placeholder={PLACEHOLDER[f.kind] ?? (f.example !== undefined ? text(f.example) : "")}
            onChange={(e) => onChange(coerce(f, e.target.value) ?? (e.target.value.trim() === "" ? undefined : e.target.value))}
            onBlur={onBlur}
            aria-invalid={bad}
          />
        );
    }
  }
  const isCheckbox = f.kind === "bool" && (f.required || (f.default !== undefined && f.default !== null));
  return (
    <div className="flex min-w-0 flex-col gap-1">
      {isCheckbox ? null : (
        <label id={`${id}-l`} htmlFor={id} className="t12m text-muted-foreground">
          {f.label}
          {mark === "star" && f.required ? <span className="ml-0.5" aria-label="bắt buộc">*</span> : null}
          {mark === "optional" && !f.required && f.kind !== "bool" ? <span className="t12 ml-1.5 font-normal text-muted-foreground/80">không bắt buộc</span> : null}
        </label>
      )}
      {control}
      {f.help ? <p className="t12 text-muted-foreground">{f.help}</p> : null}
      {bad && f.required && f.kind !== "bool" && isBlank(value) ? <p className="t12 text-bad" role="alert">{fromQuery || f.kind === "select" || f.kind === "date" || f.kind === "time" || f.kind === "month" ? "Chưa chọn" : "Chưa nhập"}</p> : null}
    </div>
  );
}


/** Cảnh báo sau khi lưu: chữ của server + các bản gần giống (mở để bấm "Thay bằng bản mới" trên bản cũ nếu đúng là bản mới). */
export function SavedWarningPanel({ warn, onOpen }: { warn: SavedWarning; onOpen?: (id: string, title: string) => void }) {
  return (
    <div className="flex flex-col gap-2 rounded-xl bg-warn/10 p-3" role="alert">
      <p className="t14">{warn.warning}</p>
      {warn.similar.length && onOpen ? (
        <ul className="flex flex-col gap-1.5">
          {warn.similar.map((x) => (
            <li key={x.id} className="flex items-center justify-between gap-2">
              <span className="t14m min-w-0 truncate">
                {x.title}
                {typeof x.ratio === "number" ? <span className="t12 ml-1.5 text-muted-foreground">giống {Math.round(x.ratio * 100)}%</span> : null}
              </span>
              <button type="button" className="t12m shrink-0 rounded-lg border border-border bg-surface px-2 py-1 hover:bg-accent" onClick={() => onOpen(x.id, x.title)}>
                Mở bản đang dùng
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

const summaryOf = (output: Json): string =>
  output && typeof output === "object" && !Array.isArray(output) && typeof output.summary === "string" ? output.summary : "";
