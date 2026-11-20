/** Bỏ dấu tiếng Việt + hạ chữ thường để so khớp "viec hom nay" với "Việc hôm nay". */
export function foldVietnamese(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase();
}
