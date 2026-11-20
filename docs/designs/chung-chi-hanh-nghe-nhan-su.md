# Chứng chỉ hành nghề có hạn của nhân sự — ghi chú thiết kế (bản thảo, CHƯA là ADR, chưa có code)

> Người soạn: Claude (Anthropic), phiên "báo cáo quản trị", 11/11/2026, theo yêu cầu của firstcc-01 ("đặt trong gói `hr`"). **Số đo** lấy từ bản sao sheet nhân sự của khách (tổng hợp; không có tên người, số chứng chỉ hay giấy tờ cá nhân trong tài liệu này). **Nhận định và đề xuất tách riêng, chưa chốt.** Chờ trả lời Q-F11.

## 1. Vì sao là hạ tầng chung

Nhiều ngành chỉ được giao việc cho người có chứng chỉ còn hạn: điều dưỡng / bác sĩ (chứng chỉ hành nghề), giáo viên (chứng chỉ nghiệp vụ), lái xe (bằng lái, giấy phép vận tải), môi giới bất động sản (chứng chỉ môi giới), kiểm toán / kế toán, thợ điện, huấn luyện viên. Việc cần: **ai đang có chứng chỉ gì, hết hạn khi nào, bản scan ở đâu, sắp hết thì báo, và tuỳ ngành có thể chặn giao việc cho người hết hạn.** Hiện Barista không có chỗ chứa (đã kiểm: không bảng chứng chỉ trong `hr`).

## 2. Số đo ở khách (sheet nhân sự)

988 dòng; **296 người đang làm**. Trong số đó: 199 có số chứng chỉ, 243 có ngày hết hạn, **211 đã quá hạn theo sheet**, 53 không có thông tin; chỉ 1 người hết hạn trong 90 ngày tới; 92 dòng có bản công chứng. Cấp bậc trưởng phòng trở lên đủ thông tin; thiếu tập trung ở nhân viên và dòng không ghi cấp bậc. **Chưa biết 211 quá hạn là thật hay sheet không được cập nhật** (Q-F11) — vì vậy tính năng phải chịu được dữ liệu cũ, không coi mọi "hết hạn" là vi phạm.

## 3. Mô hình đề xuất (chưa chốt)

Trong gói `hr`, hai bảng dữ liệu của công ty:
- `hr.credential_kinds` — loại chứng chỉ do công ty khai (mã, tên, có bắt buộc số không, thời hạn mặc định tháng, nhắc trước bao nhiêu ngày). Không có loại cố định trong code.
- `hr.credentials` — mỗi dòng một chứng chỉ của một người (định danh từ `parties`/`hr`): loại, số, ngày cấp, ngày hết hạn, nơi cấp, tệp scan (kho tệp `files`), trạng thái (đang dùng / thu hồi / thay bằng bản mới). Chỉ thêm; sửa = dòng mới thay dòng cũ (giữ lịch sử).
- **Lệnh:** `hr.set_credential` (importable — nạp từ sheet khi khách chuyển), `hr.revoke_credential`. Người xin: HR hoặc quản lý đội; chính người đó thấy chứng chỉ của mình và tải bản scan lên (HR duyệt). Số chứng chỉ không vào ngữ cảnh trợ lý và không vào nhật ký thao tác (`audit=False`).
- **Nhắc hết hạn:** vòng quét hằng ngày theo múi giờ công ty (0169): báo người có chứng chỉ + quản lý đội + HR ở mốc 90 / 30 / 7 ngày và khi hết hạn (cùng cơ chế thông báo / việc theo dõi sẵn có), có thẻ buổi sáng cho HR "sắp hết hạn".
- **Báo cáo:** danh sách theo loại / đội / người, đếm "còn hạn / sắp hết / quá hạn / chưa có thông tin" (4 nhóm như số đo mục 2) — thẻ cho HR và giám đốc; số có nguồn bấm ra dòng gốc.

## 4. "Chặn" là luật (dữ liệu), kiểm ở lệnh cụ thể

Chặn theo ngành không viết cứng. Đề xuất `hr.credential_rules` — dữ liệu công ty: (loại chứng chỉ, áp cho vai / đội nào, kiểm ở lệnh nào, chế độ `warn` hoặc `block`). Các lệnh của gói khác **gọi một điểm cắm** (như các điểm cắm sẵn có của acct) hỏi "người này có đủ chứng chỉ cho việc này không": gói bán hàng hỏi khi ghi nhận giao dịch; gói lịch hỏi khi xếp ca; gói lái xe hỏi khi giao chuyến. Không có luật ⇒ không kiểm (hành vi cũ). Mặc định `warn`: ghi được nhưng cảnh báo và lưu vào nhật ký; `block` chỉ khi công ty tự bật. Cách này giữ "chặn" ngoài code của từng ngành và không đổi lệnh của gói nào khi chưa có luật.

## 5. Rủi ro và điều cần quyết
- **Dữ liệu cá nhân:** số chứng chỉ + bản scan là dữ liệu cá nhân (Nghị định 13/2023): quyền xem hẹp (HR, chính người đó, quản lý trực tiếp xem trạng thái chứ không xem số), không gửi cho model.
- **Chặn nhầm vì dữ liệu cũ** (211/296 quá hạn trong sheet): mặc định `warn`; khi nạp từ sheet đánh dấu nguồn "nạp từ hệ cũ, chưa xác minh" để không chặn người làm trên dữ liệu chưa kiểm.
- **Phạm vi bản đầu:** hai bảng + nhắc + báo cáo; luật chặn và điểm cắm làm sau khi khách xác nhận Q-F11 (chứng chỉ có bắt buộc cho việc bán không, hết hạn thì có được ghi nhận giao dịch / hoa hồng không).
