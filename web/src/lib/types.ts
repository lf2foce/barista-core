// Hợp đồng đọc từ core (backend/src/barista/kernel/ui.py, workspaces.py). Chỉ THÊM trường — trường lạ bị bỏ qua, khối lạ không làm vỡ thẻ.
// Phản chiếu macos/Sources/BaristaDesk/Models.swift + Cards.swift.

import type { ChartData } from "./chart";
import type { WordData } from "./word";

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Input = Record<string, Json>;
export type Row = { id: string; values: Record<string, Json> };

// ── manifest ──

/** Dòng của ô chọn. `hint` = dòng phụ mờ dưới nhãn (SĐT, dự án, vai…) — thêm 1.61; thiếu ⇒ chỉ nhãn. */
export type Option = { value: string; label: string; hint?: string };

export type FieldSpec = {
  key: string;
  label: string;
  kind: string;
  required: boolean;
  help: string;
  hidden: boolean;
  default?: Json;
  options?: Option[];
  options_query?: string;
  /** Danh sách chọn tuỳ giá trị các trường này của chính form (vd mã tin nhắn) — gửi kèm khi tải `options_query`. */
  options_with?: string[];
  /** Như `options_with` nhưng đổi tên: `{tham_số_truy_vấn: trường_của_form}` (1.61). Trường đó đang hiện ⇒ danh sách tải lại + ô này bị xoá khi nó đổi. */
  options_args?: Record<string, string>;
  example?: Json;
  /** Lệnh tạo thêm một mục của danh sách chọn (server chỉ gửi khi người xem có quyền) — danh sách rỗng ⇒ ô hiện "Tạo mới". */
  options_create?: { command: string; title: string };
  /** Nhóm GẤP LẠI của bộ lọc / form lệnh (vd "Thêm điều kiện", "Tuỳ chọn khác"): ô cùng nhóm nằm sau một nút bấm; không có ⇒ luôn hiện. */
  group?: string;
  /** Chữ mờ của ô trống — nói trống nghĩa là gì (vd "Hôm nay"); không có ⇒ "Tất cả". */
  placeholder?: string;
  /** Ô lọc CHÍNH của màn (server khai / mặc định 2 ô đầu không gấp — rà bố cục 18/11): nằm ngoài hàng đầu; ô còn lại vào nút "Bộ lọc". */
  primary?: boolean;
  fields?: FieldSpec[]; // kind "rows": ô con của mỗi dòng (vd các việc của kế hoạch: việc · người nhận · hạn)
};

export type ActionSpec = {
  command: string;
  title: string;
  style: string; // default · primary · destructive
  bind: Record<string, string>;
  preset: Input;
  /** Ô điền sẵn từ cột của dòng nhưng vẫn hiện + sửa được (form "Sửa") — khác `bind` (cố định, ẩn). */
  prefill?: Record<string, string>;
  /** Ô ĐIỀN SẴN do server soạn (vd tiêu đề việc lấy từ tin nhắn) — vẫn hiện + sửa được; khác `preset` (cố định, ẩn). */
  values?: Input;
  when: Record<string, Json[]>;
  importable: boolean;
  /** Lệnh nhập CẢ BẢNG (vd nhập kế hoạch từ Excel): không có form "nhập một" — nút mở thẳng màn Nhập từ Excel, `preset` đi kèm file. */
  import_only?: boolean;
  /** Đường HTTP RIÊNG của gói cho lệnh này (vd chấm công: máy chủ đọc địa chỉ mạng của kết nối) — trống ⇒ `/v1/commands/{command}`. */
  route?: string;
  /** CĂN CỨ người xem đang thấy (0071 / 0251): gửi lại nguyên chữ ở tiêu đề `X-Expect-Basis` khi chạy — số đã đổi ⇒ server trả `conflict`. */
  basis?: string;
  /** Câu hỏi lại của nút không có ô nào để điền (server soạn — vd "Phát hành = cấp số và khoá nội dung…"); trống ⇒ câu chung. */
  confirm?: string;
  /** LUẬT XÁC NHẬN (ADR 0253, server nói): nút không còn ô nào và lệnh ĐẢO ĐƯỢC ⇒ chạy ngay khi chạm, báo kết quả (kèm Hoàn tác nếu có). Hộp xác nhận chỉ cho `confirm` (hệ quả). */
  instant?: boolean;
};

export type OpenSpec = { query: string; bind: Record<string, string>; title: string };

export type ScreenSpec = {
  key: string;
  title: string;
  icon: string;
  tint?: string | null; // màu theo ý nghĩa do server khai (kernel/screens.py); không có ⇒ màu phân hệ
  query: string;
  group: string;
  filters: FieldSpec[];
  actions: ActionSpec[];
  row_actions: ActionSpec[];
  open: OpenSpec | null;
  /** Đầu vào CỐ ĐỊNH của truy vấn (0098 — vd màn app tự ghép: mã app); gửi kèm mọi lần chạy, không phải bộ lọc. */
  input?: Input;
  /** Nút "nhờ trợ lý" trên thanh công cụ: đưa câu mở đầu vào ô chat (vd "Tạo app"). */
  ask?: AskSpec | null;
  /** Nhãn nhỏ cạnh tên màn trên menu (vd "Thử nghiệm"). */
  badge?: string;
  /** Một câu "màn này để làm gì" (cơ chế lõi G1) — hiện dưới tên màn, và là câu của màn trống khi bảng không nói gì hơn. */
  about?: string;
  /** Bộ lọc xếp thành cột bên trái (báo cáo quản trị — 0166); không có ⇒ hàng trên đầu như cũ. */
  filters_side?: boolean;
  /** Màn TỰ VẼ TRỌN khung làm việc (không thẻ bọc, không đầu khung) — như phân hệ một màn; vd "Soạn" (agent + văn bản). */
  full_frame?: boolean;
  /** Bấm MỘT lần vào dòng (core 12/11/2026): "open" ⇒ mở `open`; tên lệnh ⇒ form của nút đó trong `row_actions`. Không có ⇒ bấm chỉ chọn dòng.
   *  Server chỉ gửi khi người xem chạy được đích. */
  row_click?: string;
};

/** `pinned`: người xem tự ghim CẢ PHÂN HỆ này lên thanh bên (ADR 0255) — kể cả khi nó đã nằm ở nhóm "Theo vai". Core cũ không gửi. */
export type WorkspaceSpec = { key: string; title: string; icon: string; screens: ScreenSpec[]; pinned?: boolean };

export type Manifest = {
  user: { id: string; name: string; email: string; role: string; role_label: string; team: string | null };
  tenant: { id: string; name: string };
  workspaces: WorkspaceSpec[];
  inbox: ScreenSpec | null;
  /** `mine`: ghim của CHÍNH người đó (0098) — bỏ ghim được; không có ⇒ ghim mặc định theo vai. */
  pins: { workspace: string; key: string; mine?: boolean }[];
  /** Thanh bên trái theo vai (ADR 0255): khoá phân hệ của nhóm "Theo vai" (công ty / gói khai) và "Đã ghim" (người xem ghim, không lặp ở "Theo vai"), đã lọc quyền,
   *  đúng thứ tự hiện. Không có ⇒ vai không có mặc định nào ⇒ thanh bên vẽ hết `workspaces` như trước. Core cũ không gửi. */
  rail?: { role: string[]; pinned: string[] };
  /** Màn VIỆC chỉ ở bàn việc (0094) — vẫn mở được (lời chào trợ lý, thông báo, ⌘K), không lặp ở menu. */
  desk?: (ScreenSpec & { workspace: string })[];
  /** Màn CÁ NHÂN (trí nhớ của trợ lý…) — vẽ trong menu tài khoản, không ở menu phân hệ. Core cũ không gửi. */
  account?: ScreenSpec[];
  /** Truy vấn người này chạy được (gói đang bật + quyền) — phần gắn thêm (hộp tin…) chỉ gọi khi có tên ở đây. Core cũ không gửi ⇒ coi như có. */
  queries?: string[];
};

/** Bàn việc (0094): một điểm vào "tôi phải làm gì" — server ráp + lọc quyền; web chỉ vẽ. */
export type DeskSection = { workspace: string; screen: ScreenSpec; lines: Attention[]; unchecked: boolean };
export type DeskGroup = { key: "decide" | "do" | "watch" | string; title: string; sections: DeskSection[] };
/** `morning`: số thẻ đề xuất sáng đang mở (thẻ lấy ở `/v1/assistant/morning`); `count`: số MỤC cần để ý trên nút "Việc" — server đếm;
 *  `unchecked`: số mục chưa kiểm được (nút hiện dấu xám thay vì để trống như hết việc). */
export type DeskPage = { groups: DeskGroup[]; morning: number; morning_unchecked?: boolean; count: number; unchecked?: number };

// ── trang ──

/** `drill` (0097): nguồn của con số — bấm ô số ⇒ mở đúng các dòng tạo nên nó (server đã lọc quyền; không có ⇒ ô số không bấm được). */
export type KpiDrill = { query: string; input: Input; sum: string | null };
export type Kpi = { label: string; value: Json; format: string; tone: string | null; hint: string; drill?: KpiDrill; edit?: ActionSpec;
  /** Câu hỏi "vì sao" điền sẵn (0220 lát 7) — bấm ⇒ gửi vào chat */ why?: string };
export type Column = {
  key: string;
  label: string;
  format: string;
  labels?: Record<string, string>;
  tones?: Record<string, string>;
  width?: number;
};

/** Góc nhìn khác của CÙNG các dòng bảng (0087) — server khai bảng nào hợp Kanban (cột trạng thái) / Lịch (cột ngày). */
export type BoardViewSpec = { by: string; title: string; subtitle: string; columns: { key: string; title: string; tone?: string }[]; move: BoardMove | null };
export type CalendarViewSpec = { date: string; title: string; subtitle: string; overdue: boolean };   // overdue=false: lịch sự kiện, ngày đã qua không phải trễ
/** Dòng thời gian (0222): server dựng sẵn mọi thứ (nhóm, thanh, mốc, ngày nghỉ, mũi tên) — web chỉ vẽ. Ngày dạng YYYY-MM-DD. */
export type TimelineItem = {
  id: string;
  title: string;
  start: string;
  /** null = việc chưa có hạn (vẽ chấm tại `start`) */
  end: string | null;
  kind: "bar" | "milestone";
  tone: string;
  status: string;
  who: string;
  /** chữ ngày server đã định dạng ("12/11 – 20/11") */
  dates: string;
  /** cảnh báo code tính (vd bắt đầu trước khi việc trước xong); trống = không */
  flag: string;
  open?: OpenRef;
  /** mã các dòng phải xong trước (mũi tên) */
  after: string[];
};
export type TimelineMilestone = { on: string; title: string; tone: string };
export type TimelineGroup = {
  key: string;
  title: string;
  subtitle: string;
  /** thanh tóm tắt cả nhóm ở hàng tiêu đề (vd cả kế hoạch) */
  start?: string;
  end?: string;
  collapsed: boolean;
  open?: OpenRef;
  milestones: TimelineMilestone[];
  items: TimelineItem[];
};
export type TimelineData = {
  title: string;
  today: string;
  start: string;
  end: string;
  groups: TimelineGroup[];
  milestones: TimelineMilestone[];
  off: { from: string; to: string; kind: string }[];
  note: string;
};
/** Lịch kiểu app lịch (0240) — server tính hết: ai ở làn nào, màu theo loại, mục nào kéo được bằng lệnh nào; app chỉ vẽ lưới giờ + xếp mục chồng giờ. */
export type CalendarLink = { title: string; query: string; input: Input };
export type CalendarMove = { command: string; preset: Input; fields: Record<string, string> };
export type CalendarItem = {
  id: string;
  lane: string;
  title: string;
  subtitle: string;
  /** ISO có múi giờ công ty; cả ngày ⇒ yyyy-mm-dd (hai đầu) */
  start: string;
  end: string;
  all_day: boolean;
  kind: string;
  tone: string;
  /** booked · tentative · maybe · busy · done · marker */
  status: string;
  busy: boolean;
  repeats: boolean;
  open?: OpenRef;
  links: CalendarLink[];
  move?: CalendarMove;
  cancel?: { command: string; preset: Input };
  /** người tổ chức + người được mời (câu trả lời) */
  organizer?: string;
  people?: { name: string; rsvp: string; label: string; organizer: boolean }[];
  /** người xem được mời ⇒ nút trả lời */
  respond?: { command: string; preset: Input; param: string; current: string; options: { key: string; title: string }[] };
};
export type CalendarLane = { key: string; title: string; subtitle: string; me: boolean; email: string; open?: Input };
export type CalendarCreator = {
  key: string;
  label: string;
  kind: string;
  tone: string;
  command: string;
  preset: Input;
  /** tên trường của lệnh cho: day · start · end · minutes · title · attendee */
  fields: Record<string, string>;
  subject?: { field: string; label: string; search: string; required: boolean };
  place?: string;
};
export type CalendarNav = {
  param_view: string;
  param_on: string;
  prev: string;
  next: string;
  views: { key: string; title: string }[];
  who?: { param: string; current: string; options: { key: string; title: string }[] };
  /** chọn đơn vị (chi nhánh / phòng) */
  unit?: { param: string; current: string; options: { key: string; title: string }[] };
};
export type CalendarData = {
  title: string;
  view: "day" | "week" | "agenda";
  start: string;
  end: string;
  today: string;
  now: string;
  tz: string;
  hours: [number, number];
  days: { on: string; label: string; off: string }[];
  lanes: CalendarLane[];
  kinds: { key: string; label: string; tone: string }[];
  items: CalendarItem[];
  nav: CalendarNav | null;
  create: { creators: CalendarCreator[]; understand: string; me: string; invite: { options: string; groups: { title: string; emails: string[] }[] } | null } | null;
  empty: string;
  note: string;
};
export type TableViews = { board: BoardViewSpec | null; calendar: CalendarViewSpec | null; timeline: TimelineData | null };

export type TablePage = {
  kind: "table";
  title: string;
  kpis: Kpi[];
  columns: Column[];
  rows: Row[];
  empty: string;
  /** Nút dưới câu `empty` khi bảng không có dòng — việc nên làm tiếp (vd "Thêm người / phòng"). */
  empty_actions?: ActionSpec[];
  /** Nút thanh công cụ chưa chạy được lúc này: lệnh → lý do (cơ chế lõi G2). Nút hiện mờ, lý do hiện ngay cạnh / khi rê chuột. */
  unavailable?: Record<string, string>;
  note: string;
  views: TableViews | null;
  /** Thẻ gọn (0094): bản server đã cắt — `total` chỉ có khi biết tổng thật. */
  compact?: Compact;
  /** Bảng nằm trong THẺ (app tự ghép — 0098) mang nút trên dòng + mở chi tiết của riêng nó (màn thì khai ở ScreenSpec). */
  row_actions?: ActionSpec[];
  open?: OpenSpec | null;
  /** Khối vẽ TRÊN bảng (vd chọn mức tự chủ của trợ lý — 0144). Thiếu ⇒ không có. */
  header?: Block[];
  /** Trang phía server (ui.slice_page / ui.paging): `rows` là một trang; `total` = số dòng CẢ tập lọc; `complete` = false khi truy vấn chạm trần. */
  paging?: Paging;
};

export type Paging = { offset: number; limit: number; total: number; complete: boolean };

export type Compact = { shown: number; total: number | null };

/** Ô bấm được (0251, chỉ THÊM): `open` mở trang khác (vd "Thuộc: Giao dịch …"), `action` mở form của đúng ô (vd ô thiếu ⇒ điền); `placeholder` hiện khi ô trống. */
export type DetailField = { label: string; value: Json; format: string; open?: OpenRef; action?: ActionSpec; placeholder?: string };
/** Mẩu chữ của tờ giấy (0251): chữ của mẫu `{t}`; ô `{t, k, layer}` — `record` số từ hồ sơ (khoá), `company` thông tin công ty, `missing` ô trống (chạm điền), `person` người nhập. */
export type SheetRun = { t: string; k?: string; layer?: string };
/** `slot`: khối là ĐOẠN TỰ DO của mẫu (0251 lát 2) — `hint` viết gì, `empty` chưa viết (tờ giấy vẽ chỗ trống, bản in bỏ). */
export type SheetBlock = { type: string; text: string; runs?: SheetRun[]; columns?: string[]; rows?: string[][]; total?: string[]; align?: string[]; slot?: string; hint?: string; empty?: boolean };
/** MÀN "SOẠN" (0251 lát 2): agent bên trái + VĂN BẢN MỚI bên phải. `doc`: thao tác của trình soạn nhận mở văn bản mới (tạo lúc gửi câu đầu, mở,
 *  trợ lý viết) — server gửi tên thao tác, web không viết cứng. `kinds`: cách soạn khác thành lựa chọn "Loại" (mở màn của nó). */
export type StudioData = {
  title: string;
  subtitle: string;
  examples: string[];
  doc: { create: string; view: string; assist: string; home: string; label: string; kb_documents?: string };
  /** Khung thể thức chọn được (Nghị định 30, thư…) — không chọn ⇒ máy chủ nhận theo lời mô tả. */
  forms: { key: string; label: string }[];
  kinds: { label: string; subtitle?: string; current?: boolean; open?: { query: string; input: Input } }[];
};
/** "SOẠN VỚI AGENT" (0251 lát 2): tờ giấy đang lập nói cho khung chat — dòng "Về", lần sửa cuối, chip do server chọn (`ask` gửi vào chat nguyên văn ·
 *  `action` mở form điền), nút sửa đoạn tại chỗ (`write`, khoá đoạn điền ở `key`), ngữ cảnh gửi kèm câu hỏi (`context`). */
export type SheetCompose = {
  about: string;
  status: string;
  chips: { label: string; ask?: string; action?: ActionSpec }[];
  write?: ActionSpec;
  context: { query: string; input: Input; title: string };
};
/** CHÍNH TỜ GIẤY (0251) — màn mặc định của giấy lập từ hồ sơ. `actions`: nút điền ô thiếu (khoá ô = `preset.key`). */
export type Sheet = {
  title: string;
  number: string;
  draftMark: string;
  blocks: SheetBlock[];
  signers: string[];
  notes: string[];
  actions: ActionSpec[];
  legend: { layer: string; label: string }[];
  /** Chạm số từ hồ sơ ⇒ mở hồ sơ để sửa (số không sửa trên giấy). */
  open?: OpenRef;
  compose?: SheetCompose;
};
/** `table` (0102): bảng thật trong văn bản (vd bảng phí của công văn) — vẽ bảng, không ghép chữ có dấu |. */
export type SectionTable = { columns: string[]; rows: string[][]; total: string[] };
export type DetailSection = { title: string; fields: DetailField[]; text: string | null; table: SectionTable | null };
/** Bản gốc để XEM trước tiên (0102): luôn là PDF (Word / Excel đã đổi ở máy chủ); lỗi ⇒ cho tải như `links`. */
export type DetailOriginal = { title: string; path: string; filename?: string; page?: number };
/** `mode: "builder"` ⇒ mở TRÌNH TẠO APP (0098) thay vì đưa câu vào trợ lý cá nhân; `app_id` ⇒ trình tạo làm trên app đó. */
export type AskSpec = { title: string; prompt: string; mode?: string; app_id?: string };
export type DetailLink = { title: string; path: string; filename?: string; page?: number };
export type DetailPage = {
  kind: "detail";
  title: string;
  subtitle: string;
  sections: DetailSection[];
  /** File tải / mở (vd giấy tờ .docx, bản gốc tài liệu kho). `page` (0052): PDF mở ngay ở đúng trang. */
  links: DetailLink[];
  actions: ActionSpec[];
  style: string | null;
  ask: AskSpec | null;
  /** 0102: có ⇒ vẽ trình xem bản gốc; `textRole: "extracted"` ⇒ các mục là chữ bóc / OCR (tab phụ), mặc định mở bản gốc. */
  original: DetailOriginal | null;
  textRole: string | null;
  /** 0251: có ⇒ vẽ tờ giấy làm góc nhìn mặc định (cạnh "Bản in"). */
  sheet: Sheet | null;
};

export type OpenRef = { title?: string; query: string; input: Input };
export type StepItem = { label: string; state: string; at?: Json; note?: string; owner?: string };
export type StageItem = { label: string; status: string; required: boolean; hidden: boolean; value?: string; note?: string };
export type Stage = { key: string; name: string; owner: string; state: string; items: StageItem[] };
export type Tile = { title: string; subtitle?: string; badge?: string; tone?: string; module?: string; open?: OpenRef };
/** `file` (0128, tab Tệp của trao đổi): tệp đính kèm của dòng — app tải / vẽ ảnh thu nhỏ qua đường tải tệp sẵn có. */
export type ListItem = { title: string; subtitle?: string; tone?: string; badge?: string; open?: OpenRef; ask?: string; file?: TalkFile };
export type BoardCard = { id: string; title: string; subtitle?: string; tone?: string; badge?: string; open?: OpenRef };
export type BoardColumn = { key: string; title: string; subtitle?: string; tone?: string; items: BoardCard[] };
export type BoardMove = { command: string; id_key: string; column_key: string; form_columns?: string[] };
export type ThreadMsg = {
  id: number;
  author: string;
  mine: boolean;
  body: string;
  at: string;
  mentions_me: boolean;
  /** `live` (0089): server chạy lại truy vấn của thẻ bằng quyền NGƯỜI XEM — trạng thái hiện tại + nút người đó được bấm. */
  card: { query: string; input: Input; title: string; live: { gone: boolean; subtitle: string; headline: string; actions: ActionSpec[] } | null } | null;
  // 0115 — trường thêm; thiếu thì mặc định
  edited: boolean;
  recalled: boolean;
  pinned: boolean;
  reply: { id: number; author: string; body: string; recalled: boolean } | null;
  attachments: TalkFile[];
  reactions: { emoji: string; count: number; mine: boolean; names: string[] }[];
  can_edit: boolean;
  /** Lịch sử sửa / thu hồi (server chỉ đưa khi người xem được mở). */
  history: OpenRef | null;
  /** Điểm cắm talk.message_actions: nút gói khác góp cho tin này (vd "Tạo việc") — đã lọc theo quyền người xem. */
  actions: ActionSpec[];
  /** Dấu nhỏ dưới tin (vd "Đã tạo việc") — server chỉ gửi cho người thấy thứ dấu đó dẫn tới. */
  marks: MessageMark[];
};
export type MessageMark = { title: string; detail: string; tone: string; open: OpenRef | null };
export type TalkFile = { id: string; filename: string; content_type: string; size_bytes: number; is_image: boolean; keep?: ActionSpec };
/** Khối "Tệp" của hồ sơ (ADR 0249): danh sách thường + nơi gắn tệp — app thả / chọn tệp gửi lên ``path`` cùng loại + mã hồ sơ. */
export type ListAttach = { subject_kind: string; subject_id: string; subject_title: string; path: string; file_max: number; file_kinds: string; labels: string[] };
export type ThreadBlockData = {
  title: string;
  subject_kind: string;
  subject_id: string;
  subject_title: string;
  thread_id: string | null;
  last_read: number;
  can_post: boolean;
  people: Option[];
  /** Nút gói khác góp lên khối (điểm cắm talk.thread_actions — vd "Gọi"), đã lọc theo quyền người xem; trống khi gói tắt. */
  actions: ActionSpec[];
  messages: ThreadMsg[];
  // 0115 — trường thêm; thiếu thì mặc định
  kind_label: string;
  has_more: boolean;
  has_newer: boolean;
  focus_id: number | null;
  pinned: { id: number; author: string; body: string; pinned_by: string; at: string }[];
  muted: boolean;
  following: boolean;
  reaction_choices: string[];
  can_attach: boolean;
  file_max: number;
  file_kinds: string;
  /** Nút "Tóm tắt" — câu hỏi do server soạn; hỏi trợ lý RIÊNG (0046). */
  assist: { title: string; prompt: string }[];
  notice: string;
  /** 0128: tab "Tệp" — trang thẻ (có tab Ảnh / Tài liệu / Link) server mô tả; null khi luồng chưa có tin. */
  files: OpenRef | null;
};

/** Bằng chứng của một phát hiện (kernel/ui.py evidence): có document_id ⇒ mở file gốc đúng trang, tìm câu nguyên văn. */
export type Evidence = { label: string; quote: string; value: string | null; page: number | null; document_id: string | null; scan: boolean; filename: string; note: string };

export type Block = (
  | { type: "kpis"; items: Kpi[] }
  | { type: "steps"; title: string; steps: StepItem[] }
  | { type: "stages"; title: string; stages: Stage[] }
  | ({ type: "brief" } & Brief)
  | { type: "callout"; tone: string; title: string; text: string; facts: string[]; draft: string; actions: ActionSpec[] }
  | { type: "list"; title: string; items: ListItem[]; attach?: ListAttach }
  | { type: "tiles"; title: string; items: Tile[] }
  | { type: "text"; title: string; markdown: string }
  /** Ảnh trong thẻ (ui.b_image): `src` link ký ngắn hạn / đường /v1/…; trống ⇒ hiện `note` */
  | { type: "image"; src: string; caption: string; alt: string; note: string }
  /** Chia hai cột (ui.b_split): mỗi cột là danh sách khối; `ratio` "a:b"; màn hẹp ⇒ xếp dọc */
  | { type: "split"; left: Block[]; right: Block[]; ratio: string }
  /** Ghi âm (tổng đài — 0149): `src` là đường /v1/… của core (qua BFF), quyền kiểm mỗi lần tải */
  | { type: "audio"; title: string; src: string; duration_s: number | null }
  | { type: "table"; page: TablePage }
  | { type: "board"; title: string; columns: BoardColumn[]; move: BoardMove | null }
  | ({ type: "thread" } & ThreadBlockData)
  | ({ type: "chart" } & ChartData)
  | ({ type: "timeline" } & TimelineData)
  | ({ type: "calendar" } & CalendarData)
  | { type: "finding"; tone: string; title: string; badge: string; text: string; facts: string[]; evidence: Evidence[]; actions: ActionSpec[] }
  | { type: "choices"; title: string; questions: Question[]; submit: string; prefix: string }
  | ({ type: "composer" } & ComposerData)
  | ({ type: "word" } & WordData)
  | ({ type: "studio" } & StudioData)
  | ({ type: "picker" } & PickerData)
  /** Khối tải sau (ui.b_later): chỗ của các khối truy vấn `query` trả — thẻ tự tải theo thứ tự khối (chỉ khi gọi `api.page`, `?later=1`) */
  | { type: "later"; query: string; input: Input; title: string; part?: string }
  | { type: "unknown" }
) & {
  /** "Sửa khối này" (app tự ghép — 0098) */ edit?: ActionSpec;
  /** Bề rộng 1–12 trên lưới 12 cột (0166): khối liền nhau có `w` mà tổng ≤ 12 nằm cùng một hàng; không có ⇒ cả hàng */ w?: number;
  /** Nguồn gốc của số (0220): nguồn, phạm vi quyền, số đủ tới ngày nào, cảnh báo độ phủ — server gửi, web chỉ hiện */ source?: BlockSource;
  /** Câu hỏi "vì sao" điền sẵn của biểu đồ (0220 lát 7) — bấm ⇒ gửi vào chat */ why?: string;
};

/** Nguồn gốc của số trên một khối (0220). Ngày dạng YYYY-MM-DD; chuỗi trống = server không khai. */
export type BlockSource = { kind: string; label: string; scope: string; through: string; reliableFrom: string; warn: string };

/** Chọn MỘT mức đặt sẵn (0144): server nói mục nào đang áp (`current`; "" ⇒ hiện `custom`) và phiếu đang chờ (`pending`); bấm = chạy lệnh của mục. */
export type PickerItem = { key: string; title: string; text: string; facts: string[]; action: ActionSpec | null };
export type PickerData = { title: string; items: PickerItem[]; current: string; custom: string; note: string; pending: string };

/** Câu hỏi TRƯỚC khi làm (0098): người chọn rồi gửi một lần — web ghép thành một tin nhắn vào chat. */
export type Question = { key: string; question: string; options: string[]; multi: boolean };
/** Thẻ nhiều trang (app tự ghép — 0098): bấm tab = chạy lại truy vấn của màn với `param` = key. */
export type Tabs = { param: string; items: { key: string; title: string }[]; current: string };

export type CardPage = {
  kind: "card";
  title: string;
  subtitle: string;
  icon: string;
  blocks: Block[];
  actions: ActionSpec[];
  open: OpenRef[];
  ask: AskSpec | null;
  compact?: Compact;
  tabs?: Tabs;
};

export type Page = TablePage | DetailPage | CardPage;

// ── lệnh / form ──

export type FormSpec = {
  command: string;
  title: string;
  description: string;
  importable: boolean;
  fields: FieldSpec[];
  /** (tương lai, nếu server công bố) truy vấn xem kết quả của lệnh: {query, key} — `key` là khoá trong output = tham số truy vấn. */
  shows?: { query: string; key: string } | [string, string] | null;
  /** FORM THEO Ý ĐỊNH (ADR 0250): ô hỏi trước (vd việc gì · mốc · ai chịu trách nhiệm) + ô "Mô tả / thả file"; phần còn lại do `draft` soạn. Không có ⇒ form đủ ô. */
  intent?: string[];
  /** Truy vấn "Soạn giúp" (vd `core.draft_input`): nhận {command, text, values, file_name, file_b64}, trả `DraftResult` — không chạy lệnh. */
  draft?: string;
  /** Luật xác nhận của lệnh (ADR 0253): "none" = đảo được · "consequence" = hộp nói hệ quả bằng `effect`. */
  confirm?: "none" | "consequence";
  effect?: string;
};
/** Bản soạn của "Soạn giúp" (ADR 0250): giá trị đã kiểm ở server (gồm ô người đã điền), ô nào máy đoán, ghi chú chỗ đoán / còn thiếu. */
export type DraftResult = { values: Input; guessed: string[]; notes: string[] };
export type CommandResult = {
  status: string; // ok · pending_approval · invalid · conflict · forbidden …
  output: Json;
  error: string;
  approval_id: string | null;
  replayed: boolean;
  /** Quyết thẻ buổi sáng: trạng thái thẻ đã khoá (failed · needs_reconciliation · executed) + câu báo của server — trường THÊM. */
  state?: string;
  message?: string;
};

export type ImportReport = {
  command: string;
  title: string;
  dry_run: boolean;
  total: number;
  ok: number;
  failed: number;
  unknown_columns: string[];
  rows: { line: number; status: string; error?: string | null }[];
};

// ── nhập Excel tự khớp (0226) ──

export type ImportColumn = { index: number; letter: string; header: string; group: string; samples: string[]; filled: number; ignore: string; dropped: boolean };
export type ImportField = { key: string; label: string; kind: string; required: boolean; options: boolean };
export type ImportMatch = { field: string; column: number; confidence: number; source: "saved" | "name" | "value" | "ai"; why: string };
export type ImportSheetInfo = {
  name: string;
  rows: number;
  header_line: number | null;
  fit: number;
  /** lệnh nhập hợp trang này nhất (theo độ giống tiêu đề); `same` = chính lệnh đang mở */
  suggest: { command: string; title: string; fit: number; same: boolean } | null;
};
export type ImportDateInfo = {
  field: string;
  label: string;
  order: "dmy" | "mdy";
  decided: "data" | "country" | "mixed" | "saved" | "user";
  text_dates: number;
  sample: string;
  examples: Record<string, string | null>;
};
export type ImportAnalysis = {
  auto: boolean;
  reason?: string;
  command: string;
  title: string;
  file_name: string;
  signature: string;
  sheets: ImportSheetInfo[];
  sheet: string;
  header_line: number;
  rows: number;
  columns: ImportColumn[];
  fields: ImportField[];
  mapping: ImportMatch[];
  unmapped_required: string[];
  skipped: { line: number; reason: string }[];
  skipped_count: number;
  layout: { exact: boolean; name?: string; uses?: number } | null;
  resolutions: Record<string, Record<string, string>>;
  dates: ImportDateInfo[];
  locale: { country: string; timezone: string; date_order: "dmy" | "mdy" };
  key: { fields: string[]; update: boolean } | null;
  /** Lệnh nhập cả bảng: các trường cấp lệnh màn nhập được đặt sẵn (vd kế hoạch đích). */
  table?: { preset: string[] } | null;
  /** Tên bảng đoán được (dòng tên báo cáo phía trên tiêu đề, hoặc tên file). */
  heading?: string;
  ai: { state: string; columns?: number; matched?: number; input_chars?: number; tokens?: { input: number; output: number; requests: number } };
};
export type ImportPlan = {
  sheet: string;
  header_line: number;
  mapping: Record<string, number>;
  resolutions: Record<string, Record<string, string>>;
  date_orders: Record<string, string>;
  overwrite: boolean;
  remember: boolean;
  /** Lệnh nhập cả bảng: giá trị cấp lệnh (plan_id của kế hoạch đích, plan_title khi tạo mới). */
  preset?: Record<string, string>;
};
export type ImportUnresolved = {
  field: string;
  label: string;
  options: { value: string; label: string }[];
  values: { raw: string; key: string; count: number; suggest: string | null; suggest_label: string | null }[];
};
export type ImportRowResult = {
  line: number;
  status: string;
  action: "create" | "update" | "skip";
  error?: string | null;
  diff?: { field: string; old: Json; new: Json; applied: boolean }[];
};
export type ImportPlanReport = {
  command: string;
  title: string;
  dry_run: boolean;
  total: number;
  ok: number;
  failed: number;
  summary: { create: number; update: number; skip: number; failed: number; ignored: number; waiting: number };
  rows: ImportRowResult[];
  /** một dòng để nói VÌ SAO không có gì để nhập (null khi có dòng nhập được) */
  nothing: string | null;
  preview: { line: number; action: string; status: string; error?: string | null; values: { label: string; text: string }[] }[];
  reasons: { reason: string; count: number; lines: number[] }[];
  skips: { reason: string; count: number; lines: number[]; rows: { line: number; who?: string; note?: string }[] }[];
  ignored: { line: number; reason: string }[];
  unresolved: ImportUnresolved[];
  overwrite: boolean;
  dates: ImportDateInfo[];
  key: { fields: string[]; update: boolean } | null;
  /** Lệnh nhập cả bảng: lưu ý cả file + bản xem trước kết quả (kế hoạch, Gantt). */
  notes?: string[];
  plan_view?: PlanImportView | null;
  pending_approval?: boolean;
};
export type PlanImportView = {
  target: { plan_id: string; title: string; new: boolean };
  summary: string;
  start: string | null;
  end: string | null;
  phases: { title: string; count: number; start: string | null; end: string | null }[];
  tasks: {
    line: number;
    code: string;
    title: string;
    phase: string;
    start: string | null;
    end: string | null;
    computed: string[];
    who: string;
    action: "create" | "update" | "skip" | "invalid";
    after: number[];
    warnings: string[];
    diff: string[];
  }[];
  missing: { title: string; due: string | null }[];
  calendar: string;
  warnings: number;
  unchanged: number;
  groups: string[];
  /** Khối `timeline` thô của server (giải mã bằng `decodeTimeline`). */
  timeline: unknown;
};

// ── trợ lý ──

export type Step = { n: number; tool: string; title: string; status: string; error?: string; summary?: string; card_id?: string };
/** BẢN CHẠY THỬ CHUẨN (ADR 0253): một hợp đồng cho thẻ đề xuất, thẻ buổi sáng, phiếu duyệt — hồ sơ · cũ → mới · vì sao · đảo được không. */
export type Brief = {
  title: string;
  subject: { label: string; open: { query: string; input: Input } | null } | null;
  changes: { label: string; before: string; after: string }[];
  reason: string;
  /** ok: true = đảo được · false = không đảo được · null = chưa khai; `how` = cách đảo / hệ quả. */
  reversible: { ok: boolean | null; how: string };
};
export type ChatCard = {
  id: string;
  kind: string; // page · proposal
  /** Bản chạy thử chuẩn của đề xuất (server dựng lúc chạy thử). */
  brief?: Brief | null;
  /** Trạng thái do server gửi. Thẻ chờ xác nhận có state là proposed. */
  state?: string;
  state_label?: string;
  approval_id?: string;
  /** Thẻ CẦN ĐỐI SOÁT (rà 22/11): nút đóng thẻ do server gửi — `decision` gửi lại nguyên văn qua đúng đường quyết của thẻ. */
  resolve?: { decision: string; label: string }[];
  /** Thẻ buổi sáng: vì sao nên làm (code tính ở server — web vẽ dưới tiêu đề). */
  reason?: string;
  title: string;
  subtitle?: string;
  query?: string;
  input?: Input;
  page?: Page | null;
  /** Thẻ gọn ngay trong câu trả lời (0094 đợt 2): server cắt từ chính kết quả vừa chạy; "Mở" xem bản đủ. */
  preview?: Page | null;
  command?: string;
  fields?: FieldSpec[];
  /** Đề xuất của một lượt đang DỪNG chờ người (0049): Xác nhận / Bỏ qua `proposals/decide` ⇒ trợ lý chạy tiếp. Thiếu ⇒ thẻ cũ. */
  resumable?: boolean;
  /** Đề xuất BÊN LỀ (vd ghi nhớ): lượt KHÔNG dừng chờ — thẻ nhỏ dưới câu trả lời, quyết qua `proposals/decide`, quyết xong không chạy tiếp. */
  aside?: boolean;
  /** Bên lề: tên nút xác nhận (tên lệnh, vd "Ghi nhớ"). */
  verb?: string;
  /** "morning": đề xuất trợ lý soạn sẵn buổi sáng (0074 A) — Xác nhận / Bỏ qua `morning/{id}/decide`, không thuộc lượt chat nào. */
  source?: string;
  /** Đề xuất: vì sao chờ người (chữ server viết từ `decide` — 0144). */
  why?: string[];
  /** Thẻ kết quả (kind "result") lần trợ lý TỰ LÀM: nút Hoàn tác chạy `command` với `input` NGUYÊN VĂN bằng quyền người bấm. */
  undo?: UndoSpec | null;
};
export type UndoSpec = { command: string; input: Input; title: string };
export type StreamEvent = {
  type: string;
  text?: string;
  message?: string;
  thread_id?: string;
  card?: ChatCard;
  n?: number;
  tool?: string;
  title?: string;
  status?: string;
  error?: string;
  summary?: string;
  card_id?: string;
  /** compose stream: mã bản nháp vừa tạo (sự kiện ``session``) */
  session_id?: string;
  /** compose stream, bước lỗi: lý do ngắn bằng tiếng Việt (không tên trường kỹ thuật) */
  reason?: string;
  /** compose stream: bảng vừa lấy (``data``) / bản soạn đầy đủ (``blocks``) — thô, giải mã bằng ``decodeComposerItem`` */
  block?: unknown;
  blocks?: unknown;
  /** done: lượt dừng chờ người duyệt đề xuất (0049) */
  pending?: boolean;
  /** done: lượt dừng vì chạm trần bước — ``continue`` là câu app gửi khi người bấm "Tiếp tục" (lõi soạn, app chỉ gửi lại) */
  limit?: { steps?: unknown; continue?: unknown };
  // proposals/decide (0049)
  replayed?: boolean;
  resuming?: boolean;
  /** decided: phiếu duyệt sinh ra (đề xuất thành "Đã gửi duyệt") — mở đúng phiếu này. */
  approval_id?: string;
};
export type ThreadSummary = { id: string; title: string; updated_at: string };
export type Attention = { tone: string; title: string; detail: string; query: string; ask?: string };

export type Notice = {
  id: number;
  kind: string;
  title: string;
  body: string;
  open: { query: string; input: Input } | null;
  created_at: string;
  read: boolean;
};
export type NoticeInbox = { unread: number; items: Notice[] };

// ── đăng nhập ──

export type AuthConfig = {
  google: { client_id: string } | null;
  dev: boolean;
  /** 0205 §5c: bản cài gửi được mã đăng nhập qua email */
  email?: boolean;
  /** BFF kèm sẵn danh sách vai khi đăng nhập dev bật (một lượt hỏi thay vì hai) */
  dev_logins?: DevLogin[];
};
export type TenantChoice = { id: string; name: string; role_label: string };
export type DevLogin = {
  tenant_id: string;
  tenant: string;
  email: string;
  name: string;
  role: string;
  role_key?: string;
  group?: string;
  hint?: string;
  ask?: string;
};
export type SessionInfo = {
  email: string;
  tenant_id: string | null;
  mode: "dev" | "token";
  tenants: TenantChoice[];
};

/** Yêu cầu kết nối MCP (0093) — trang đồng ý cấp quyền cho ứng dụng AI. */
export type McpRequest = {
  client_name: string; client_uri: string; scopes: string[]; redirect_host: string; company: string; user: string;
  /** Chữ do server gửi theo loại yêu cầu (0205: đăng nhập ứng dụng first-party ≠ ứng dụng AI đọc dữ liệu). Thiếu ⇒ chữ cũ. */
  title?: string; action?: string; points?: string[];
  /** Ứng dụng first-party chỉ xin danh tính (0205) ⇒ trang tự đồng ý ngay, không bắt bấm "Cho phép". */
  auto?: boolean;
};

/** Khối `composer` (màn Ấn phẩm — gói ngoài vẽ bằng khối này, ADR 0254): một khối của bản soạn. Số chỉ nằm ở khối `data` (trang bảng server dựng sẵn). */
export type ComposerItem = {
  id: string;
  type: string; // heading · paragraph · data · quote · image · placeholder (chỗ giữ "Cần ảnh…" — chỉ ở màn soạn) (lạ ⇒ bỏ qua)
  origin: string; // ai · human · data · kb
  flags: string[];
  text: string;
  level: number;
  placeholders: string[];
  page: TablePage | null;
  /** khối số liệu: truy vấn gốc + đầu vào lúc chụp (chạy lại = cùng quyền người xem) */
  source: { query: string; title: string; at: string; input: Input } | null;
  cite: string;
  document_id: string;
  caption: string;
  filename: string;
  /** chỗ đứng trong bố cục (0224): cover · col1–3 · tiles · grid · footer; trống = chảy */
  slot: string;
  /** khối số liệu: đoạn chữ nguyên văn của bản ghi (vd chính sách bán hàng) */
  texts: { title: string; text: string }[];
  /** ảnh TRÍCH từ tài liệu PDF (không có địa chỉ tệp riêng): bản thu nhỏ dạng data URL — trống nếu không còn quyền đọc tài liệu nguồn */
  derived: boolean;
  thumb: string;
  /** ảnh AI = ảnh MINH HOẠ (0252): chữ chú thích bắt buộc ("Ảnh minh hoạ"); ảnh thật ⇒ rỗng */
  illustration: string;
};

/** Phần bố cục do MÁY CHỦ xếp (gói gửi trong khối `composer`) — web chỉ vẽ theo đây; mọi mã khối tra trong `blocks`. */
export type ComposerSection =
  | { kind: "cover"; style: string; hero: string; text: string[] }
  | { kind: "flow"; ids: string[] }
  | { kind: "columns"; cols: number; columns: string[][] }
  | { kind: "tiles"; cols: number; items: { block: string; label: string; value: string }[]; ids: string[] }
  | { kind: "grid"; cols: number; ids: string[] }
  | { kind: "footer"; ids: string[]; lines: Record<string, string[]> };

export type ComposerLayout = {
  /** khoá trang mẫu; rỗng = bố cục cũ (không bố cục) */
  template: string;
  templates: { key: string; label: string; about: string }[];
  slots: { key: string; label: string }[];
  sections: ComposerSection[];
  /** mẫu nhiều trang: CHÍNH trang HTML của bản gửi / PDF (khung nhúng); rỗng = web tự vẽ theo `sections` */
  html: string;
};

/** Một lần chia sẻ qua link: số lượt khách mở, còn hiệu lực hay không (link chỉ hiện đúng lúc tạo). */
export type ComposerShare = { id: string; recipient: string; version: number; at: string; expires_at: string; views: number; last_viewed_at: string; active: boolean };

/** Ấn phẩm VỀ hồ sơ nào / cho khách nào (ADR 0249) — trống = chưa gắn. */
export type ComposerRef = { kind: string; id: string; label: string };

/** Công thức làm ấn phẩm từ hồ sơ (gói / module chủ hồ sơ góp): loại ấn phẩm làm ra, có cần chọn hồ sơ, có nhận khách. */
export type ComposerRecipe = { key: string; label: string; about: string; piece: string; needs_subject: boolean; customer: boolean };

export type ComposerData = {
  session_id: string;
  kind: string;
  kind_label: string;
  title: string;
  brief: string;
  version: number;
  /** drafting · ready · failed — lượt AI chạy sau khi lệnh ghi xong; lỗi / hết giờ ở `error` */
  status: string;
  status_label: string;
  error: string;
  /** loại ấn phẩm mời chọn — mỗi loại kèm mẫu + định dạng hợp nó (server quyết, một danh sách). Web KHÔNG biết khoá loại nào:
   *  `paper` = cách vẽ bản xem ("deck": bộ trình chiếu, mỗi tiêu đề H1 một trang 16:9 · "page": tờ giấy), `example` = câu gợi ý ở màn trống (rỗng = không gợi ý). */
  kinds: ComposerKind[];
  /** loại khi người chưa chọn (nhãn ô chọn, lọc công thức); web chỉ gửi `kind` khi người chọn rõ — không gửi ⇒ máy chủ nhận loại từ lời mô tả */
  default_kind: string;
  /** định dạng xuất của ấn phẩm này (đầu = chính) */
  formats: { key: string; label: string }[];
  subject: ComposerRef;
  customer: ComposerRef;
  /** ấn phẩm dựng từ hồ sơ (công thức của gói): "làm bản cho khách khác" dựng lại bằng công thức */
  from_record: boolean;
  recipes: ComposerRecipe[];
  blocks: ComposerItem[];
  stats: { flagged: number; placeholders: number; data: number };
  layout: ComposerLayout;
  shares: ComposerShare[];
  versions: { version: number; via: string; note: string; at: string }[];
  exports: { id: string; format: string; filename: string; version: number; at: string }[];
  /** cuộc trò chuyện của bản soạn (cũ → mới) */
  turns: ComposerTurn[];
  /** vòng "vừa trang" đang chạy nền: "measure" (đang kiểm) · "shorten" (AI đang rút gọn chữ) · "" (không) — web hiện trạng thái và tự làm mới */
  fitting: string;
  /** các bản soạn của chính người dùng (mới sửa trước) — như danh sách cuộc trò chuyện */
  history: { id: string; title: string; kind: string; kind_label: string; status: string; status_label: string; at: string }[];
  note: string;
  brand: { color: string; font: string; footer: string; logo: string };
  commands: Record<string, string>;
  /** đường HTTP luồng soạn do server gửi (`/v1/…`) — bắt buộc; vắng / sai ⇒ rỗng và web báo lỗi khi gửi (không tự đoán đường của gói). */
  stream: string;
};

export type ComposerKind = {
  key: string; label: string; templates: { key: string; label: string }[]; formats: { key: string; label: string }[];
  paper: "deck" | "page"; example: string;
};

/** Một lượt: người nói (yêu cầu / lời sửa, `block` khi sửa một khối) hoặc AI trả lời (`status` ready · failed + lý do, các bước đã chạy). */
export type ComposerTurn = {
  role: "user" | "assistant";
  text: string;
  at: string;
  block: string;
  mode: string;
  status: string;
  version: number | null;
  steps: { title: string; status: string; reason?: string }[];
  /** lượt AI mới soạn được MỘT PHẦN (hết giờ / chạm lưới) — web hiện nút "Tiếp tục" */
  partial: boolean;
  /** các trang CÒN tràn khung khi lượt này ghi (đo ở phiên bản `version`) — chỉ vẽ nút khi `version` là phiên bản hiện hành */
  overflow: ComposerOverflowPage[];
};

/** Trang tràn khung (mẫu nhiều trang cố định) — server đo, web chỉ vẽ lời báo + nút bấm. */
export type ComposerOverflowPage = { page: number; lines: number; axis: string; blocks: ComposerOverflowBlock[] };
/** Khối của trang tràn; `action` = lệnh bấm được (lệnh viết lại của gói + đầu vào viết sẵn: rút gọn đúng khối này), null = không có cách bấm một chạm. */
export type ComposerOverflowBlock = {
  id: string; kind: string; label: string; culprit: boolean;
  action: { command: string; label: string; input: { session_id: string; block: string; mode: string; instruction: string } } | null;
};
