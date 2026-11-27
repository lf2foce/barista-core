/** Chữ đếm ở đầu thẻ gọn trong khung trò chuyện. Tổng của thẻ có thể cộng lẫn nhiều loại khối (cảnh báo, thẻ phòng, dòng việc) nên KHÔNG hiện "đang hiện / tổng"
 *  ("3/14" khiến người đọc đoán mò) — chỉ nói còn bao nhiêu mục chưa hiện; hiện đủ ⇒ không ghi gì; không biết tổng ⇒ "N dòng đầu". */
export function compactCount(c: { shown: number; total?: number | null } | undefined): string {
  if (!c || !c.shown) return "";
  if (c.total == null) return `${c.shown} dòng đầu`;
  return c.total > c.shown ? `còn ${c.total - c.shown} mục` : "";
}
