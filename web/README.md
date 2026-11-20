# Barista Web

Máy khách web của Barista (Next.js 16 App Router · React 19 · TypeScript strict · Tailwind 4 · shadcn/ui trên base-ui · pnpm).
Cùng ngôn ngữ giao diện với app Mac/iPhone `macos/Sources/BaristaDesk`: **khung kiểu Lark — thanh bên trái (Tìm · Trợ lý · Tin nhắn ·
một mục mỗi phân hệ · người dùng) → cột giữa đổi theo mục → "khung làm việc" bên phải**, nền #F2F2F7,
thẻ trắng bo tròn, chế độ tối, màu xanh làm điểm nhấn, thang chữ 20/16/14/12 chỉ ba độ đậm, không in hoa có dấu, không nghiêng.

## Chạy

```bash
# core (đã chạy sẵn ở :8300 khi dev): BARISTA_DEV_AUTH=1 uv run uvicorn barista.main:app --port 8300  (xem ../README.md)
cd web
pnpm install
pnpm dev            # http://localhost:3100  — chọn vai ở màn đăng nhập dev
pnpm typecheck && pnpm lint && pnpm test
pnpm shots <thư mục>   # Playwright headless chụp: đăng nhập, trợ lý, cột phân hệ + bảng, thẻ khách + Trao đổi, kanban, Tin nhắn, phòng,
                       # thông báo, thanh ẩn, chat, biểu đồ exec.overview (giám đốc); có lỗi console ⇒ thoát mã 1
pnpm menus             # mở mọi menu / hộp thoại headless, đếm lỗi console (bắt lỗi kiểu DropdownMenuLabel ngoài Group)
```

Biến môi trường (xem `.env.example`):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `BARISTA_CORE_URL` | `http://127.0.0.1:8300` | Địa chỉ core (chỉ BFF dùng — trình duyệt không bao giờ gọi thẳng) |
| `BARISTA_SESSION_SECRET` | (dev: khoá cố định) | **Bắt buộc ở production**, ≥ 16 ký tự: khoá mã hoá cookie phiên |
| `BARISTA_WEB_DEV_LOGIN` | tắt ở production | `1` = cho phép màn chọn vai dev khi `NODE_ENV=production` |
| `BARISTA_GOOGLE_WEB_CLIENT_ID` | lấy từ core | Client ID loại **Web** cho nút Google (core hiện chỉ công bố client của app iOS) |

## Kiến trúc

```
Trình duyệt ──cookie httpOnly──► Next (BFF)  ──Authorization: Bearer / X-Dev-User + X-Tenant-Id──► Core FastAPI
   React (client)                /api/core/[...path]  (stream NDJSON / SSE không đệm)             (RLS + grants, một cửa)
                                 /api/auth/*          (config, dev, google, tenant, session, logout)
```

* **Chỉ là máy khách của API core.** Không có truy cập DB, không tính số, không quyết quyền. Core ép quyền (RLS + grants).
* **BFF** (`src/app/api/core`, `src/lib/server/*`): giữ mã phiên của core trong cookie `barista_session` (httpOnly, SameSite=Lax, mã hoá
  AES-GCM — devtools cũng không đọc được). Chỉ chuyển `content-type`, `accept`, `idempotency-key`, `x-filename`; danh tính và công ty luôn
  lấy từ cookie (header danh tính do trình duyệt gửi bị bỏ). Chỉ cho `/v1/*` (không `v1/auth`, `v1/dev`). Ghi từ Origin khác ⇒ 403.
* **Đăng nhập**: Google Identity Services → `POST /v1/auth/google {id_token, nonce}` ⇒ chọn công ty. Dev (core `BARISTA_DEV_AUTH=1`): chọn vai
  theo nhóm kèm "Thử hỏi"; BFF giữ `{email, công ty}` và gửi `X-Dev-User` (core chỉ nhận từ loopback). `src/proxy.ts` đẩy người chưa đăng nhập về `/login`.
* **Giao diện do server mô tả**: manifest → màn; mỗi màn = một truy vấn trả trang `table | detail | card`; form từ `/v1/forms/{lệnh}`.
  Giải mã dung thứ ở `src/lib/decode.ts` (khối lạ ⇒ bỏ qua).

## Bản đồ sang app Mac

| Web | macOS (`BaristaDesk`) |
|---|---|
| `components/desk/desk-context.tsx`, `chat-context.tsx` | `Desk.swift` |
| `components/desk/shell.tsx`, `rail.tsx`, `column.tsx` | `Rail.swift` (DeskShell, Rail, ColumnHeader) |
| `components/desk/messages.tsx` | `Rail.swift` (MessagesColumn, ThreadPane, RoomAvatar), `Talk.swift` (TalkInbox) |
| `components/desk/workspace-column.tsx` | `Rail.swift` (WorkspaceColumn) |
| `components/blocks/chart.tsx`, `lib/chart.ts` | `Charts.swift` |
| `lib/talk.ts`, `lib/shortcuts.ts` | `Talk.swift`, `Fmt.stamp`, phím tắt của `DeskShell` |
| `components/desk/chat-column.tsx` | `ChatView.swift` |
| `components/desk/canvas.tsx` | `CanvasView.swift` |
| `components/pages/screen-view.tsx`, `table.tsx`, `detail.tsx` | `ScreenView.swift` |
| `components/pages/card.tsx`, `blocks/*` | `Cards.swift` (`board`, `thread` ở `Cards.swift` / `Talk.swift`) |
| `components/forms/form-body.tsx`, `dialogs.tsx` | `FormSheet.swift`, `ImportSheet.swift`, `OpenSheet` |
| `components/desk/notices.tsx` | `Notices.swift` |
| `components/login/login-view.tsx`, `app/api/auth/*` | `Shell.swift` (LoginView), `Auth.swift` |
| `lib/format.ts` | `Fmt` trong `Theme.swift` |
| `app/globals.css` | `Theme.swift` (màu, thang chữ) |

"Tin nhắn" kiểu app chat: cột luôn là danh sách (trợ lý, thông báo, phiếu chờ duyệt, phòng, trao đổi trên hồ sơ; dòng đang chọn tô sáng);
bên phải hiện mục đang chọn cỡ lớn — hộp thông báo, hoặc cuộc trò chuyện + **ngăn kiểm tra** (~440px, thẻ đang hoạt động của khung làm việc;
đóng sẵn, bật bằng "Thành viên" / "Hồ sơ" hoặc bấm thẻ đã chia sẻ trong tin). Không chọn gì / bấm "Phiếu chờ duyệt" ⇒ bên phải là khung làm việc.
Hẹp: chỉ danh sách; chọn ⇒ cuộc trò chuyện cả bề ngang + nút quay lại.

Thẻ đề xuất của trợ lý (0049): lượt chat DỪNG ở đề xuất có `resumable: true`; "Xác nhận" / "Bỏ đề xuất" đi qua
`POST /v1/assistant/proposals/decide` (NDJSON qua BFF như chat). Dòng đầu quyết form (`rejected` ⇒ giữ form, hiện lỗi, khoá mới · `decided` ⇒
thẻ xong); nếu `resuming` thì phần trợ lý chạy tiếp thành một lượt trả lời mới trong chat (`lib/decide.ts`, `chat-context.tsx`). Thẻ cũ
không `resumable` vẫn gọi thẳng `/v1/commands/{lệnh}`.

"Xác nhận cả N đề xuất" (0050): dưới câu trả lời có ≥ 2 thẻ đề xuất `resumable` chưa quyết ⇒ xác nhận lần lượt (khoá mới mỗi thẻ), lỗi đầu tiên
⇒ dừng + mở thẻ đó (`lib/confirm-all.ts`). Mở lại cuộc trò chuyện cũ: thẻ đã quyết (`decided` / `pending.calls` của lượt) hiện kết quả.

Ảnh gửi trợ lý (0050): dán (chỉ chặn khi bảng nháp có FILE ảnh), kéo thả vào cột trợ lý, hoặc nút chọn ảnh; nén ở trình duyệt (cạnh ≤ 2000 px,
JPEG 0,85 — `lib/images.ts`), tối đa 3 ảnh × 5 MB; gửi qua `images` của chat stream. Server KHÔNG lưu ảnh: bong bóng hiện ảnh cục bộ trong
phiên; mở lại cuộc trò chuyện chỉ còn "N ảnh (không lưu lại)".

Phím tắt: ⌘K / Ctrl+K tìm; **Alt+1…9** chuyển mục của thanh (⌘/Ctrl+số là phím chuyển tab của trình duyệt — trang không chặn được);
**Ctrl+Shift+S** (hoặc ⌃⌘S) ẩn / hiện thanh bên trái — lựa chọn lưu ở localStorage `barista-rail-hidden`. Dưới 980px: thanh + MỘT ngăn.

Khác biệt có chủ ý: chat **bám đáy** khi đang ở sát đáy (người dùng cuộn lên thì để yên) thay vì "câu hỏi lên đỉnh" của Mac; không có giọng nói (ngoài phạm vi).

## Điểm mở rộng

* **⌘K** (`components/desk/palette.tsx`): nhảy tới màn (lọc tại chỗ) + tìm toàn hệ thống qua truy vấn `crm.find {q}` (≥ 2 ký tự, debounce 200ms);
  kết quả là các khối `list` có `open` ⇒ mở thành thẻ. Thêm nguồn tìm = thêm một dòng vào `SEARCH_SOURCES`.
* **Khối thẻ mới**: thêm một `case` vào `decodeBlock` (`lib/decode.ts`) + `CardView`. Khối chưa biết đang tự bị bỏ qua.
* **Icon**: server gửi tên SF Symbols; ánh xạ sang lucide ở `components/icons.tsx` (tên lạ ⇒ ô lưới).
