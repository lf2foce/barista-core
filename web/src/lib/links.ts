import type { DetailLink } from "./types";

// Liên kết file của trang chi tiết (DetailPage.links). Đường dẫn là của core (/v1/…); trình duyệt đi qua BFF /api/core (cookie phiên).

/** PDF ⇒ mở xem ngay (inline, đúng trang); file khác ⇒ tải về. */
export function isInlinePdf(l: Pick<DetailLink, "filename" | "page">): boolean {
  return (l.filename ?? "").toLowerCase().endsWith(".pdf") || (typeof l.page === "number" && !l.filename);
}

/** URL qua BFF, kèm #page=N cho trình xem PDF của trình duyệt. Chỉ nhận đường dẫn /v1/… của core. */
export function linkUrl(l: Pick<DetailLink, "path" | "page">): string {
  const path = l.path.startsWith("/v1/") ? l.path : `/v1/${l.path.replace(/^\/+/, "")}`;
  return `/api/core${path}${l.page ? `#page=${l.page}` : ""}`;
}

/** Cụm để TÌM câu nguyên văn trong trình xem PDF của trình duyệt (#search=): cụm có chữ số dài nhất (số tiền, số hiệu, ngày) — đặc trưng và ít lệch
 *  khoảng trắng nhất; không có thì 4 chữ đầu. Trình duyệt không hỗ trợ #search thì bỏ qua — câu vẫn hiện ở đầu hộp xem để người đối chiếu. */
export function searchTerm(quote: string): string {
  const words = quote.replace(/[“”"|*]/g, " ").split(/\s+/).filter(Boolean);
  const numeric = words.filter((w) => w.length >= 4 && /\d/.test(w)).sort((a, b) => b.length - a.length);
  return (numeric[0] ?? words.slice(0, 4).join(" ")).replace(/[,.;:]+$/, "");
}

/** File gốc của một tài liệu kho (qua BFF), mở đúng trang + tìm câu. */
export function documentFileUrl(documentId: string, page: number | null, quote?: string): string {
  const parts = [page ? `page=${page}` : "", quote ? `search=${encodeURIComponent(searchTerm(quote))}` : ""].filter(Boolean);
  return `/api/core/v1/kb/documents/${encodeURIComponent(documentId)}/file${parts.length ? `#${parts.join("&")}` : ""}`;
}
