// Ảnh gửi trợ lý (quyết định 0050): model đọc TRONG LƯỢT ĐÓ, server không lưu byte ảnh. Web nén trước khi gửi: cạnh dài ≤ 2000 px, JPEG ~0,85.
// Giới hạn khớp core (api.py parse_images): tối đa 3 ảnh / câu, mỗi ảnh ≤ 5 MB sau giải mã, PNG / JPEG / WebP / HEIC.

export const IMAGE_MAX = 3;
export const IMAGE_BYTES = 5 * 1024 * 1024;
export const IMAGE_SIDE = 2000;
export const JPEG_QUALITY = 0.85;
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"];
export const IMAGE_ONLY_TEXT = "Cập nhật từ ảnh này giúp em";

export type ChatImage = { media_type: string; data: string; preview: string };

/** Kích thước sau khi thu nhỏ để cạnh dài ≤ `max` (giữ tỷ lệ, không phóng to). */
export function fitWithin(w: number, h: number, max = IMAGE_SIDE): { w: number; h: number; scaled: boolean } {
  const long = Math.max(w, h);
  if (long <= max || long <= 0) return { w, h, scaled: false };
  const k = max / long;
  return { w: Math.max(1, Math.round(w * k)), h: Math.max(1, Math.round(h * k)), scaled: true };
}

/** Số byte thật của một chuỗi base64 (không tính đệm "="). */
export function base64Bytes(b64: string): number {
  const s = b64.replace(/\s/g, "");
  const pad = s.endsWith("==") ? 2 : s.endsWith("=") ? 1 : 0;
  return Math.floor((s.length * 3) / 4) - pad;
}

export const isImageType = (t: string): boolean => IMAGE_TYPES.includes(t.toLowerCase());

/** Kiểm một ảnh đã mã hoá trước khi gửi — lời báo tiếng Việt như server. */
export function checkEncoded(img: { media_type: string; data: string }): string | null {
  if (!isImageType(img.media_type)) return "Chỉ nhận ảnh PNG, JPEG, WebP, HEIC";
  const n = base64Bytes(img.data);
  if (n <= 0) return "Ảnh không đọc được";
  if (n > IMAGE_BYTES) return "Mỗi ảnh tối đa 5 MB";
  return null;
}

/** Ghép ảnh mới vào danh sách đang chờ gửi, không quá 3; ảnh thừa bị bỏ kèm lời báo. */
export function addImages<T>(current: T[], incoming: T[], max = IMAGE_MAX): { images: T[]; dropped: number } {
  const room = Math.max(0, max - current.length);
  return { images: [...current, ...incoming.slice(0, room)], dropped: Math.max(0, incoming.length - room) };
}

/** Clipboard có FILE ảnh không (chỉ khi đó mới chặn dán — dán chữ / ô Excel vẫn như thường). */
export function imageFilesOf(items: { kind: string; type: string; getAsFile: () => File | null }[]): File[] {
  return items.filter((i) => i.kind === "file" && i.type.startsWith("image/")).flatMap((i) => (i.getAsFile() ? [i.getAsFile() as File] : []));
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).replace(/^data:[^,]*,/, ""));
    r.onerror = () => reject(r.error ?? new Error("Không đọc được ảnh"));
    r.readAsDataURL(blob);
  });
}

/**
 * Nén một ảnh (trình duyệt): cạnh dài ≤ 2000 px, JPEG 0,85 qua canvas. Trình duyệt không giải được (vd HEIC ngoài Safari) ⇒ gửi nguyên bản nếu
 * đúng loại và ≤ 5 MB, để model đọc. Lỗi ⇒ ném Error có lời tiếng Việt.
 */
export async function prepareImage(file: File): Promise<ChatImage> {
  let bitmap: ImageBitmap | null = null;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    bitmap = null;
  }
  if (!bitmap) {
    const t = (file.type || "").toLowerCase();
    if (!isImageType(t)) throw new Error("Chỉ nhận ảnh PNG, JPEG, WebP, HEIC");
    if (file.size > IMAGE_BYTES) throw new Error("Mỗi ảnh tối đa 5 MB");
    const data = await blobToBase64(file);
    return { media_type: t, data, preview: URL.createObjectURL(file) };
  }
  const { w, h } = fitWithin(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Không xử lý được ảnh");
  ctx.fillStyle = "#fff"; // ảnh PNG trong suốt ⇒ nền trắng khi thành JPEG
  ctx.fillRect(0, 0, w, h);
  ctx.drawImage(bitmap, 0, 0, w, h);
  bitmap.close();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
  if (!blob) throw new Error("Không nén được ảnh");
  const data = await blobToBase64(blob);
  const err = checkEncoded({ media_type: "image/jpeg", data });
  if (err) throw new Error(err);
  return { media_type: "image/jpeg", data, preview: URL.createObjectURL(blob) };
}
