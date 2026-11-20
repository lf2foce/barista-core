# Số liệu marketing chuẩn của Phê Nâu — phần đã mang sang khách đầu tiên (31/10/2026)

Nguồn: `phenau_v3/docs/architecture/108-canonical-metrics-contract.md` + `backend/app/services/ads_effectiveness_service.py`,
`executive_connector_service.py`, `marketing_report_service.py`, `lead_attribution_service.py`. (Báo cáo rà bởi agent, chưa chạy SQL / PROD.)

## Mang sang (đã làm trong `mkt.overview`)
| Luật Phê Nâu | khách đầu tiên |
|---|---|
| Mỗi số khai 6 trục: phạm vi, hạt, thời điểm, nguồn, mẫu số, quy tắc null (108 §2) | Ghi trong chú thích code + "Lưu ý" trên thẻ |
| CPL = chi phí ÷ lead **đếm theo người** vào từ chiến dịch, cùng kỳ; KHÔNG chia cho lượt nhấp / lead nền tảng | `lead_stats` đếm khách; `leads_platform` chỉ để đối chiếu |
| Lead tính theo ngày khách để lại thông tin (`captured_at`), không phải ngày nhập | Migration 111 `crm.leads.captured_at`; `create_lead(captured_on)`; giờ Việt Nam |
| Ẩn TỈ LỆ (CPL, ROAS) khi kỳ xem ra ngoài mốc dữ liệu tin cậy hoặc đuôi kỳ trễ > 3 ngày; giữ số tuyệt đối; chưa khai mốc ⇒ báo thẳng | `mkt.coverage` + `mkt.set_coverage` |
| Số 0 ≠ "chưa đo được"; không kết luận chiến dịch kém khi nhóm lead còn đang chín (≥ 50% còn mở) | Lưu ý + gợi ý "chi mà chưa thấy lead" = KIỂM TRA, không "cắt" |
| ROAS = giá trị ký ÷ chi phí; None khi thiếu một vế | Phí môi giới đã ký (nhóm lead của kỳ) ÷ chi phí |
| So kỳ trước cùng độ dài, Δ% chỉ cho chi phí / lead; None khi kỳ trước 0 | Có |
| Gợi ý: tăng ngân sách top 2 theo ROAS; "chi không lead"; "có lead chưa ra giao dịch" (không đổ lỗi sale); chặn gợi ý khi dữ liệu chưa tin cậy | Có |
| Nhận diện chiến dịch theo id, tên chỉ là nhãn; tên bị ghi đè khi đổi | khách đầu tiên định danh theo `code` (không đổi); chưa cần nhật ký tên |

## Chưa mang (ghi backlog)
- Kết quả cuộc gọi / lead rác ("Thuê bao" 5%) — cần dữ liệu cuộc gọi chuẩn (đã có `outcome` trên hoạt động; chưa có thống kê rác).
- Phễu quảng cáo theo giai đoạn có `measurable=false` từng bước; phủ sóng bàn giao CRM (`handoff coverage`) — khách đầu tiên tự là CRM nên khác.
- Múi giờ tài khoản quảng cáo, đổi tiền tệ, nối Meta Ads API (khách đầu tiên nhập chi phí tay / dán file).
- Cảnh báo bất thường chi phí theo giờ (z-score, đề xuất của Phê Nâu, chưa làm ở chính Phê Nâu).

## Bẫy đã ghi bên Phê Nâu — khách đầu tiên cần nhớ
Ba nghĩa "lead chất lượng" lệch nhau tới 3 lần (có SĐT / đã bàn giao / sale đánh giá tốt): khách đầu tiên dùng một nghĩa (đã lên Tiềm năng hoặc Giao dịch), đừng đổi
nghĩa — thêm trường mới. Ghép chi phí với lead theo thời điểm khác nhau (chi phí theo ngày chạy, lead theo ngày để lại thông tin) là SO KỲ, không phải đoàn hệ.
