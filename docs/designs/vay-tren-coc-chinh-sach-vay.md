# Chính sách vay trên cọc / hợp đồng — ghi chú thiết kế (bản thảo, CHƯA là ADR, chưa có code)

> Người soạn: Claude (Anthropic), phiên "báo cáo quản trị", 11/11/2026, theo phân công của firstcc-01 (việc "vay trên cọc" không nằm trong thiết kế quỹ ôm). **Số đo** lấy từ bản sao TMS và sheet của khách đầu tiên trên máy (tổng hợp, không có tên người). **Nhận định và đề xuất tách riêng, chưa chốt.** Chờ firstcc-01 duyệt trước khi viết code; số ADR đánh khi có quyết.

## 1. Số đo — "vay" đang nằm ở đâu trong dữ liệu khách đầu tiên

| Chỗ | Trường | Số đo |
|---|---|---|
| Hợp đồng đặt cọc / giao dịch (TMS, 1.870 dòng) | `chinh_sach_vay` | **Vay 920 · Không vay 869 · trống 81** (53 / 61 / 28 dự án). Giao dịch trong kho OLAP (1.826 dòng): Vay 910 · Không vay 865 · trống 51 |
| Theo trạng thái (hợp đồng đặt cọc) | | Ký hợp đồng: Vay 853, Không vay 770, trống 30 · Đặt cọc: Không vay 70, trống 13 · Đã huỷ: Vay 62, Không vay 29, trống 38 |
| Dự án (TMS `projects`, 287 dòng) | `loan_percentage_support` (% hỗ trợ vay) | 41 dự án có giá trị, từ 30% đến 80%, phổ biến 70% |
| Dự án | `loan_support_period` (tháng) | 32 dự án có giá trị, từ 12 đến 40 tháng (24 và 30 phổ biến) |
| Lịch thanh toán (TMS `deal_payment_schedules`, 1.712 dòng) | `delay_payment_interest` | 1.514 dòng > 0 (0,02 đến 1) — **đã có trong Barista** là `deals.installments.late_interest_pct` |
| Khách tiềm năng (TMS) | `nhu_cau_vay` | cột có, **bảng rỗng** trong bản sao |

**Barista hôm nay:** không có trường nào cho việc khách vay hay không vay, cũng không có chính sách vay của dự án (chỉ có lãi chậm nộp theo đợt).

## 2. Nhận định (chưa chốt)

- Hai tầng khác nhau: **chính sách của DỰ ÁN** (CĐT / ngân hàng hỗ trợ vay tối đa bao nhiêu %, ân hạn bao lâu) là dữ liệu tham chiếu có hiệu lực theo thời gian; **lựa chọn của GIAO DỊCH** (khách này vay hay trả thẳng) là một sự thật của từng hợp đồng.
- "Vay" gần một nửa số hợp đồng ký (853 / 1.653) ⇒ ảnh hưởng thật đến dòng tiền: khoản thanh toán sau do ngân hàng giải ngân theo đợt, không phải khách tự chuyển — ảnh hưởng dự báo thu tiền và đối soát phải thu.
- Dự án có `loan_percentage_support` chỉ 41/287 trong khi 53 dự án có hợp đồng "Vay" ⇒ chính sách dự án không được nhập đủ; không nên bắt buộc có chính sách dự án mới ghi được "Vay".
- Tên "vay trên cọc" của firstcc-01 mô tả đúng cột `chinh_sach_vay` trên hợp đồng đặt cọc; chưa thấy dữ liệu về số tiền vay, ngân hàng, ngày giải ngân.

## 3. Phương án (đề xuất)

**A — chỉ một trường trên giao dịch:** `deals.deals.financing` ∈ {`loan`, `cash`, `unknown`} (mặc định `unknown`, cột có mặc định ⇒ không cần cửa sổ bảo trì). Lệnh `deals.set_financing` (importable, người xác nhận giao dịch / sales admin, có nhật ký; cùng chỗ với bước "xác nhận người bán & hoa hồng"). Nạp lịch sử từ `chinh_sach_vay` (Vay → loan, Không vay → cash, trống → unknown).
**B — thêm chính sách dự án:** bảng nhỏ `catalog.project_financing` (dự án, % hỗ trợ tối đa, ân hạn tháng, hiệu lực từ–đến) — như bảng phí theo dự án; chỉ để TRA và gợi ý, không chặn ghi giao dịch.
**C — bảng khoản vay theo giao dịch** (ngân hàng, số tiền, ngày giải ngân…): CHƯA làm; không có dữ liệu nguồn, và bản đầu chưa báo cáo nào cần.

**Đề xuất:** A trước (nhỏ, có dữ liệu thật, nạp được ngay); B khi khách đầu tiên gửi chính sách dự án đủ (41/287 chưa đủ); C chỉ khi có yêu cầu báo cáo giải ngân. Theo nguyên tắc "đơn giản dễ bảo trì": không tạo bảng cho thứ chưa có nguồn.

## 4. Ai nhập, báo cáo nào dùng

- **Ai nhập:** sales admin khi xác nhận giao dịch (khách nói lúc đặt cọc); kế toán sửa khi giải ngân cho thấy khác. Sale không tự đổi sau khi đã xác nhận.
- **Báo cáo dùng:** (1) giao dịch / giá trị theo hình thức thanh toán (vay – trả thẳng) trong Báo cáo quản trị; (2) dự báo thu tiền và phải thu: tách "chờ ngân hàng giải ngân" khỏi "khách chậm đóng" (hiện lẫn chung trong tuổi nợ); (3) tỷ lệ huỷ theo vay / không vay (huỷ: Vay 62, Không vay 29 trong dữ liệu — cần xác nhận có phải mẫu thật).
- **Không dùng:** hoa hồng (tính khi CĐT trả phí cho sàn — Q-B13 / ADR hoa hồng); không đổi cách tính phí theo vay.

## 5. Câu hỏi cho khách đầu tiên (đã thêm vào sổ câu hỏi)

Q-B52: "Vay" nghĩa chính xác là gì (khách vay ngân hàng do CĐT hỗ trợ? vay ngoài?), ai nhập, lúc nào; có cần số tiền vay, ngân hàng, ngày giải ngân không?  Q-D31: chính sách hỗ trợ vay theo dự án (% tối đa, ân hạn): nguồn chuẩn, ai cập nhật; 41 dự án có số, các dự án còn lại thế nào?
