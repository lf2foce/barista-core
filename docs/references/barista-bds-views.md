# Đối chiếu từng view: barista_bds → khách đầu tiên (31/10/2026)

Nguồn: rà mã `barista_bds` (backend/src/barista, macos/Sources/BaristaDesk). Khách ưng góc nhìn của bds ("câu hỏi trong ngày của sale"),
nên khách đầu tiên học cách nghĩ đó chứ không chỉ chép màn. Cột "khách đầu tiên" ghi việc đã làm hoặc còn lại.

| View của bds (cách tính) | Học được gì | khách đầu tiên |
|---|---|---|
| **Sáng nay chăm khách nào trước** (`nudges.py`): xếp vừa mở tài liệu > quá 30' chưa gọi > quá hạn gọi lại > nóng > ấm; mỗi khách có lý do + dữ kiện + tin Zalo soạn sẵn từ dữ liệu thật, chỉ soạn không gửi | Xếp theo độ GẤP, có LÝ DO; tin nhắn chỉ dùng dữ kiện có thật | **Đã làm** `crm.brief`: quá hạn gọi lại > chưa ai gọi > hẹn hôm nay > nóng > sắp nguội; tin soạn sẵn, không bịa "hôm trước mình có trao đổi" khi chưa từng liên hệ. Thiếu: "vừa mở tài liệu" (chưa có link theo dõi) |
| **Bản tin hôm nay** (`brief.py`): gọi trước, lịch gặp hôm nay/mai, tuần tới, đề xuất chờ duyệt, lead kẹt | Gom một màn đầu ngày theo vai | Một phần: thẻ buổi sáng + khách sắp đóng tiền. Còn: lịch hẹn (chưa có bảng hẹn), phiếu chờ duyệt đã có ở hộp duyệt |
| **Hồ sơ xuyên chặng** (`cases.py`): chặng → đội giữ → mục (bắt buộc / riêng tư / luật kiểm / chỉ người xác nhận); chuyển chặng do code; trả về đúng mục | Mục riêng tư: đội khác thấy trạng thái, không thấy số; model không tự khai "đạt" | **Đã làm** (module `flow`, quyết định 0043) nhưng KHÁC: mẫu là dữ liệu có phiên bản; mục lấy từ nguồn thật qua bộ kiểm module ngành đăng ký, chỉ mục chưa có nguồn do người xác nhận (đúng vai / đội khai trong mẫu). Trả về ⇒ lùi đúng chặng + việc bổ sung. Ẩn số theo quyền, không theo cờ riêng. Còn: tải giấy tờ lên hồ sơ |
| **Chi tiết lead + hành trình** (`journey`, blocker): mốc từ dữ liệu thật; "đang kẹt / ai làm / từ khi nào" luật đầu tiên khớp thắng; độ nóng | Hành trình = mốc thật; blocker là câu trả lời đầu tiên | **Đã làm** `crm.journey`: 8 mốc (lead về → gọi → nói chuyện được → quan tâm → giữ chỗ → cọc → ký → đóng tiền), blocker, dòng thời gian gộp chuyển chặng + chăm sóc + giao dịch. Cải tiến so với bds: lịch sử chuyển chặng CHỈ THÊM (bds ghi mốc từ cột hiện tại) |
| **Tổng quan toàn công ty** (`reports.py`): phễu, theo dự án, kênh, cây tổ chức | Phễu có % từng bước | Một phần ở marketing (`mkt.overview`); phễu toàn công ty + theo cây tổ chức: còn |
| **Điều đáng chú ý** (`insights.py`, 7 luật) | Mỗi luật có ngưỡng + ai chịu | Chưa; ghi backlog |
| **Lead sắp rơi + cứu lead** (`rescue.py`): người nghỉ / không ai giữ / chưa gọi → chia lại cho người ít lead nhất, qua duyệt | Tự phát hiện + đề xuất | Chưa; cần chia lead (lõi TMS) |
| **Chiến dịch + budget watch** (`ads_watch.py`): −30% nếu CPL ≥ 1,4× nền hoặc ≥ 40% lead rác; +20% nếu CPL ≤ 0,75× trung vị | Ngưỡng đề xuất đổi ngân sách | Gợi ý tăng ngân sách theo ROAS đã có; tự đề xuất đổi ngân sách: chưa |
| **Gửi tài liệu có link theo dõi** (`doc_share.py`, `share_public.py`): token băm, trang công khai CSP chặt, mở = sự kiện, bot xem trước không tính | Kiến trúc cổng công khai an toàn | Chưa (cần trang công khai, làm kỹ bảo mật) — doc 171 của Phê Nâu cũng có |
| **Lịch của bạn** (`agenda.py`) | Một lịch gộp hẹn gặp + hạn gọi + hạn chặng | Chưa (chưa có bảng hẹn) |
| **Căn phù hợp** (`unit.match`): lọc cứng + điểm | khách đầu tiên đã có `catalog.match_units` | Giữ |
| **CardSpec** (kpis / chart / table / list / callout / text + nút gọi action) | Hợp đồng thẻ khối | **Đã làm** (`kernel/ui.py`: kpis, steps, stages, callout, list, text, table; khối lạ bị bỏ qua) |

## Lỗi của bds đã tránh (rà thấy)
- Mac: `CaseBody` so trạng thái "confirmed/ok" trong khi server gửi "verified/received" ⇒ mục đạt hiện vòng xám. Khách đầu tiên: trạng thái một nguồn.
- Mac chỉ ĐỌC hồ sơ / lịch sử; sửa chỉ có trên web. Khách đầu tiên: nút thao tác nằm ngay trên thẻ (lệnh qua executor).
- Timeline của khách không gộp sự kiện hồ sơ + tin nhắn. Khách đầu tiên: gộp chuyển chặng + chăm sóc + giao dịch.
- `OVERLOAD = 25` khai mà không dùng.

## Hợp đồng thẻ khối
Xem chú thích đầu `backend/src/barista/kernel/ui.py` và `macos/Sources/BaristaDesk/Cards.swift`. Chỉ được THÊM trường; `--check-contract` giờ
giải mã cả trang MỞ TỪ dòng / thẻ (hành trình, hồ sơ giao dịch…), không chỉ màn chính.
