import type { StreamEvent } from "./types";

/** Tách NDJSON từ các mảnh byte bất kỳ: giữ phần dòng dở cho lần sau; dòng hỏng bị bỏ qua. */
export class NdjsonParser {
  private buf = "";
  private dec = new TextDecoder();

  push(chunk: Uint8Array | string): StreamEvent[] {
    this.buf += typeof chunk === "string" ? chunk : this.dec.decode(chunk, { stream: true });
    const out: StreamEvent[] = [];
    let i: number;
    while ((i = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, i).trim();
      this.buf = this.buf.slice(i + 1);
      const ev = parseLine(line);
      if (ev) out.push(ev);
    }
    return out;
  }

  flush(): StreamEvent[] {
    const ev = parseLine(this.buf.trim());
    this.buf = "";
    return ev ? [ev] : [];
  }
}

export function parseLine(line: string): StreamEvent | null {
  if (!line) return null;
  try {
    const v = JSON.parse(line) as unknown;
    return typeof v === "object" && v !== null && typeof (v as { type?: unknown }).type === "string" ? (v as StreamEvent) : null;
  } catch {
    return null;
  }
}
