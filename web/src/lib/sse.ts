/** Đọc dòng SSE theo khúc (khúc có thể cắt giữa dòng): mỗi sự kiện = `event:` + `data:` (JSON) kết thúc bằng dòng trống. Dòng giữ kết nối (`:`) bỏ qua. */
export type SseEvent = { event: string; data: Record<string, unknown> };

export class SseParser {
  private buf = "";
  private event = "";
  private data = "";

  push(chunk: string): SseEvent[] {
    this.buf += chunk;
    const out: SseEvent[] = [];
    let i: number;
    while ((i = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, i).replace(/\r$/, "");
      this.buf = this.buf.slice(i + 1);
      if (line === "") {
        if (this.event) out.push({ event: this.event, data: parse(this.data) });
        this.event = "";
        this.data = "";
      } else if (line.startsWith("event:")) this.event = line.slice(6).trim();
      else if (line.startsWith("data:")) this.data += line.slice(5).trim();
    }
    return out;
  }
}

function parse(s: string): Record<string, unknown> {
  try {
    const v: unknown = JSON.parse(s || "{}");
    return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** Mã luồng trong tín hiệu hộp tin (`{"threads": [...]}`) — rỗng = "đọc lại cả hộp". */
export function signalThreads(data: Record<string, unknown>): string[] {
  return Array.isArray(data.threads) ? data.threads.filter((x): x is string => typeof x === "string") : [];
}

/**
 * Việc app làm với một sự kiện của kênh tức thì (0115). Chỉ `gone` là mất quyền đọc luồng (thôi hiện tin); `auth` = phiên hết hạn (đăng nhập
 * lại); `retry` = lỗi tạm thời phía server (nối lại sau, giữ nguyên tin đang hiện). Sự kiện lạ ⇒ bỏ qua.
 */
export function signalAction(event: string): "changed" | "gone" | "auth" | "retry" | "ignore" {
  return event === "changed" || event === "gone" || event === "auth" || event === "retry" ? event : "ignore";
}

/** Mã HTTP khi nối kênh: 404 = mất quyền (dừng hẳn), 401 = đăng nhập lại, còn lại (503, mạng) = nối lại sau. */
export function connectAction(status: number): "ok" | "gone" | "auth" | "retry" {
  return status === 200 ? "ok" : status === 404 ? "gone" : status === 401 ? "auth" : "retry";
}
