# Barista Web — quy tắc cho agent

Máy khách web của Barista (đọc `README.md` trước; luật bất biến của cả repo ở `../CLAUDE.md`). **Chỉ sửa trong `web/`.**

1. **BFF only, không DB.** Web là máy khách thuần của API core. Không drizzle / SQL / driver DB. Trình duyệt chỉ gọi `/api/core/*` và
   `/api/auth/*`; mã phiên của core ở lại trong cookie httpOnly (đừng bao giờ đưa xuống JS, đừng log). Danh tính + công ty luôn lấy từ cookie.
2. **Không tự tính, không tự quyết quyền.** Số liệu, nhãn, nút nào hiện (`when`, `row_actions`, `actions`) đều do server gửi; web chỉ vẽ.
   Server vẫn kiểm lại khi chạy lệnh. Thấy mình cộng / lọc / suy quyền ở client ⇒ sai chỗ — sửa ở core.
3. **Hợp đồng chỉ được THÊM.** Trường lạ bỏ qua, khối thẻ lạ ⇒ `{type:"unknown"}` (không vỡ thẻ), trường thiếu có mặc định. Đổi / bỏ trường của
   server ⇒ sửa `lib/decode.ts` + `lib/types.ts` cùng commit và báo người giữ backend + app Mac.
4. **Một cửa ghi:** mọi thao tác ghi = `POST /v1/commands/{tên}` với `Idempotency-Key` (một khoá cho mỗi hộp thoại, đổi khi `conflict`/`invalid`).
   Thẻ đề xuất của trợ lý do NGƯỜI bấm xác nhận — web không bao giờ tự chạy lệnh thay trợ lý.
5. **Giao diện = bản sao ngôn ngữ của app Mac:** tiếng Việt toàn bộ; thang chữ `t20/t16/t14/t14m/t12/t12m` (chỉ 400/500/600); không in hoa có dấu, không
   nghiêng; màu qua biến CSS trong `globals.css` (sáng + tối); không đổ bóng ở thẻ; `Be Vietnam Pro`.
6. **Stream không đệm:** NDJSON (chat) và SSE (thông báo, trao đổi) đi thẳng qua BFF; sửa BFF nhớ giữ `downstreamHeaders`.
7. **Kiểm tra trước khi xong:** `pnpm typecheck && pnpm lint && pnpm test`; đổi giao diện ⇒ `pnpm dev` + `pnpm shots <thư mục>` rồi NHÌN ảnh,
   và `pnpm menus` (mở mọi menu, 0 lỗi console). base-ui: `DropdownMenuLabel` PHẢI nằm trong `DropdownMenuGroup`.
   SVG (Recharts): id của `url(#…)` chỉ chữ / số (useId có ký tự lạ); màu qua biến CSS `--chart-*`.
   Logic thuần mới (định dạng, giải mã, payload) ⇒ thêm test ở `src/lib/__tests__/`.
   **Form (`components/forms/form-body.tsx`, logic thuần ở `lib/form-ui.ts`):** ô trống chưa chạm KHÔNG báo lỗi (lỗi sau khi rời ô / bấm gửi, nút gửi mờ tới khi đủ ô bắt buộc); không lặp chữ "Bắt buộc" —
   hầu hết bắt buộc ⇒ ô tuỳ chọn ghi "không bắt buộc", ít ⇒ dấu *; `options_query` ⇒ ô chọn có tìm (`combobox.tsx`: bỏ dấu, ↑↓ Enter Esc, "Gần đây", hỏi lại server bằng `q` khi danh sách bị cắt); ô phụ thuộc
   (`options_with` / `options_args`) tải lại + xoá khi ô kia đổi; giá trị máy điền sẵn (`values` của nút, `initial`, danh sách chỉ một dòng) là chip ✨ + "Chấp nhận tất cả"; nhóm `group` gấp, tự mở khi có giá trị / lỗi; hai ô ngắn
   liền nhau chung hàng; điện thoại (< 640px) hộp là tấm trượt từ dưới lên. Ngày hiện "1 tháng 12, 2026", tiền có dấu chấm nghìn. Đổi hành vi ⇒ đổi cả `macos/.../FormUI.swift` + `FormSheet.swift` (cùng hợp đồng).
8. Next 16 khác bản cũ (`proxy.ts` thay `middleware.ts`, `params` là Promise…): đọc `node_modules/next/dist/docs/` trước khi dùng API lạ.
9. Không commit khi chưa được yêu cầu rõ.
