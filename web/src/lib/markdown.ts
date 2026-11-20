// Markdown gọn (đoạn, tiêu đề, gạch đầu dòng lồng nhau, bảng, đường kẻ) — như MarkdownText bên Swift. Không dùng HTML thô: chỉ
// sinh cây khối; phần vẽ ở components/markdown.tsx (React tự thoát ký tự ⇒ không XSS).

export type MdBlock =
  | { kind: "heading"; text: string; level: number }
  | { kind: "bullet"; marker: string; text: string; level: number }
  | { kind: "para"; text: string }
  | { kind: "table"; rows: string[][] }
  | { kind: "rule" };

const BULLET = /^([-*•◦]|\d+[.)])\s+/;
const TABLE_SEP = /^\|[\s:\-|]+\|?$/;
const RULE = /^[-*_]{3,}$/;

export function parseMarkdown(text: string): MdBlock[] {
  const lines = text.replace(/\r/g, "").split("\n");
  const indents = [...new Set(lines.filter((l) => BULLET.test(l.trim())).map((l) => l.length - l.trimStart().length))].sort((a, b) => a - b);
  const out: MdBlock[] = [];
  let table: string[][] = [];
  const flush = () => {
    if (table.length) out.push({ kind: "table", rows: table });
    table = [];
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith("|")) {
      if (!TABLE_SEP.test(line)) table.push(line.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
      continue;
    }
    flush();
    if (!line) continue;
    if (RULE.test(line)) out.push({ kind: "rule" });
    else if (line.startsWith("#")) {
      const level = line.length - line.replace(/^#+/, "").length;
      out.push({ kind: "heading", text: line.replace(/^#+\s*/, ""), level });
    } else {
      const m = BULLET.exec(line);
      if (m) {
        const level = Math.max(0, indents.indexOf(raw.length - raw.trimStart().length));
        const marker = /^\d/.test(m[1]) ? m[1] : (["•", "◦", "–"][Math.min(level, 2)] ?? "•");
        out.push({ kind: "bullet", marker, text: line.slice(m[0].length), level });
      } else out.push({ kind: "para", text: line });
    }
  }
  flush();
  return out;
}

export type Inline = { kind: "text" | "bold" | "code" | "link"; text: string; href?: string };

/** **đậm**, `mã`, [chữ](https://…). Không có nghiêng (thang chữ: không nghiêng). */
export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  const re = /\*\*([^*]+)\*\*|\*([^*\n]+)\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push({ kind: "text", text: s.slice(last, m.index) });
    if (m[1] !== undefined) out.push({ kind: "bold", text: m[1] });
    else if (m[2] !== undefined) out.push({ kind: "text", text: m[2] }); // *nhấn mạnh* ⇒ chữ thường (không nghiêng)
    else if (m[3] !== undefined) out.push({ kind: "code", text: m[3] });
    else out.push({ kind: "link", text: m[4], href: m[5] });
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push({ kind: "text", text: s.slice(last) });
  return out;
}

/**
 * Đoạn chữ của mục chi tiết dạng bảng "a | b | c" (mỗi dòng một hàng, vd lịch thanh toán của bản tư vấn căn) ⇒ vẽ thành bảng. Cần ≥ 2 dòng
 * và mọi dòng đều có " | "; không thì null (đoạn chữ thường).
 */
export function pipeTable(text: string): string[][] | null {
  const lines = text.replace(/\r/g, "").split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2 || !lines.every((l) => l.includes(" | "))) return null;
  const rows = lines.map((l) => l.replace(/^\||\|$/g, "").split(" | ").map((c) => c.trim()));
  // hàng ngắn hơn tiêu đề (vd "Tổng cộng | 100% | 3.570.000.000") ⇒ chèn ô trống sau ô đầu để các số rơi đúng cột cuối
  const width = rows[0].length;
  return rows.map((r) => (r.length < width && r.length > 1 ? [r[0], ...Array<string>(width - r.length).fill(""), ...r.slice(1)] : r));
}
