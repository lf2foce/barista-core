// Văn bản Word sửa trong app (0227): giải mã khối `word` server gửi + phần THUẦN đổi "bản đã sửa trên màn" thành danh sách thay đổi gửi về máy chủ.
// Web chỉ vẽ và thu lại chữ người gõ; máy chủ áp thay đổi vào file gốc (không đổi Word → HTML → Word).

export type WordFormat = { b: boolean; i: boolean; u: boolean; s: boolean; sz: number | null; color: string; font: string; hl: string; va: string };
export type WordRun = WordFormat & { src: number; text: string; img: string | null; w: number; h: number };
export type WordParaStyle = {
  align: string; indL: number; indR: number; indFirst: number; spBefore: number; spAfter: number; line: number | null; linePt: number | null; heading: number;
  /** Đường kẻ ngang dưới đoạn (vd dưới tiêu ngữ — Nghị định 30): chỉ hiện. */
  rule?: boolean;
};
export type WordList = { label: string; level: number; indL: number; hang: number };
export type WordPara = { t: "p"; id: string; locked: string; style: WordParaStyle; list: WordList | null; runs: WordRun[]; base: WordFormat };
export type WordCell = { blocks: WordBlock[]; span: number; rowspan: number; fill: string; valign: string; borders: boolean | null };
export type WordTable = { t: "table"; id: string; cols: number[]; rows: { cells: WordCell[] }[]; borders: boolean; wPct: number | null };
export type WordBlock = WordPara | WordTable;
export type WordPage = { w: number; h: number; ml: number; mr: number; mt: number; mb: number };
export type WordDocRef = { id: string; title: string; version: number; updatedAt: string };
export type WordOps = { home: string; view: string; save: string; versions: string; restore: string; archive: string; rename: string; assist: string; create: string; send: string };
export type WordData = {
  documentId: string; title: string; version: number; editable: boolean; page: WordPage; blocks: WordBlock[]; notes: string[];
  docxUrl: string; pdfUrl: string; documents: WordDocRef[];
  /** Tên thao tác + đường tải lên do máy chủ gửi (web không viết cứng tên thao tác của gói). */
  ops: WordOps; uploadUrl: string;
  /** Gửi vào phòng: nơi gửi do máy chủ liệt kê (null = công ty chưa bật Tin nhắn / người này không dùng được ⇒ không vẽ nút). */
  share: { targets: { value: string; label: string }[] } | null;
  /** Cuộc trò chuyện với agent của văn bản (màn Soạn thảo) — server lưu, mở lại văn bản là thấy lại. */
  chat: { role: "user" | "agent"; text: string; note?: string }[];
};

const obj = (x: unknown): Record<string, unknown> => (x && typeof x === "object" && !Array.isArray(x) ? (x as Record<string, unknown>) : {});
const arr = (x: unknown): unknown[] => (Array.isArray(x) ? x : []);
const str = (x: unknown): string => (typeof x === "string" ? x : "");
const num = (x: unknown, d = 0): number => (typeof x === "number" && Number.isFinite(x) ? x : d);
const hex = (x: unknown): string => (typeof x === "string" && /^[0-9a-fA-F]{6}$/.test(x) ? x : "");

function format(o: Record<string, unknown>): WordFormat {
  return {
    b: o.b === true, i: o.i === true, u: o.u === true, s: o.s === true, sz: typeof o.sz === "number" && o.sz > 0 ? o.sz : null,
    color: hex(o.color), font: str(o.font).replace(/[^\p{L}\p{N} _-]/gu, "").slice(0, 60), hl: str(o.hl).replace(/[^a-zA-Z]/g, ""), va: o.va === "sup" || o.va === "sub" ? o.va : "",
  };
}

function block(x: unknown, depth: number): WordBlock | null {
  const o = obj(x);
  const id = str(o.id);
  if (!/^[a-z0-9.]{1,120}$/.test(id) || depth > 6) return null;
  if (o.t === "table") {
    return {
      t: "table", id, borders: o.borders === true, wPct: typeof o.w_pct === "number" && o.w_pct > 0 && o.w_pct <= 100 ? o.w_pct : null, cols: arr(o.cols).map((c) => num(c)).filter((c) => c > 0),
      rows: arr(o.rows).map((r) => ({
        cells: arr(obj(r).cells).map((c) => {
          const co = obj(c);
          return {
            blocks: arr(co.blocks).map((b) => block(b, depth + 1)).filter((b): b is WordBlock => b !== null),
            span: Math.max(1, Math.trunc(num(co.span, 1))), rowspan: Math.max(1, Math.trunc(num(co.rowspan, 1))), fill: hex(co.fill),
            valign: co.valign === "center" || co.valign === "bottom" ? co.valign : "",
            borders: typeof co.borders === "boolean" ? co.borders : null,
          };
        }),
      })),
    };
  }
  if (o.t !== "p") return null;
  const st = obj(o.style);
  const li = o.list ? obj(o.list) : null;
  return {
    t: "p", id, locked: str(o.locked),
    style: {
      align: ["left", "center", "right", "justify"].includes(str(st.align)) ? str(st.align) : "", indL: num(st.ind_l), indR: num(st.ind_r), indFirst: num(st.ind_first),
      spBefore: num(st.sp_before), spAfter: num(st.sp_after), line: typeof st.line === "number" && st.line > 0 ? st.line : null,
      linePt: typeof st.line_pt === "number" && st.line_pt > 0 ? st.line_pt : null, heading: Math.trunc(num(st.heading)),
      ...(st.rule === true ? { rule: true } : {}),
    },
    list: li ? { label: str(li.label).slice(0, 40), level: Math.trunc(num(li.level)), indL: num(li.ind_l), hang: num(li.hang) } : null,
    runs: arr(o.runs).map((r) => {
      const ro = obj(r);
      const img = typeof ro.img === "string" ? (ro.img.startsWith("data:image/") ? ro.img : "") : null;
      return { ...format(ro), src: typeof ro.src === "number" ? Math.trunc(ro.src) : -1, text: str(ro.text), img, w: num(ro.w), h: num(ro.h) };
    }),
    base: format(obj(o.base)),
  };
}

/** Giải mã khối `word` (phòng thủ: trường thiếu có mặc định, khối lạ bị bỏ — hợp đồng chỉ được THÊM). */
export function decodeWord(x: unknown): WordData {
  const o = obj(x);
  const pg = obj(o.page);
  const ops = obj(o.ops);
  const op = (v: unknown): string => (/^[a-z_]+\.[a-z_]+$/.test(str(v)) ? str(v) : "");
  return {
    documentId: str(o.document_id), title: str(o.title), version: Math.trunc(num(o.version)), editable: o.editable === true,
    page: { w: num(pg.w, 595.3), h: num(pg.h, 841.9), ml: num(pg.ml, 72), mr: num(pg.mr, 72), mt: num(pg.mt, 72), mb: num(pg.mb, 72) },
    blocks: arr(o.blocks).map((b) => block(b, 0)).filter((b): b is WordBlock => b !== null),
    notes: arr(o.notes).map(str).filter(Boolean), docxUrl: str(o.docx_url), pdfUrl: str(o.pdf_url),
    chat: arr(o.chat).map(obj).filter((t) => (t.role === "user" || t.role === "agent") && str(t.text))
      .map((t) => ({ role: t.role as "user" | "agent", text: str(t.text), ...(str(t.note) ? { note: str(t.note) } : {}) })),
    ops: { home: op(ops.home), view: op(ops.view), save: op(ops.save), versions: op(ops.versions), restore: op(ops.restore), archive: op(ops.archive), rename: op(ops.rename), assist: op(ops.assist), create: op(ops.create), send: op(ops.send) },
    uploadUrl: /^\/v1\/[a-z0-9_/-]+$/.test(str(o.upload_url)) ? str(o.upload_url) : "",
    share: o.share && typeof o.share === "object" ? {
      targets: arr(obj(o.share).targets).map((t) => ({ value: str(obj(t).value), label: str(obj(t).label) })).filter((t) => t.value && t.label),
    } : null,
    documents: arr(o.documents).map((d) => {
      const dd = obj(d);
      return { id: str(dd.id), title: str(dd.title), version: Math.trunc(num(dd.version)), updatedAt: str(dd.updated_at) };
    }).filter((d) => d.id),
  };
}

// ── Từ bản đã sửa trên màn ⇒ danh sách thay đổi ──

/** Một lượt chữ đọc lại từ màn: `pid` + `src` = đoạn và lượt chữ GỐC mà nó kế thừa định dạng (-1 = không rõ). */
export type EditedRun = {
  pid: string; src: number; text: string; b: boolean; i: boolean; u: boolean;
  /** Cỡ (pt) / màu (hex) / phông đọc được trên màn; thiếu = trang giấy không nêu ⇒ như lượt gốc. */
  sz?: number | null; color?: string | null; font?: string | null;
};
/** Một đoạn đọc lại từ màn: `id` = mã đoạn gốc gắn trên phần tử (trình duyệt chép mã khi bấm Enter ⇒ có thể trùng), null = đoạn mới hẳn. */
/** `align` / `line` (bội số giãn dòng) / `indent` (thụt lề trái, pt): chỉ có khi NGƯỜI SỬA đã bấm đổi ở đoạn đó. */
export type EditedPara = { id: string | null; runs: EditedRun[]; align?: WordAlign | null; line?: number | null; indent?: number | null };
/** Một VÙNG SỬA: dãy đoạn sửa được liền nhau trong thân văn bản / một ô bảng. `ids` = mã các đoạn gốc của vùng, theo thứ tự. */
export type Segment = { ids: string[]; paras: EditedPara[] };
export type OpRun = { src: number; text: string; b: boolean; i: boolean; u: boolean; sz: number | null; color: string | null; font: string | null };
export type WordAlign = "left" | "center" | "right" | "justify";
export type WordOp =
  | { op: "set"; id: string; runs: OpRun[] }
  | ({ op: "insert"; after: string; runs: OpRun[] } & ParaProps)
  | ({ op: "insert"; before: string; runs: OpRun[] } & ParaProps)
  | { op: "delete"; id: string }
  | ({ op: "para"; id: string } & ParaProps);
export type ParaProps = { align?: WordAlign; line?: number; indent?: number };

/** Gộp các mảnh liền nhau cùng nguồn + cùng định dạng (trình duyệt cắt vụn chữ khi gõ); bỏ mảnh rỗng. `owner` = đoạn mà `src` được tính theo. */
export function normalize(runs: EditedRun[], owner: string, orig?: WordPara): OpRun[] {
  const out: OpRun[] = [];
  for (const r of runs) {
    if (!r.text) continue;
    const src = r.pid === owner ? r.src : -1;
    const base = src >= 0 ? orig?.runs.find((o) => o.img === null && o.src === src) : undefined;   // trang giấy không nêu ⇒ lấy của lượt gốc
    const next: OpRun = {
      src, text: r.text, b: r.b, i: r.i, u: r.u,
      sz: pick(r.sz ?? null, base?.sz ?? null, PAGE.sz), color: pick(r.color ? r.color.toUpperCase() : null, base?.color || null, PAGE.color),
      font: pick(r.font || null, base?.font || null, PAGE.font),
    };
    const last = out[out.length - 1];
    if (last && sameFormat(last, next)) last.text += next.text;
    else out.push(next);
  }
  return out;
}

/** Mặc định của trang giấy khi vẽ: lượt chữ không nêu cỡ / màu / phông thì trình duyệt vẽ bằng các giá trị này. */
export const PAGE = { sz: 11, color: "000000", font: "Times New Roman" } as const;

/**
 * Giá trị một thuộc tính chữ để so với bản gốc: màn không đọc được ⇒ như lượt gốc; lượt gốc không nêu (theo kiểu của file) mà màn đang vẽ đúng mặc định
 * của trang ⇒ vẫn là "không nêu" (không đổi gì); khác đi ⇒ giá trị đang hiển thị.
 */
function pick<T>(seen: T | null, base: T | null, page: T): T | null {
  if (seen === null) return base;
  if (base === null && seen === page) return null;
  return seen;
}

function sameFormat(a: OpRun, b: OpRun): boolean {
  return a.src === b.src && a.b === b.b && a.i === b.i && a.u === b.u && a.sz === b.sz && a.color === b.color && a.font === b.font;
}

function same(a: OpRun[], b: OpRun[]): boolean {
  return a.length === b.length && a.every((x, k) => x.text === b[k].text && sameFormat(x, b[k]));
}

/** Lượt chữ gốc của một đoạn ở dạng so sánh được (chỉ lượt có chữ). */
export function originalRuns(p: WordPara): OpRun[] {
  return normalize(p.runs.filter((r) => r.img === null).map((r) => ({ pid: p.id, src: r.src, text: r.text, b: r.b, i: r.i, u: r.u, sz: r.sz, color: r.color, font: r.font })), p.id, p);
}

/** Thuộc tính đoạn người sửa đã đổi; có `orig` ⇒ chỉ giữ thứ KHÁC bản gốc. */
function paraProps(p: EditedPara, orig?: WordPara): ParaProps {
  const out: ParaProps = {};
  if (p.align && (!orig || p.align !== (orig.style.align || "left"))) out.align = p.align;
  if (p.line != null && (!orig || (orig.style.linePt !== null || Math.abs(p.line - (orig.style.line ?? 1)) > 0.01))) out.line = p.line;
  if (p.indent != null && (!orig || Math.abs(p.indent - orig.style.indL) > 0.5)) out.indent = p.indent;
  return out;
}

/** Mọi đoạn (kể cả trong ô bảng) theo mã. */
export function indexParas(blocks: WordBlock[], into: Map<string, WordPara> = new Map()): Map<string, WordPara> {
  for (const b of blocks) {
    if (b.t === "p") into.set(b.id, b);
    else for (const r of b.rows) for (const c of r.cells) indexParas(c.blocks, into);
  }
  return into;
}

/**
 * So từng vùng sửa với bản gốc ⇒ thay đổi. Đoạn mang mã gốc (lần gặp ĐẦU) là chính đoạn đó: chữ / định dạng khác ⇒ `set`. Đoạn không mã hoặc mã lặp
 * (Enter tách đoạn) là đoạn MỚI: thêm sau đoạn gốc đứng ngay trước nó, không có thì thêm trước đoạn gốc kế tiếp. Mã gốc không còn trên màn ⇒ `delete`.
 * Đoạn mang `align` / `line` / `indent` (người bấm đổi) khác bản gốc ⇒ thêm `para`.
 * Vùng không còn đoạn gốc nào ⇒ đoạn đầu trên màn nhận lại mã gốc đầu tiên (không xoá sạch rồi thêm — đoạn gốc giữ định dạng đoạn).
 */
export function buildOps(paras: Map<string, WordPara>, segments: Segment[]): WordOp[] {
  const ops: WordOp[] = [];
  const deletes: WordOp[] = [];
  for (const seg of segments) {
    const ids = seg.ids.filter((id) => paras.has(id));
    if (!ids.length) continue;
    const edited = seg.paras.map((p) => ({ ...p }));
    const first = new Set<string>();
    let any = false;
    for (const p of edited) {
      if (p.id && ids.includes(p.id) && !first.has(p.id)) {
        first.add(p.id);
        any = true;
      } else p.id = null;
    }
    if (!any) {
      if (edited.length) edited[0].id = ids[0];
      else edited.push({ id: ids[0], runs: [] });
      first.add(ids[0]);
    }
    let prev: string | null = null;
    edited.forEach((p, k) => {
      if (p.id) {
        const orig = paras.get(p.id)!;
        const now = normalize(p.runs, p.id, orig);
        if (!same(now, originalRuns(orig))) ops.push({ op: "set", id: p.id, runs: now });
        const props = paraProps(p, orig);
        if (Object.keys(props).length) ops.push({ op: "para", id: p.id, ...props });
        prev = p.id;
        return;
      }
      const props = paraProps(p);   // đoạn mới chép định dạng đoạn mốc; người sửa đã đổi gì ở đoạn này thì gửi kèm
      if (prev) ops.push({ op: "insert", after: prev, runs: normalize(p.runs, prev, paras.get(prev)), ...props });
      else {
        const next = edited.slice(k + 1).find((q) => q.id)?.id;
        if (next) ops.push({ op: "insert", before: next, runs: normalize(p.runs, next, paras.get(next)), ...props });
      }
    });
    for (const id of ids) if (!first.has(id)) deletes.push({ op: "delete", id });
  }
  return [...ops, ...deletes];
}

// ── Trợ lý soạn thảo (đề xuất của model; người áp dụng trên trang giấy rồi tự lưu) ──

/** `why`: khi RÀ SOÁT — một câu nêu lỗi đã sửa (trợ lý soạn thường để trống). */
export type AssistEdit = { id: string; before: string; text: string; why: string };
export type AssistAddition = { after: string; text: string };
/** Chỗ RÀ SOÁT nghi sai mà trợ lý không tự sửa (không biết giá trị nào đúng): người viết tự kiểm. ``quote`` = cụm chữ nghi sai trong đoạn ("" ⇒ cả đoạn). */
export type AssistCheck = { id: string; why: string; quote: string };
export type AssistResult = { reply: string; edits: AssistEdit[]; additions: AssistAddition[]; checks: AssistCheck[] };

/** Giải mã kết quả `assist` (phòng thủ: mục thiếu mã / chữ bị bỏ). */
export function decodeAssist(x: unknown): AssistResult {
  const o = obj(x);
  return {
    reply: str(o.reply),
    edits: arr(o.edits).map((e) => ({ id: str(obj(e).id), before: str(obj(e).before), text: str(obj(e).text), why: str(obj(e).why) })).filter((e) => e.id && e.text),
    additions: arr(o.additions).map((a) => ({ after: str(obj(a).after), text: str(obj(a).text) })).filter((a) => a.after && a.text),
    checks: arr(o.checks).map((c) => ({ id: str(obj(c).id), why: str(obj(c).why), quote: str(obj(c).quote) })).filter((c) => c.id && c.why),
  };
}

/** Chữ trợ lý trả về có nhiều dòng ⇒ mỗi dòng (bỏ dòng trống, cắt khoảng trắng hai đầu) là một ĐOẠN riêng trên trang giấy — không dồn thành xuống dòng
 *  trong một đoạn (Word hiểu là một đoạn có ngắt dòng, khác hẳn khi in / đánh số). Chữ một dòng ⇒ một đoạn. */
export function splitParagraphs(text: string): string[] {
  const parts = text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  return parts.length ? parts : [text.trim()];
}

// ── Rà soát: so chữ cũ / mới theo từ (thẻ đề xuất gạch đỏ phần bỏ, tô xanh phần thêm; trang giấy gạch dưới đúng chỗ sai) ──

export type DiffPart = { kind: "same" | "del" | "ins"; text: string };

/** So hai chữ theo TỪ (khoảng trắng là một mảnh riêng). Mỗi cụm khác nhau ra đúng một "del" rồi một "ins" (khoảng trắng đơn lẻ kẹp giữa hai chỗ đổi gộp vào
 *  cụm — không vỡ vụn). Chữ quá dài để so từng từ ⇒ bỏ cả phần giữa, thêm cả phần giữa. */
export function diffWords(a: string, b: string): DiffPart[] {
  const ta = a.match(/\s+|\S+/g) ?? [];
  const tb = b.match(/\s+|\S+/g) ?? [];
  let pre = 0;
  while (pre < ta.length && pre < tb.length && ta[pre] === tb[pre]) pre++;
  let suf = 0;
  while (suf < ta.length - pre && suf < tb.length - pre && ta[ta.length - 1 - suf] === tb[tb.length - 1 - suf]) suf++;
  const A = ta.slice(pre, ta.length - suf);
  const B = tb.slice(pre, tb.length - suf);
  const raw: DiffPart[] = [];
  if (A.length * B.length > 250_000) {
    raw.push({ kind: "del", text: A.join("") }, { kind: "ins", text: B.join("") });
  } else {
    const n = A.length, m = B.length;
    const L = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (A[i] === B[j]) {
        raw.push({ kind: "same", text: A[i++] });
        j++;
      } else if (L[i + 1][j] >= L[i][j + 1]) raw.push({ kind: "del", text: A[i++] });
      else raw.push({ kind: "ins", text: B[j++] });
    }
    while (i < n) raw.push({ kind: "del", text: A[i++] });
    while (j < m) raw.push({ kind: "ins", text: B[j++] });
  }
  // khoảng trắng "giống" kẹp giữa hai chỗ đổi ⇒ thuộc cụm đổi (bỏ ở bên cũ, thêm ở bên mới)
  const mid: DiffPart[] = [];
  raw.forEach((x, k) => {
    if (x.kind === "same" && /^\s+$/.test(x.text) && raw[k - 1] && raw[k - 1].kind !== "same" && raw[k + 1] && raw[k + 1].kind !== "same") {
      mid.push({ kind: "del", text: x.text }, { kind: "ins", text: x.text });
    } else mid.push(x);
  });
  const out: DiffPart[] = [];
  const same = (text: string) => { if (text) out.push({ kind: "same", text }); };
  same(ta.slice(0, pre).join(""));
  for (let k = 0; k < mid.length;) {
    if (mid[k].kind === "same") {
      let t = "";
      while (k < mid.length && mid[k].kind === "same") t += mid[k++].text;
      same(t);
      continue;
    }
    let del = "", ins = "";
    while (k < mid.length && mid[k].kind !== "same") {
      if (mid[k].kind === "del") del += mid[k].text;
      else ins += mid[k].text;
      k++;
    }
    // khoảng trắng chung ở đầu / cuối cụm (cả hai bên đều có) là phần giữ nguyên — không gạch
    const head = del && ins ? (del.match(/^\s*/)![0].length <= ins.match(/^\s*/)![0].length ? del.match(/^\s*/)![0] : ins.match(/^\s*/)![0]) : "";
    del = del.slice(head.length);
    ins = ins.slice(head.length);
    const dt = del.match(/\s*$/)![0], it = ins.match(/\s*$/)![0];
    const tail = del && ins ? (dt.length <= it.length ? dt : it) : "";
    same(head);
    if (del) out.push({ kind: "del", text: del.slice(0, del.length - tail.length) });
    if (ins) out.push({ kind: "ins", text: ins.slice(0, ins.length - tail.length) });
    same(tail);
  }
  same(ta.slice(ta.length - suf).join(""));
  // gộp "same" liền nhau (đầu / cuối nối với phần giữa)
  return out.reduce<DiffPart[]>((acc, x) => {
    const last = acc[acc.length - 1];
    if (last && last.kind === "same" && x.kind === "same") last.text += x.text;
    else acc.push({ ...x });
    return acc;
  }, []);
}

/** Khoảng [đầu, cuối) trong chữ CŨ cần gạch trên trang giấy: phần bị bỏ (cắt khoảng trắng hai đầu); chỗ chỉ THÊM chữ ⇒ gạch từ đứng ngay trước (đầu đoạn ⇒
 *  từ ngay sau) để người thấy chỗ đó. */
export function changedSpans(parts: DiffPart[]): [number, number][] {
  const a = parts.filter((x) => x.kind !== "ins").map((x) => x.text).join("");
  const spans: [number, number][] = [];
  let off = 0;
  parts.forEach((x, k) => {
    if (x.kind === "del") {
      const lead = x.text.length - x.text.trimStart().length;
      const body = x.text.trim().length;
      if (body) spans.push([off + lead, off + lead + body]);
      else spans.push(wordAt(a, off));
      off += x.text.length;
    } else if (x.kind === "ins") {
      if (parts[k - 1]?.kind !== "del") spans.push(wordAt(a, off));
    } else off += x.text.length;
  });
  return spans.filter(([s, e]) => e > s);
}

/** Từ đứng ngay trước vị trí ``off`` (bỏ khoảng trắng); không có ⇒ từ ngay sau. */
function wordAt(a: string, off: number): [number, number] {
  let e = off;
  while (e > 0 && /\s/.test(a[e - 1])) e--;
  if (e > 0) {
    let s = e;
    while (s > 0 && !/\s/.test(a[s - 1])) s--;
    return [s, e];
  }
  let s = off;
  while (s < a.length && /\s/.test(a[s])) s++;
  let t = s;
  while (t < a.length && !/\s/.test(a[t])) t++;
  return [s, t];
}

/** Rút gọn phần GIỐNG nhau dài trên thẻ (đoạn dài chỉ sai một chữ): giữ ``keep`` ký tự sát chỗ đổi, phần xa thay bằng "…". */
export function clipSame(parts: DiffPart[], keep = 40): DiffPart[] {
  return parts.map((x, k) => {
    if (x.kind !== "same" || x.text.length <= keep * 2 + 10) return x;
    const first = k === 0, last = k === parts.length - 1;
    if (first && last) return x;
    if (first) return { ...x, text: `…${x.text.slice(-keep).replace(/^\S*/, "")}` };
    if (last) return { ...x, text: `${x.text.slice(0, keep).replace(/\S*$/, "")}…` };
    return { ...x, text: `${x.text.slice(0, keep).replace(/\S*$/, "")}… ${x.text.slice(-keep).replace(/^\S*/, "")}` };
  });
}
