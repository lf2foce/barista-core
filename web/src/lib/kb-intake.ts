// Thả file vào Kho tài liệu (0225): kiểu dữ liệu của POST /v1/kb/upload + phần logic thuần của hộp thả file (kiểm file, hàng đợi, bản sửa chờ xác nhận).
// Hợp đồng đầy đủ: docstring đầu backend/src/barista/modules/kb/intake.py (app iOS / macOS dùng cùng cửa). Chỉ THÊM trường.

export const MAX_FILE = 30 * 1024 * 1024;
export const EXTENSIONS = [".pdf", ".docx", ".txt", ".md", ".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".pptx", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".zip"];
export const ACCEPT = EXTENSIONS.join(",");

export type IntakeSuggestion = {
  effective_from?: string;
  effective_to?: string;
  evidence?: string;
  replaces_id?: string;
  replaces_title?: string;
  replaces_reason?: string;
};

export type IntakeDoc = {
  status: "added" | "duplicate";
  filename: string;
  document_id?: string;
  title?: string;
  kind?: string;
  shelf?: string;
  shelf_name?: string;
  shelf_everyone?: boolean;
  shelf_audience?: string;
  context_code?: string;
  effective_from?: string | null;
  suggestion?: IntakeSuggestion | null;
  similar?: { id: string; title: string; ratio?: number }[];
  replaced?: string | null;
  duplicate_of?: { id: string; title: string; added?: string };
  guessed?: string[];
  note?: string;
};

export type IntakeOut = { documents: IntakeDoc[]; skipped: { filename: string; reason: string }[]; added: number; duplicates: number; document_id: string | null };

export type IntakeOptions = {
  shelves: { value: string; label: string; audience?: string; everyone?: boolean }[];
  shelf: string;
  kinds: { value: string; label: string }[];
  contexts: { value: string; label: string }[];
  can_manage: boolean;
};

export function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i).toLowerCase();
}

/** Lý do file không nhận được (null = nhận). Server kiểm lại — đây chỉ để báo sớm, không phải cửa chặn. */
export function rejectReason(f: { name: string; size: number }): string | null {
  if (f.size === 0) return "File rỗng";
  if (f.size > MAX_FILE) return "File quá 30 MB";
  if (!EXTENSIONS.includes(extOf(f.name))) return `Chưa nhận loại file ${extOf(f.name) || "(không đuôi)"} — nhận PDF, Word, Excel, PowerPoint, ảnh, ZIP`;
  return null;
}

/** Ảnh dán từ clipboard thường không có tên ("image.png") — đặt tên theo giờ để file không trùng tên nhau. */
export function nameClipboardFile(f: File, now = new Date()): File {
  if (f.name && !/^image\.(png|jpe?g|webp)$/i.test(f.name)) return f;
  const p = (n: number) => String(n).padStart(2, "0");
  const ext = extOf(f.name) || ".png";
  return new File([f], `Ảnh dán ${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}-${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}${ext}`, { type: f.type });
}

export function sizeText(n: number): string {
  return n >= 1024 * 1024 ? `${(n / (1024 * 1024)).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

/** Chạy `work` cho từng phần tử, tối đa `limit` việc cùng lúc (thả 20 file không bắn 20 yêu cầu — mỗi file có thể tốn một lượt trợ lý). */
export async function pool<T>(items: T[], limit: number, work: (item: T) => Promise<void>): Promise<void> {
  let next = 0;
  const run = async () => {
    while (next < items.length) {
      const item = items[next++];
      await work(item);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, run));
}

/** Một tài liệu trong danh sách xác nhận: giá trị máy đã áp (`base`) + phần người sửa / bấm chấp nhận (`edit`). */
export type Review = {
  doc: IntakeDoc;
  title: string;
  kind: string;
  context_code: string;
  shelf: string;
  from: string; // "" = dùng ngay; có ngày ⇒ gửi kb.update_document
  acceptFrom: boolean;
  acceptReplace: boolean;
  saved: boolean;
  error?: string; // lý do server từ chối lần lưu gần nhất (vd bản cũ đã có bản thay) — hiện ngay trên thẻ
};

export function reviewOf(doc: IntakeDoc): Review {
  return { doc, title: doc.title ?? "", kind: doc.kind ?? "other", context_code: doc.context_code ?? "", shelf: doc.shelf ?? "", from: "", acceptFrom: false, acceptReplace: false, saved: false };
}

/** Đầu vào `kb.update_document` cho đúng phần khác với giá trị máy đã áp; null = không có gì phải gửi. */
export function updateInput(r: Review): Record<string, unknown> | null {
  const d = r.doc;
  if (d.status !== "added" || !d.document_id) return null;
  const out: Record<string, unknown> = { document_id: d.document_id };
  if (r.title.trim() && r.title.trim() !== (d.title ?? "")) out.title = r.title.trim();
  if (r.kind !== (d.kind ?? "other")) out.kind = r.kind;
  if (r.context_code !== (d.context_code ?? "")) {
    if (r.context_code) out.context_code = r.context_code;
    else out.no_context = true;
  }
  if (r.shelf && r.shelf !== (d.shelf ?? "")) out.shelf = r.shelf;
  const sg = d.suggestion;
  if (r.from) out.effective_from = r.from;
  else if (r.acceptFrom && sg?.effective_from) {
    out.effective_from = sg.effective_from;
    if (sg.effective_to) out.effective_to = sg.effective_to;
  }
  if (r.acceptReplace && sg?.replaces_id) out.replaces_document_id = sg.replaces_id;
  return Object.keys(out).length > 1 ? out : null;
}

/** Số đề xuất của máy còn chờ người chấp nhận (ngày hiệu lực, thay bản cũ). */
export function pendingProposals(r: Review): number {
  const sg = r.doc.suggestion;
  if (r.doc.status !== "added" || !sg || r.saved) return 0;
  return (sg.effective_from && !r.acceptFrom && !r.from ? 1 : 0) + (sg.replaces_id && !r.acceptReplace ? 1 : 0);
}

export function acceptAll(rs: Review[]): Review[] {
  return rs.map((r) => (r.doc.status === "added" && !r.saved ? { ...r, acceptFrom: !!r.doc.suggestion?.effective_from, acceptReplace: !!r.doc.suggestion?.replaces_id } : r));
}

/** Ngày KHÔNG nhập nhằng: "1 tháng 12, 2026" — "01/12/2026" là ngày 1 hay tháng 1 tuỳ nước (0169). */
export function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return y && m && d ? `${d} tháng ${m}, ${y}` : iso;
}

/** Đề xuất "thay bản cũ" trỏ vào một tài liệu CHÍNH LẦN THẢ NÀY vừa thêm (hai file cùng bộ giống tên nhau) không phải bản cũ thật ⇒ bỏ đề xuất đó. */
export function withoutSiblingReplace(rs: Review[]): Review[] {
  const fresh = new Set(rs.flatMap((r) => (r.doc.status === "added" && r.doc.document_id ? [r.doc.document_id] : [])));
  return rs.map((r) => {
    const sg = r.doc.suggestion;
    if (!sg?.replaces_id || !fresh.has(sg.replaces_id)) return r;
    const rest = { ...sg };
    delete rest.replaces_id;
    delete rest.replaces_title;
    delete rest.replaces_reason;
    return { ...r, doc: { ...r.doc, suggestion: Object.keys(rest).length ? rest : null } };
  });
}

/** Kệ có hạn chế người đọc không, và ai đọc được — để chip kệ hiện kiểu "hạn chế" (tài liệu không được lặng lẽ nằm ở kệ chỉ vài người thấy). */
export function shelfInfo(opts: IntakeOptions | null, key: string, fallback?: IntakeDoc): { restricted: boolean; audience: string; label: string } {
  const o = opts?.shelves.find((x) => x.value === key);
  if (o) return { restricted: o.everyone === false, audience: o.audience ?? "", label: o.label };
  return { restricted: fallback?.shelf_everyone === false, audience: fallback?.shelf_audience ?? "", label: fallback?.shelf_name ?? key };
}
