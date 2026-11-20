// Chứng từ thả vào ô chat (0072): PDF / Word ⇒ hộp tải lên — NGƯỜI chọn kệ (thấy rõ ai đọc), rồi mới tải vào kho tài liệu (core đọc chữ, bản scan
// do model chép — 0051) và điền sẵn câu mời trợ lý; người sửa / bấm gửi. Ảnh vẫn đi đường cũ (đọc trong lượt, không lưu — 0050).

const DOCUMENT_EXT = [".pdf", ".docx"];

export function isDocumentFile(name: string): boolean {
  const n = name.toLowerCase();
  return DOCUMENT_EXT.some((e) => n.endsWith(e));
}

/** Tên trong kho = tên file bỏ đuôi (như app Mac). */
export function documentTitle(name: string): string {
  return name.replace(/\.[^.]+$/, "") || name;
}

export function splitDrop<T extends { name: string }>(files: T[]): { documents: T[]; others: T[] } {
  return { documents: files.filter((f) => isDocumentFile(f.name)), others: files.filter((f) => !isDocumentFile(f.name)) };
}

export type Uploaded = { title: string; documentId: string };

/** Kết quả một lần tải lên: CHỈ "saved" (đã vào kho, có mã tài liệu) mới đưa vào câu mời trợ lý — chờ duyệt thì tài liệu chưa có (review 04/11). */
export function uploadOutcome(r: { status: string; output: unknown }): { kind: "saved"; documentId: string } | { kind: "pending" } | { kind: "failed" } {
  const out = (r.output ?? {}) as { document_id?: unknown };
  if (r.status === "ok" && typeof out.document_id === "string" && out.document_id) return { kind: "saved", documentId: out.document_id };
  if (r.status === "pending_approval") return { kind: "pending" };
  return { kind: "failed" };
}

/** Gợi ý loại do server soạn sẵn (``kb.suggest_type``): app chỉ VẼ ``chip`` theo ``tone`` và chèn ``prompt``; không tự suy, không tự chọn thay người. */
export type TypeHint = { applicable: boolean; chip: string; tone: string; prompt: string };

export function decodeTypeHint(raw: unknown): TypeHint {
  const h = (raw ?? {}) as Record<string, unknown>;
  const text = (k: string) => (typeof h[k] === "string" ? (h[k] as string) : "");
  return { applicable: h.applicable === true, chip: text("chip"), tone: text("tone"), prompt: text("prompt") };
}

/** Câu mời trợ lý: tên + mã tài liệu (trợ lý dùng thẳng mã, không phải đoán theo tên) + gợi ý loại server đã soạn; người sửa câu trước khi gửi. */
export function uploadedPrompt(items: Uploaded[], hints: Record<string, TypeHint> = {}): string {
  return items.length
    ? `Tôi vừa tải lên ${items.length} chứng từ: ${items
        .map((d) => `“${d.title}” (mã tài liệu ${d.documentId})${hints[d.documentId]?.prompt ? ` · ${hints[d.documentId].prompt}` : ""}`)
        .join(", ")}. `
    : "";
}

/** Kệ người này thêm được (kb.upload_shelves) — hộp tải lên KHÔNG chọn sẵn kệ nào. */
export type UploadShelf = { value: string; label: string; audience: string; everyone: boolean };

export function decodeShelves(raw: unknown): UploadShelf[] {
  return (Array.isArray(raw) ? raw : []).flatMap((x) => {
    const o = (x ?? {}) as Record<string, unknown>;
    return typeof o.value === "string" && o.value
      ? [{ value: o.value, label: typeof o.label === "string" ? o.label : o.value, audience: typeof o.audience === "string" ? o.audience : "", everyone: o.everyone === true }]
      : [];
  });
}

/** Bong bóng chat: mã tài liệu chỉ dành cho trợ lý, người đọc không cần chuỗi kỹ thuật (nội dung gửi đi giữ nguyên). */
export function withoutDocumentIds(text: string): string {
  return text.replace(/ \(mã tài liệu [0-9a-f-]{36}\)/g, "");
}
