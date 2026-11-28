# 24 · Luật tự động theo lịch, nhờ trợ lý soạn, hộp thư đi (email)

Vì sao: ADR 0265 (mục 1, 3, 4). Mục 2 của ADR (ngưỡng tiền là luật duyệt có điều kiện) CHƯA làm.

## 1. Bật gửi email cho một bản cài

Hộp thư đi gửi qua SMTP chuẩn của bản cài (`kernel/mailer.py`) — cùng cấu hình với đăng nhập bằng mã email:

```
BARISTA_SMTP_URL=smtps://<user>:<mật khẩu / khoá API>@<host>:465     # hoặc smtp://…:587 (STARTTLS)
BARISTA_MAIL_FROM=Tên bản cài <no-reply@vi-du.test>
```

- Tên miền trong `BARISTA_MAIL_FROM` phải khai SPF + DKIM ở nhà cung cấp thư, không thì thư vào spam. Địa chỉ GỬI luôn là địa chỉ này; từng công ty chỉ
  đổi **tên hiển thị** và **địa chỉ nhận trả lời** (Hộp thư đi › “Tên người gửi” — quyền `outbound` manage).
- Chưa đặt hai biến ⇒ nút “Gửi” báo “Bản cài chưa bật gửi email”; thư nháp vẫn soạn được và nằm chờ.
- Kiểm nhanh sau khi bật: soạn một thư cho chính mình (một người trong Cá nhân & tổ chức có email của mình), bấm Gửi, chờ ≤ 1 phút (vòng quét chung —
  `BARISTA_LEADFLOW_SWEEP_S`, mặc định 60 giây), mở thư: nhật ký phải có “Đã gửi”.

## 2. Vòng đời một thư

Nháp (người / module / trợ lý soạn) → **người bấm Gửi** (kiểm lại kênh đã bật + sự đồng ý nếu là chăm sóc / tiếp thị) → Chờ gửi → vòng gửi nền mỗi phút
(`outbound.deliver`) → Đã gửi. Lỗi ⇒ thử lại lùi dần (1, 2, 4… phút, tối đa 1 giờ một lần); quá `MAX_ATTEMPTS` (8) ⇒ “Không gửi được” + thông báo cho
người đã bấm gửi; “Gửi lại” đưa về hàng chờ, “Huỷ thư” bỏ hẳn. Mỗi bước một dòng nhật ký (ai, lúc nào, lỗi gì) trên thẻ thư.

- Trợ lý không bao giờ tự gửi: `outbound.send` là `agent="propose"` (thẻ đề xuất, người bấm Xác nhận mới gửi). Công ty muốn hai người (soạn ≠ gửi) ⇒
  Quản trị › Luật duyệt: đặt chuỗi duyệt cho lệnh “Gửi thư ra ngoài”.
- Người nhận = một người / tổ chức trong Cá nhân & tổ chức (gói `directory`); địa chỉ chốt lúc soạn từ email của họ, đọc bằng quyền người soạn.
- Tin “Chăm sóc” / “Tiếp thị” cần sự đồng ý hiệu lực cho đúng mục đích (Cá nhân & tổ chức › Ghi sự đồng ý) — kiểm lúc soạn và lúc bấm gửi.
- Giấy tờ đã ban hành: nút “Gửi cho …” soạn sẵn thư nháp kèm **tệp ban hành đóng băng** (.docx — đúng tệp tải về). Giấy cấp số trước khi có tệp đóng
  băng ⇒ thân thư là nội dung giấy, không đính kèm. Chưa có bản PDF đóng băng: đổi .docx → PDF lúc gửi cần LibreOffice trên máy chủ, để sau.
- Sự cố: thư đứng “Chờ gửi” lâu ⇒ xem lỗi gần nhất trên thẻ thư; lỗi “Máy chủ thư … không nhận thư” ⇒ kiểm `BARISTA_SMTP_URL` / khoá API / hạn mức
  của nhà cung cấp; “Không đọc được tệp …” ⇒ tệp đính kèm mất ở kho tệp (runbook kho tệp, 0122).

## 3. Đặt luật theo lịch

Quản trị › Luật tự động › Đặt luật:

1. **Hoặc theo lịch**: chọn Hằng ngày / Ngày làm việc / Hằng tuần (chọn thứ) / Hằng tháng (ngày 1–28 hoặc cuối tháng), **Lúc** (nửa tiếng một). Giờ là
   giờ của CÔNG TY (Quản trị › Công ty › múi giờ). Không gõ quy tắc lặp.
2. **Thì**: Báo · Tạo việc · Chạy lệnh (lệnh không cần mã bản ghi) · **Nhờ trợ lý soạn** · **Gửi tóm tắt phát hiện**.
3. Đích: một người hoặc một đội (luật theo lịch không có “người phụ trách bản ghi”).
4. Lưu ⇒ luật **chạy thử** trước (chỉ ghi “sẽ làm gì” vào các lần chạy); đúng ý thì bấm “Chạy thật”.

Hành vi: vòng quét mỗi phút chọn luật tới giờ, mỗi lượt chạy đúng một lần (hai tiến trình không chạy đôi). Máy chủ nghỉ / luật tắt lâu hơn 6 giờ ⇒ lượt lỡ
ghi “Bỏ lượt … không chạy bù”, chạy tiếp từ lượt kế — không dồn nhiều thông báo một lúc. Bật lại luật ⇒ lượt kế tính từ lúc bật.

### Nhờ trợ lý soạn

- **Nội dung** = lời dặn trợ lý (soạn gì, cho ai, dựa vào số nào). **Trợ lý được dùng** = bộ công cụ HẸP (1–12): truy vấn để đọc + lệnh “soạn đề xuất”.
  Lệnh trợ lý vốn được tự làm không có trong danh sách — lượt không người không bao giờ tự ghi.
- Lúc tới giờ: trợ lý của TỪNG người nhận chạy bằng quyền của chính người đó (chỉ phần công cụ người đó cũng dùng được); việc cần ghi thành **thẻ đề xuất**
  ở “Trợ lý soạn sẵn” của người nhận; người nhận nhận một thông báo “trợ lý đã soạn”. Bấm Xác nhận trên thẻ mới chạy, bằng quyền người bấm.
- Trần cứng: tối đa 5 người nhận một luật, 20 lần / ngày, 6 lượt gọi model + 60.000 token một lần soạn cho một người; hạn mức AI tháng của công ty / người vẫn
  áp. Token tính vào lượt dùng AI của người nhận (Quản trị › Lượt dùng AI, loại `auto:draft`).
- Model gọi NGOÀI giao dịch (bước chuẩn bị của vòng quét `flow.run_automation_drafts`); tiến trình chết giữa chừng ⇒ hết thuê 15 phút, lượt sau soạn lại
  (thẻ / thông báo có khoá chống lặp).

### Gửi tóm tắt phát hiện

Cần gói “Báo cáo chạy song song” (`findings`). Mỗi lượt gom phát hiện 7 ngày qua bằng quyền NGƯỜI NHẬN — người không xem được báo cáo cả công ty không có
bản tóm tắt (lần chạy ghi lý do). Thông báo trong app mở thẳng báo cáo.
