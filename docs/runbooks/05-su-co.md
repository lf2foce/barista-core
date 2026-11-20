# Runbook 05 · Sự cố

| Triệu chứng | Nguyên nhân thường gặp | Xử lý |
|---|---|---|
| App: "Không phải thành viên đang hoạt động" | Vừa `seed --reset` (mã công ty đổi) hoặc tài khoản bị khoá | Chọn lại vai |
| Trợ lý: "Chưa cấu hình model" | Thiếu khoá | Đặt `BARISTA_LLM_API_KEY` hoặc `..._FILE` + `..._VAR`, khởi động lại |
| Trợ lý: "đang giới hạn số lượt gọi" / "quá tải hoặc gián đoạn" / "hết hạn mức / tín dụng" | Nhà cung cấp model lỗi sau khi đã thử lại (loại lỗi ở `core.ai_usage.error_class`) | Runbook 15: xem loại lỗi theo giờ; khai nhà cung cấp dự phòng `BARISTA_LLM_FALLBACK_MODELS`; `config` / `quota` ⇒ kiểm khoá / nạp tín dụng |
| Khởi động: "Migration … đã chạy nhưng nội dung đã đổi" | Sửa file migration đã chạy | Hoàn tác, tạo file mới (quyết định 0019) |
| Hoa hồng / hoá đơn không sinh sau khi ký | Bên nghe sự kiện lỗi (vd chưa có chính sách hoa hồng hiệu lực ngày ký) | Quản trị › Sự kiện lỗi: đọc lỗi, sửa dữ liệu, bấm Chạy lại |
| "Ngoài phạm vi quyền: không được ghi dòng này" | RLS chặn (vd sale nhập giao dịch cho người khác) | Đúng thiết kế — kiểm vai / phạm vi trong Vai trò & quyền |
| Trợ lý trả 0 dòng với bộ lọc lạ | Model đoán giá trị lọc | Ô lọc phải có danh sách lựa chọn (`field(options=…)`) ⇒ tự thành enum cho model |
| Huỷ giao dịch mà hoá đơn CĐT đã thu một phần | Kế toán không tự huỷ (đúng thiết kế) | Sự kiện lỗi báo; kế toán xử lý hoàn / bù trừ tay |
| App Mac thoát khi bấm một nút trong khung làm việc | Gọi `@Environment(\.dismiss)` ở view nằm trong cửa sổ chính: macOS hiểu là ĐÓNG CỬA SỔ (cả khi `isPresented` = true) | Đóng sheet bằng closure do sheet truyền vào (`DetailView(onClose:)`), không dùng `dismiss` của môi trường trong view dùng chung |
| Bút toán lệch nhưng thẻ đề xuất của trợ lý vẫn "chạy được" | Chạy thử hoàn điểm lưu trước khi ràng buộc hoãn được kiểm | Đã sửa: `executor.check_deferred` (0027) |
| Trợ lý viết `$\rightarrow$` / LaTeX trong câu trả lời | Thói quen của model | Quy tắc định dạng trong `agent/chat.py: RULES` (không LaTeX, mũi tên viết →) |
| Sau khi nạp dữ liệu hệ cũ: hàng chục nghìn thông báo, lead tự đổi người giữ, trợ lý tự chạy, API ~90% CPU, báo cáo chậm | Lớp nạp không truyền `historical=True`, hoặc DB nạp trước 0185 chưa đánh dấu ⇒ vòng quét coi bản ghi cũ là vừa tới hạn; việc theo dõi (`work.items`) của dữ liệu nạp còn mở | Tắt vòng quét trên bản cài, khôi phục DB sạch, làm theo [runbook 16](16-nap-du-lieu-he-cu.md); KHÔNG tăng CPU (0185) |
| Trợ lý (429): "Bạn đang có N lượt trợ lý chạy cùng lúc (mức tối đa)…" | Người đó còn lượt đang chạy — kể cả lượt chạy tiếp sau khi đã đóng app, lượt chạy tiếp sau khi quyết đề xuất | Đợi lượt trước xong / bấm Dừng; app tự đọc `Retry-After`. Mức chung của bản cài: `BARISTA_CHAT_TURNS_PER_PERSON` (mặc định 3), 0 = không giới hạn (0200) |
| Trợ lý (429): "Công ty đang có N lượt trợ lý chạy cùng lúc (mức tối đa)…" | Cả công ty chạm trần lượt cùng lúc | Thử lại sau ít giây; thường xuyên ⇒ xem tải, cân nhắc nâng `BARISTA_CHAT_TURNS_PER_COMPANY` (mặc định 20) |
| Trợ lý (503): "Trợ lý đang quá tải — thử lại sau khoảng 30 giây." | Tiến trình API chạm `BARISTA_CHAT_TURNS_PER_PROCESS` (mặc định 60) — mỗi bản API đếm riêng | Xem `docker stats` / pool DB trước khi nâng mức; nâng mức không thêm kết nối DB |
| Trợ lý (503): "Máy chủ đang khởi động lại — thử lại sau ít giây." | Đang phát hành / khởi động lại: bản cũ thôi nhận lượt mới | Chờ bản mới lên (runbook 11); không cần làm gì |
| Trợ lý (413): "Yêu cầu quá lớn (tối đa N MB)…" / (422) "Câu hỏi quá dài…" | Ảnh / ghi âm quá cỡ, câu dán quá `BARISTA_CHAT_MESSAGE_MAX_CHARS` (mặc định 8000 ký tự) | Bớt ảnh, rút ngắn; tài liệu dài ⇒ tải vào kho tài liệu rồi hỏi |
| Cuộc trò chuyện có lượt "(Lượt trả lời quá thời gian cho phép…)" hoặc "(Máy chủ vừa khởi động lại giữa lượt…)" | Lượt quá `BARISTA_CHAT_TURN_MAX_S` (mặc định 300 giây), hoặc còn chạy khi máy chủ tắt quá `BARISTA_SHUTDOWN_TURN_GRACE_S` (8 giây) — lượt lưu `interrupted` = `timeout` / `shutdown`, bước đã chạy giữ nguyên | Người dùng hỏi lại; lệnh đã ghi không hoàn tác — xem nhật ký thao tác theo vết lượt. Máy chủ chết đột ngột (kill -9, mất điện) thì lượt đang chạy KHÔNG kịp lưu |
| Log: "chưa ghi được token của lượt … — xếp hàng ghi lại" / "còn N lượt dùng AI chưa ghi được" | Pool DB kín quá `BARISTA_AI_USAGE_WAIT_S` (5 giây) hoặc DB chập — dòng `core.ai_usage` xếp hàng trong bộ nhớ, thử lại nền, ghi nốt lúc tắt máy chủ | Thường tự hết; lặp lại ⇒ pool quá nhỏ cho tải hoặc DB có vấn đề. Tiến trình chết đột ngột thì dòng đang chờ mất (đã có log từng dòng) |
