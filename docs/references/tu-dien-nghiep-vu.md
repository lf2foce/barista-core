# Từ điển nghiệp vụ — hệ thống ĐANG hiểu thế nào (để khách đầu tiên ký / sửa)

Cuộc họp 31/10 (P0): "lead / khách / qualified lead / giao dịch / doanh số / doanh thu / thực thu" đang được mỗi bên hiểu một kiểu ⇒ số lệch.
Bảng dưới ghi đúng cách **code hiện tại** tính (kèm chỗ trong code), cột cuối để khách đầu tiên ghi "đúng" hoặc "sửa thành…". Sửa ở đây trước, rồi mới
sửa code — một định nghĩa, một chỗ tính (màn điều hành, báo cáo, trợ lý đều đọc cùng chỗ). Cập nhật: 31/10/2026.

## Khách và lead

| Thuật ngữ | Hệ thống đang hiểu | Ở đâu | khách đầu tiên xác nhận |
|---|---|---|---|
| **Khách** | Một NGƯỜI, nhận diện bằng SĐT (một SĐT = một khách trong công ty) | `crm.customers` | |
| **Lead** | Một khách × một dự án quan tâm. Một khách quan tâm 2 dự án = 2 lead | `crm.leads` (duy nhất theo khách + dự án) | |
| **Chặng lead** | Lead (mới) → Tiềm năng → Giao dịch (đã có giao dịch) · hoặc Mất (kèm lý do) | `crm.STAGES` | |
| **Đã liên hệ** | Có bất kỳ hoạt động nào trừ "ghi chú nội bộ" (gọi, gặp, Zalo, email, dẫn xem, gửi profile). Dùng chung cho chia lead, trần lead chưa gọi, hạn gọi, thẻ buổi sáng | `crm.log_activity` | |
| **Gọi được** | Hoạt động "Gọi điện" có kết quả "Nghe máy" | `crm.OUTCOMES` | |
| **Qualified lead** | **Chưa có định nghĩa riêng.** Báo cáo chiến dịch đang đếm lead ở chặng Tiềm năng hoặc Giao dịch là "đủ điều kiện" | `crm.lead_stats` | |
| **Lead về ngày nào** | Ngày khách để lại thông tin nếu người nhập khai; không khai thì ngày tạo trên hệ thống | `crm.leads.captured_at` | |
| **Nhu cầu** | Số PN, ngân sách tối đa, hướng, mục đích, mong muốn khác — sửa được, giữ lịch sử "trước → sau" | `crm.leads.interest` | |

## Giao dịch và tiền

| Thuật ngữ | Hệ thống đang hiểu | Ở đâu | khách đầu tiên xác nhận |
|---|---|---|---|
| **Giao dịch** | Một căn × một khách: Giữ chỗ → Đã cọc → Đã ký HĐ · hoặc Đã huỷ | `deals.STATUS` | |
| **Giao dịch ký trong kỳ** | Giao dịch có **ngày ký HĐMB** trong kỳ, không tính giao dịch đã huỷ | `deals.period_stats` | |
| **Giá trị giao dịch (GMV)** | Giá bán ghi trên giao dịch (chưa phân biệt niêm yết / sau chiết khấu / có VAT) | `deals` | |
| **Phí môi giới** | Giá trị giao dịch × tỷ lệ phí của dự án (hợp đồng đại lý với CĐT). Căn ký gửi: theo thoả thuận với chủ nhà (% hoặc số tiền), chủ nhà trả | `catalog.projects.fee_rate_pct`, `catalog.consignments` (0171) | |
| **Hàng ký gửi (thứ cấp)** | Căn chủ nhà gửi công ty bán lại: một căn trên bảng hàng (thị trường Thứ cấp) + một thoả thuận ký gửi (chủ nhà, giá muốn bán, phí, hiệu lực). Một căn một thoả thuận đang hiệu lực; gửi lại = thoả thuận mới | `catalog.units.market`, `catalog.consignments` (0171) | |
| **Nguồn hàng / lý do khoá căn** | Nhãn của căn do công ty khai: nguồn hàng (độc quyền, hàng CĐT, lấy chéo…) và lý do căn không bán (CĐT thu hồi, đơn vị khác đã bán, công ty giữ lại, chủ nhà rút…) — lọc được trên bảng hàng | `catalog.unit_terms` (0156, 0171) | |
| **Doanh thu ghi nhận** | Phí môi giới, ghi nhận theo **ngày phát hành hoá đơn** cho CĐT (= ngày ký) | `acct` hoá đơn | |
| **Thực thu** | Tiền CĐT đã trả thật, theo **ngày nhận tiền** (phiếu thu) | `acct` phiếu thu | |
| **Công nợ CĐT** | Hoá đơn phí đã phát hành − đã thu; tuổi nợ tính theo số ngày quá hạn trả (mặc định 30 ngày sau ký) | `acct.collections` | |
| **Khách đóng tiền** | Các đợt khách trả CĐT theo tiến độ dự án — sàn chỉ theo dõi để nhắc, KHÔNG phải tiền của sàn | `deals` đợt thanh toán | |

## Hoa hồng và ví

| Thuật ngữ | Hệ thống đang hiểu | Ở đâu | khách đầu tiên xác nhận |
|---|---|---|---|
| **Hoa hồng phát sinh** | Khi ký HĐMB: giá trị giao dịch × tỷ lệ bậc, bậc chọn theo doanh số đã ký **cuốn chiếu 365 ngày TẠI LÚC KÝ**, áp cho cả khoản (không tính phần vượt), + thưởng KPI. **Không hồi tố** giao dịch cũ khi lên bậc (họp 31/10 nêu hồi tố — câu G1) | `commission` | |
| **Hoa hồng chờ về** | Đã phát sinh, chờ CĐT trả phí | ví | |
| **Hoa hồng về ví** | Theo tỷ lệ phí CĐT đã trả (CĐT trả 50% ⇒ về ví 50%) | `acct.fee_received` | |
| **Ứng** | Xin ≤ hạn mức (tỷ lệ × hoa hồng chờ − đang ứng), qua chuỗi duyệt trưởng nhóm → kế toán; có phí ứng. Tỷ lệ đang là số đặt tạm | `commission` | |
| **Rút** | Xin ≤ số dư ví, kế toán duyệt, không phí | `commission` | |
| **Huỷ giao dịch** | Huỷ khoản chờ; phần đã về ví ⇒ bút toán thu hồi (ví có thể âm) | `commission` | |

## Marketing

| Thuật ngữ | Hệ thống đang hiểu | Ở đâu | khách đầu tiên xác nhận |
|---|---|---|---|
| **Chi phí trên mỗi lead (CPL)** | Chi tiêu quảng cáo của chiến dịch ÷ số lead gắn mã chiến dịch đó | `mkt` | |
| **Lead quảng cáo** | Lead có nguồn "Quảng cáo" — mặc định qua đội Digital chia (có gợi ý), nguồn khác tự chia | `leadflow` | |
| **Góp chi phí quảng cáo (7/3)** | **Chưa có** — họp 31/10 nêu sales góp chi phí; câu G3 | — | |
