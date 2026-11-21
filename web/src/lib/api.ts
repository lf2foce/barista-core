import { decodePage, ROUTE } from "./decode";
import { NdjsonParser } from "./ndjson";
import { connectAction, signalAction, SseParser } from "./sse";
import type { IntakeOut } from "./kb-intake";
import { primeDesk } from "./session";
import type {
  Attention,
  AuthConfig,
  ChatCard,
  CommandResult,
  DevLogin,
  FormSpec,
  ImportAnalysis,
  ImportPlan,
  ImportPlanReport,
  ImportReport,
  Input,
  Json,
  Manifest,
  DeskPage,
  McpRequest,
  NoticeInbox,
  Option,
  Page,
  SessionInfo,
  StreamEvent,
  ThreadSummary,
} from "./types";

// Máy khách của core qua BFF (/api/core/…). Trình duyệt chỉ giữ cookie httpOnly — không thấy mã phiên. Server quyết quyền mỗi request.

export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
    /** mã máy đọc từ core (tiêu đề `X-Barista-Error`, vd `not_member`) — quyết định theo mã, không theo chữ */
    public code = "",
  ) {
    super(message);
  }
}

/** Phiên không dùng được nữa (hết hạn / không còn là thành viên công ty đang chọn) ⇒ "Thử lại" vô ích, phải đăng nhập lại. */
export function sessionGone(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 401 || e.code === "not_member");
}

/** Phản hồi lỗi của core ⇒ ApiError mang câu cho người + mã cho máy. */
export async function failure(res: Response, fallback: string): Promise<ApiError> {
  return new ApiError(await detailOf(res, fallback), res.status, res.headers.get("x-barista-error") ?? "");
}

/** 401 từ BFF / core ⇒ phiên hết hạn: về trang đăng nhập (chỉ gọi được trong trình duyệt). */
let onUnauthorized: () => void = () => {
  // đăng nhập lại cần dựng lại toàn bộ trạng thái ⇒ điều hướng cả trang, không dùng router; kèm đường đang mở ⇒ đăng nhập xong quay lại
  // đúng chỗ (vd trang đồng ý kết nối ứng dụng AI — 0093); trang login lọc bằng safeNext
  if (typeof window !== "undefined" && window.location.pathname !== "/login")
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
};
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

const BASE = "/api/core";

async function raw(path: string, init: RequestInit = {}): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(BASE + path, { credentials: "same-origin", cache: "no-store", ...init });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError("Không kết nối được máy chủ — kiểm tra mạng rồi thử lại");
  }
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError(await detailOf(res, "Phiên đăng nhập đã hết hạn — đăng nhập lại"), 401);
  }
  return res;
}

async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    const d = (await res.clone().json()) as { detail?: unknown; error?: unknown };
    if (typeof d.detail === "string") return d.detail;
    if (typeof d.error === "string" && d.error) return d.error;
  } catch {
    /* không phải JSON */
  }
  return fallback;
}

async function jsonOf<T>(res: Response): Promise<T> {
  if (res.status === 403 || res.status >= 500) throw await failure(res, `Máy chủ lỗi (${res.status})`);
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError(`Không đọc được phản hồi (${res.status})`, res.status);
  }
}

type Envelope<T> = { status: string; output: T | null; error: string };

async function envelope<T>(res: Response): Promise<T> {
  const env = await jsonOf<Envelope<T>>(res);
  if (env.status !== "ok" || env.output === null || env.output === undefined) throw new ApiError(env.error || `Không thực hiện được (${env.status})`, res.status);
  return env.output;
}

const post = (path: string, body: unknown, headers: Record<string, string> = {}, signal?: AbortSignal) =>
  raw(path, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body), signal });

/** Tên file cho header X-Filename: header HTTP chỉ nhận ASCII ⇒ mã hoá phần trăm (core giải mã, giữ nguyên tên tiếng Việt làm nguồn). */
export function headerFilename(name: string): string {
  return encodeURIComponent(name || "file");
}

export const api = {
  manifest: async (): Promise<Manifest> => jsonOf<Manifest>(await raw("/v1/manifest")),
  /** Đọc chính tả: đoạn ghi âm WAV ⇒ CHỈ chữ chép được (không chạy lượt trợ lý) — chèn vào ô nhập. */
  transcribe: async (audio: ArrayBuffer): Promise<string> => {
    const res = await raw("/v1/assistant/transcribe", { method: "POST", body: audio, headers: { "content-type": "audio/wav" } });
    if (!res.ok) throw new ApiError(await detailOf(res, `Không chép được lời (${res.status})`), res.status);
    return String(((await res.json()) as { text?: unknown }).text ?? "");
  },
  /** Dừng lượt chat đang chạy của chính mình (mã lượt gửi lúc hỏi) — server huỷ, phần đã làm vẫn lưu. */
  stopTurn: async (turnKey: string): Promise<void> => {
    await post("/v1/assistant/chat/stop", { turn_key: turnKey });
  },

  /** MCP chỉ đọc (0093): yêu cầu kết nối của một ứng dụng AI — ứng dụng nào, quyền gì, cho công ty đang chọn. */
  mcpRequest: async (id: string): Promise<McpRequest> => jsonOf<McpRequest>(await raw(`/v1/mcp/requests/${encodeURIComponent(id)}`)),
  /** Đồng ý / từ chối ⇒ địa chỉ quay về ứng dụng AI. */
  mcpApprove: async (id: string, approve: boolean): Promise<{ redirect: string }> =>
    jsonOf<{ redirect: string }>(await raw("/v1/mcp/approve", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ request_id: id, approve }) })),

  query: async (name: string, input: Input = {}, signal?: AbortSignal): Promise<Page> => decodePage(await envelope<unknown>(await post(`/v1/queries/${name}`, input, {}, signal))),
  /** Trang của màn: như `query` nhưng thẻ về ngay với phần nhanh — khối nặng là khối `later`, thẻ tự tải (`useLater` ở card.tsx). */
  page: async (name: string, input: Input = {}, signal?: AbortSignal, rows?: { offset: number; limit: number }): Promise<Page> =>
    decodePage(await envelope<unknown>(await post(`/v1/queries/${name}?later=1${rows ? `&limit=${rows.limit}&offset=${rows.offset}` : ""}`, input, {}, signal))),
  /** Một phần tải sau của thẻ (`ui.part` — khối `later` có `part`): server chạy lại phần nhanh, dựng đúng phần này. */
  part: async (name: string, input: Input, part: string, signal?: AbortSignal): Promise<Page> =>
    decodePage(await envelope<unknown>(await post(`/v1/queries/${name}?part=${encodeURIComponent(part)}`, input, {}, signal))),
  /** Thẻ gọn (0094): cùng truy vấn, cùng quyền — server cắt bớt (≤ 5 dòng, ≤ 3 cột, tổng thật nếu biết). */
  queryCompact: async (name: string, input: Input = {}, signal?: AbortSignal): Promise<Page> =>
    decodePage(await envelope<unknown>(await post(`/v1/queries/${name}?compact=1`, input, {}, signal))),

  /** Truy vấn trả dữ liệu thô (không phải trang): hộp thông báo… */
  queryRaw: async <T>(name: string, input: Input = {}): Promise<T> => envelope<T>(await post(`/v1/queries/${name}`, input)),

  /** `input`: giá trị các trường form mà danh sách phụ thuộc (`options_with`) — gửi qua query string. */
  options: async (name: string, input: Record<string, string> = {}): Promise<Option[]> => {
    const qs = new URLSearchParams(input).toString();
    return envelope<Option[]>(await raw(`/v1/options/${name}${qs ? `?${qs}` : ""}`));
  },

  form: async (command: string): Promise<FormSpec> => jsonOf<FormSpec>(await raw(`/v1/forms/${command}`)),

  /** Lệnh trả kết quả nghiệp vụ (kể cả bị từ chối) — chỉ ném lỗi khi mạng / máy chủ hỏng. */
  command: async (name: string, input: Input, key: string, route?: string, basis?: string): Promise<CommandResult> => {
    // `route`: lệnh có đường riêng của gói (vd chấm công — máy chủ đọc địa chỉ mạng của kết nối); cùng hình kết quả như đường lệnh chung
    // `basis`: căn cứ nút trang mang theo (0071 / 0251) — server so với hiện tại, khác ⇒ `conflict` ("xem lại"), không chạy
    const res = await post(route && ROUTE.test(route) ? route : `/v1/commands/${name}`, input, { "idempotency-key": key, ...(basis ? { "x-expect-basis": basis } : {}) });
    if (res.status === 403 && !(res.headers.get("content-type") ?? "").includes("json")) throw new ApiError(await detailOf(res, "Không có quyền"), 403);
    const body = await jsonOf<Partial<CommandResult> & { detail?: string }>(res);
    if (body.status === undefined) throw new ApiError(body.detail ?? `Không thực hiện được (${res.status})`, res.status);
    return { status: body.status, output: body.output ?? null, error: body.error ?? "", approval_id: body.approval_id ?? null, replayed: !!body.replayed };
  },

  xlsx: async (query: string, input: Input): Promise<Blob> => {
    const res = await post(`/v1/queries/${query}/xlsx`, input);
    if (!res.ok) throw new ApiError(await detailOf(res, `Xuất Excel lỗi (${res.status})`), res.status);
    return res.blob();
  },

  download: async (path: string): Promise<Blob> => {
    const res = await raw(path);
    if (!res.ok) throw new ApiError(await detailOf(res, `Không tải được file (${res.status})`), res.status);
    return res.blob();
  },

  template: (command: string): Promise<Blob> => api.download(`/v1/imports/${command}/template`),

  importXlsx: async (command: string, file: Blob, dryRun: boolean): Promise<ImportReport> => {
    const res = await raw(`/v1/imports/${command}/xlsx?dry_run=${dryRun}`, { method: "POST", body: file, headers: { "content-type": "application/octet-stream" } });
    if (res.status === 422) throw new ApiError(await detailOf(res, "Không đọc được file .xlsx"), 422);
    return envelope<ImportReport>(res);
  },

  /** Nhập tự khớp bước 1: file (+ trang) → các trang tính, dòng tiêu đề, ánh xạ cột đoán được. Chưa ghi gì. multipart: trình duyệt tự đặt ranh giới. */
  importAnalyze: async (command: string, file: File, sheet?: string): Promise<ImportAnalysis> => {
    const fd = new FormData();
    if (sheet) fd.append("sheet", sheet);
    fd.append("file", file, file.name);
    const res = await raw(`/v1/imports/${command}/analyze`, { method: "POST", body: fd });
    if (res.status === 413 || res.status === 422) throw new ApiError(await detailOf(res, "Không đọc được file"), res.status);
    return envelope<ImportAnalysis>(res);
  },

  /** Nhập tự khớp bước 2-3: chạy thử (`dryRun`) hoặc nhập thật theo ánh xạ đã xác nhận. */
  importPlan: async (command: string, file: File, plan: ImportPlan, dryRun: boolean): Promise<ImportPlanReport> => {
    const fd = new FormData();
    fd.append("plan", JSON.stringify(plan));
    fd.append("file", file, file.name);
    const res = await raw(`/v1/imports/${command}/plan?dry_run=${dryRun}`, { method: "POST", body: fd });
    if (res.status === 413 || res.status === 422) throw new ApiError(await detailOf(res, "Không đọc được file"), res.status);
    return envelope<ImportPlanReport>(res);
  },

  importRows: async (command: string, rows: Input[], dryRun: boolean): Promise<ImportReport> =>
    envelope<ImportReport>(await post(`/v1/imports/${command}?dry_run=${dryRun}`, { rows })),

  /**
   * Lối cũ của ô chat (chứng từ thả vào chat — người chọn kệ, tên theo file): tải lên kho có tham số rõ ràng (core đọc ra chữ — PDF scan do model đọc, có thể 10–30 giây — rồi chạy đúng lệnh kb.add_document).
   * Tên file đi qua header X-Filename, mã hoá phần trăm (header chỉ nhận ASCII; core giải mã lại — giữ tên tiếng Việt).
   */
  /** Tải một file .docx lên thành văn bản Word mới (0227) — thân request là file, tên file ở header (mã hoá phần trăm); đường tải lên do khối `word` của máy chủ nêu. */
  wordUpload: async (url: string, file: File): Promise<CommandResult> => {
    const res = await raw(url, {
      method: "POST",
      body: file,
      headers: { "content-type": "application/octet-stream", "x-filename": headerFilename(file.name) },
    });
    if (res.status === 413) throw new ApiError(await detailOf(res, "File quá lớn"), res.status);
    const body = await jsonOf<Partial<CommandResult> & { detail?: string }>(res);
    if (body.status === undefined) throw new ApiError(body.detail ?? `Không tải được (${res.status})`, res.status);
    return { status: body.status, output: body.output ?? null, error: body.error ?? "", approval_id: body.approval_id ?? null, replayed: !!body.replayed };
  },

  kbUpload: async (
    file: File,
    params: { title: string; shelf: string; kind: string; context_code?: string; effective_from?: string; replaces_document_id?: string },
    signal?: AbortSignal,
  ): Promise<CommandResult> => {
    const q = new URLSearchParams({ title: params.title, shelf: params.shelf, kind: params.kind, context_code: params.context_code ?? "" });
    // 0052: ngày bắt đầu hiệu lực (YYYY-MM-DD) + bản mới thay bản cũ — chỉ gửi khi có
    if (params.effective_from) q.set("effective_from", params.effective_from);
    if (params.replaces_document_id) q.set("replaces_document_id", params.replaces_document_id);
    const res = await raw(`/v1/kb/upload?${q}`, {
      method: "POST",
      body: file,
      signal,
      headers: { "content-type": "application/octet-stream", "x-filename": headerFilename(file.name) },
    });
    if (res.status === 413 || res.status === 422) throw new ApiError(await detailOf(res, "Không đọc được file"), res.status);
    const body = await jsonOf<Partial<CommandResult> & { detail?: string }>(res);
    if (body.status === undefined) throw new ApiError(body.detail ?? `Không tải được (${res.status})`, res.status);
    return { status: body.status, output: body.output ?? null, error: body.error ?? "", approval_id: body.approval_id ?? null, replayed: !!body.replayed };
  },

  /**
   * Thả MỘT file (hoặc ZIP) vào kho (0225) — không tham số nào bắt buộc: tên, loại, phạm vi, kệ do máy đoán. Dùng XHR để có tiến độ gửi
   * (fetch không báo). Tên file qua header X-Filename, mã hoá phần trăm (header chỉ nhận ASCII; core giải mã lại — giữ tên tiếng Việt).
   * Hợp đồng: docstring đầu backend/src/barista/modules/kb/intake.py.
   */
  kbDrop: (
    file: File,
    params: { replaces_document_id?: string; shelf?: string } = {},
    opts: { signal?: AbortSignal; onProgress?: (sent: number, total: number) => void; onSent?: () => void } = {},
  ): Promise<IntakeOut> =>
    new Promise((resolve, reject) => {
      const q = new URLSearchParams();
      if (params.replaces_document_id) q.set("replaces_document_id", params.replaces_document_id);
      if (params.shelf) q.set("shelf", params.shelf); // người đã CHỌN kệ trong hộp thả ⇒ các file sau theo kệ đó; không gửi ⇒ kệ chung
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/v1/kb/upload${q.size ? `?${q}` : ""}`);
      xhr.withCredentials = true;
      xhr.setRequestHeader("content-type", "application/octet-stream");
      xhr.setRequestHeader("x-filename", headerFilename(file.name));
      xhr.upload.onprogress = (e) => e.lengthComputable && opts.onProgress?.(e.loaded, e.total);
      xhr.upload.onload = () => opts.onSent?.();
      xhr.onerror = () => reject(new ApiError("Không kết nối được máy chủ — kiểm tra mạng rồi thử lại"));
      xhr.onabort = () => reject(new DOMException("Đã huỷ", "AbortError"));
      xhr.onload = () => {
        let body: { status?: string; output?: IntakeOut; error?: string; detail?: unknown } = {};
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* không phải JSON */
        }
        if (xhr.status === 401) {
          onUnauthorized();
          return reject(new ApiError(typeof body.detail === "string" ? body.detail : "Phiên đăng nhập đã hết hạn — đăng nhập lại", 401));
        }
        if (xhr.status === 200 && body.status === "ok" && body.output) return resolve(body.output);
        reject(new ApiError(typeof body.detail === "string" ? body.detail : body.error || `Không tải được (${xhr.status})`, xhr.status));
      };
      opts.signal?.addEventListener("abort", () => xhr.abort(), { once: true });
      xhr.send(file);
    }),

  /**
   * Gửi MỘT tệp vào trao đổi (0115): một tệp = một tin, chú thích tuỳ chọn. multipart/form-data — chú thích nằm trong THÂN, không lên URL / nhật ký
   * truy cập. Server kiểm cỡ + loại theo nội dung; cùng khoá ⇒ không thành hai tin.
   */
  talkUpload: async (
    file: File,
    params: { subject_kind: string; subject_id: string; body?: string; reply_to?: number | null },
    key: string,
  ): Promise<CommandResult> => {
    // trình duyệt tự đặt content-type kèm ranh giới multipart — không đặt tay
    const res = await raw("/v1/talk/files", { method: "POST", body: talkUploadForm(file, params), headers: { "idempotency-key": key } });
    if (res.status === 413) throw new ApiError(await detailOf(res, "Tệp quá lớn"), 413);
    const body = await jsonOf<Partial<CommandResult> & { detail?: string }>(res);
    if (body.status === undefined) throw new ApiError(body.detail ?? `Không gửi được tệp (${res.status})`, res.status);
    return { status: body.status, output: body.output ?? null, error: body.error ?? "", approval_id: body.approval_id ?? null, replayed: !!body.replayed };
  },

  /**
   * Gắn MỘT tệp vào hồ sơ (ADR 0249) — multipart tới đường server mô tả trong khối "Tệp" (``attach.path``). Server kiểm quyền (thấy hồ sơ), cỡ + loại
   * theo nội dung; cùng nội dung đã có trên hồ sơ ⇒ không thêm bản thứ hai.
   */
  recordFileUpload: async (path: string, file: File, params: { subject_kind: string; subject_id: string; label?: string }, key: string): Promise<CommandResult> => {
    const res = await raw(path, { method: "POST", body: recordFileForm(file, params), headers: { "idempotency-key": key } });
    if (res.status === 413) throw new ApiError(await detailOf(res, "Tệp quá lớn"), 413);
    const body = await jsonOf<Partial<CommandResult> & { detail?: string }>(res);
    if (body.status === undefined) throw new ApiError(body.detail ?? `Không gắn được tệp (${res.status})`, res.status);
    return { status: body.status, output: body.output ?? null, error: body.error ?? "", approval_id: body.approval_id ?? null, replayed: !!body.replayed };
  },

  // trợ lý
  suggestions: async (): Promise<string[]> => jsonOf<string[]>(await raw("/v1/assistant/suggestions")),
  attention: async (): Promise<Attention[]> => jsonOf<Attention[]>(await raw("/v1/assistant/attention")),
  desk: async (): Promise<DeskPage> => jsonOf<DeskPage>(await raw("/v1/desk")),
  /** Đề xuất soạn sẵn hôm nay (0074 A) — server soạn ngay nếu hôm nay chưa có lượt nào cho người này. */
  morning: async (): Promise<ChatCard[]> => jsonOf<ChatCard[]>(await raw("/v1/assistant/morning")),
  morningDecide: async (id: string, decision: "confirm" | "dismiss", input: Input, key: string): Promise<CommandResult> =>
    jsonOf<CommandResult>(await post(`/v1/assistant/morning/${id}/decide`, { decision, input, key })),
  threads: async (): Promise<ThreadSummary[]> => jsonOf<ThreadSummary[]>(await raw("/v1/assistant/threads")),
  thread: async (id: string) =>
    jsonOf<{ id: string; title: string; turns: { role: string; text: string; cards?: unknown[]; steps?: unknown[]; at?: string; attachments?: unknown[]; decided?: unknown; pending?: unknown; limit?: unknown }[] }>(await raw(`/v1/assistant/threads/${id}`)),

  // thông báo
  notices: async (limit = 60): Promise<NoticeInbox> => api.queryRaw<NoticeInbox>("core.notifications", { limit }),
  markNoticesRead: (ids: number[]): Promise<CommandResult> => api.command("core.read_notifications", { ids }, crypto.randomUUID()),
};

/** Một lượt hỏi: mỗi dòng NDJSON là một sự kiện. Không đệm — sự kiện tới là yield ngay. */
export async function* chatStream(
  message: string,
  threadId: string | null,
  signal?: AbortSignal,
  images: { media_type: string; data: string }[] = [],
  opts?: { mode?: string; app_id?: string | null; turn_key?: string; subject?: { kind: string; id: string; title: string } | null; documents?: ChatDocument[] },
): AsyncGenerator<StreamEvent> {
  const body: Record<string, Json> = { message };
  if (threadId) body.thread_id = threadId;
  // hỏi từ một trao đổi đang mở (0115): trợ lý đọc đúng luồng đó bằng quyền người hỏi; trả lời vẫn riêng người hỏi
  if (opts?.subject) body.subject = { kind: opts.subject.kind, id: opts.subject.id, title: opts.subject.title };
  // chế độ trò chuyện (0098): "builder" = trình tạo app (lời dặn + công cụ riêng, cuộc trò chuyện riêng); app_id ⇒ đang làm trên app đó
  if (opts?.mode) body.mode = opts.mode;
  if (opts?.app_id) body.app_id = opts.app_id;
  if (opts?.turn_key) body.turn_key = opts.turn_key;   // mã lượt ⇒ bấm "Dừng" được (stopTurn)
  // ảnh (0050): model đọc trong lượt này, server không lưu byte ảnh
  if (images.length) body.images = images.map((i) => ({ media_type: i.media_type, data: i.data }));
  // tài liệu chỉ dùng trong cuộc trò chuyện này (ADR 0249): không vào kho tri thức, không gắn hồ sơ
  if (opts?.documents?.length) body.documents = opts.documents.map((d) => ({ filename: d.filename, data: d.data }));
  const res = await post("/v1/assistant/chat/stream", body, {}, signal);
  if (!res.ok || !res.body) throw new ApiError(await detailOf(res, `Trợ lý không trả lời (${res.status})`), res.status);
  yield* ndjson(res.body);
}

/** Ghi chú hiện trường (0074 D): gửi đoạn ghi âm WAV; dòng đầu `transcript` (chữ chép từ giọng nói), sau đó lượt trợ lý như chat. */
/** Đọc NDJSON (chat, quyết định đề xuất, ghi chú) — BFF không đệm, mỗi dòng một sự kiện. */
async function* ndjson(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const parser = new NdjsonParser();
  const reader = body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const ev of parser.push(value)) yield ev;
  }
  for (const ev of parser.flush()) yield ev;
}

/** Người Xác nhận / Bỏ một đề xuất của lượt đang dừng (0049) — NDJSON, cùng đường truyền với chat (BFF không đệm). */
export async function* decideStream(cardId: string, decision: "confirm" | "dismiss", input: Input, key: string, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
  const res = await post("/v1/assistant/proposals/decide", { card_id: cardId, decision, input, key }, {}, signal);
  if (!res.ok || !res.body) throw new ApiError(await detailOf(res, `Không gửi được quyết định (${res.status})`), res.status);
  yield* ndjson(res.body);
}

/** Soạn với AI dạng LUỒNG (trò chuyện): soạn mới `{kind, brief}` hoặc nói tiếp `{session_id, instruction, block?, mode?, retry?}`.
 *  Mỗi dòng NDJSON là tiến trình: session · status · step · data · blocks · done / error. */
export type ComposeTurnBody =
  | { kind: string; brief: string }
  | { session_id: string; instruction: string; block?: string; mode?: string; retry?: boolean };
export async function* composeStream(body: ComposeTurnBody, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
  const res = await post("/v1/compose/stream", body, {}, signal);
  if (!res.ok || !res.body) throw new ApiError(await detailOf(res, `Không soạn được (${res.status})`), res.status);
  yield* ndjson(res.body);
}

/** Tài liệu gửi kèm MỘT câu hỏi (base64) — chữ đọc trong cuộc trò chuyện, thân tệp không lưu (ADR 0249). */
export type ChatDocument = { filename: string; data: string; size: number };

/** Thân gắn tệp vào hồ sơ: trường chữ trước, tệp sau cùng (server đọc theo khúc). */
export function recordFileForm(file: Blob & { name?: string }, params: { subject_kind: string; subject_id: string; label?: string }): FormData {
  const f = new FormData();
  f.set("subject_kind", params.subject_kind);
  f.set("subject_id", params.subject_id);
  if (params.label) f.set("label", params.label);
  f.set("file", file, file.name || "tep");
  return f;
}

/** Thân gửi tệp vào trao đổi: trường chữ trước, tệp sau cùng (server đọc theo khúc). */
export function talkUploadForm(file: Blob & { name?: string }, params: { subject_kind: string; subject_id: string; body?: string; reply_to?: number | null }): FormData {
  const f = new FormData();
  f.set("subject_kind", params.subject_kind);
  f.set("subject_id", params.subject_id);
  if (params.body) f.set("body", params.body);
  if (params.reply_to) f.set("reply_to", String(params.reply_to));
  f.set("file", file, file.name || "tep");
  return f;
}

/**
 * Tín hiệu SSE "có thay đổi" (không mang nội dung — kênh hộp tin chỉ kèm MÃ luồng, 0115). Tự nối lại khi rớt, chờ tăng dần tới 30 giây.
 * `gone` (kênh một luồng, hoặc 404 khi nối): người dùng vừa mất quyền đọc luồng — dừng hẳn. `auth` (hoặc 401): phiên hết hạn ⇒ đăng nhập lại.
 * `retry` / 503: lỗi tạm thời — nối lại sau, không coi là mất quyền. Trả hàm dừng.
 */
export function listenSignals(
  path: string,
  onChanged: (data: Record<string, unknown>) => void,
  onReconnect?: () => void,
  onGone?: () => void,
): () => void {
  const ctl = new AbortController();
  (async () => {
    let wait = 2;
    while (!ctl.signal.aborted) {
      try {
        const res = await fetch(BASE + path, { credentials: "same-origin", cache: "no-store", signal: ctl.signal });
        const on = connectAction(res.status);
        if (on === "auth") {
          onUnauthorized();
          return;
        }
        if (on === "gone" && onGone) {
          onGone();
          return;
        }
        if (on !== "ok" || !res.body) throw new Error(String(res.status));
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        const parser = new SseParser();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const ev of parser.push(value)) {
            const act = signalAction(ev.event);
            if (act === "changed") {
              wait = 2;
              onChanged(ev.data);
            } else if (act === "gone" && onGone) {
              onGone();
              return;
            } else if (act === "auth") {
              // phiên hết hạn: đăng nhập lại — không phải mất quyền đọc luồng
              onUnauthorized();
              return;
            }
            // "retry": lỗi tạm thời phía server — kênh tự đóng, vòng dưới nối lại sau một lúc, tin đang hiện giữ nguyên
          }
        }
      } catch {
        if (ctl.signal.aborted) return;
      }
      await new Promise((r) => setTimeout(r, wait * 1000));
      wait = Math.min(wait * 2, 30);
      if (!ctl.signal.aborted) onReconnect?.();
    }
  })();
  return () => ctl.abort();
}

// đăng nhập (route riêng của BFF)
async function authJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", cache: "no-store", ...init });
  const body = (await res.json().catch(() => ({}))) as T & { detail?: string };
  if (!res.ok) throw new ApiError((body as { detail?: string }).detail ?? `Lỗi (${res.status})`, res.status);
  return body;
}
const jsonBody = (b: unknown): RequestInit => ({ method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b) });

export const auth = {
  config: () => authJson<AuthConfig>("/api/auth/config"),
  devLogins: () => authJson<DevLogin[]>("/api/auth/dev-logins"),
  /** 0205: tên ứng dụng first-party của yêu cầu đăng nhập (tên nhà vận hành khai — không lấy chữ trên URL). */
  signinApp: (req: string) => authJson<{ app: string }>(`/api/auth/signin-app?req=${encodeURIComponent(req)}`),
  /** Đăng nhập dev; BFF trả kèm manifest + phiên vừa kiểm ⇒ giữ cho trang chủ dùng ngay (primeDesk — một lần, ngắn hạn). */
  devLogin: async (email: string, tenantId: string): Promise<void> => {
    const r = await authJson<{ status: string; manifest?: unknown; session?: unknown }>("/api/auth/dev", jsonBody({ email, tenant_id: tenantId }));
    primeDesk(r.manifest, r.session);
  },
  google: (idToken: string, nonce: string) =>
    authJson<{ email: string; tenants: SessionInfo["tenants"]; tenant_id: string | null }>("/api/auth/google", jsonBody({ id_token: idToken, nonce })),
  /** 0205 §5c: đăng nhập bằng mã gửi qua email — xin mã, rồi nhập mã (kết quả như đăng nhập Google). */
  emailStart: (email: string) => authJson<{ status: string }>("/api/auth/email/start", jsonBody({ email })),
  emailVerify: (email: string, code: string) =>
    authJson<{ email: string; tenants: SessionInfo["tenants"]; tenant_id: string | null }>("/api/auth/email/verify", jsonBody({ email, code })),
  tenant: (tenantId: string) => authJson<{ status: string }>("/api/auth/tenant", jsonBody({ tenant_id: tenantId })),
  session: () => authJson<SessionInfo | null>("/api/auth/session"),
  logout: () => authJson<{ status: string }>("/api/auth/logout", { method: "POST" }),
};
