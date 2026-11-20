# 14 · Diễn tập tắt khẩn trợ lý (nút tắt phải từng được bấm thật)

Vì sao: 0143, 0144 mục 6 — một nút chưa từng bấm là lời
hứa, không phải biện pháp. Chạy trên BẢN DEMO sau mỗi bản phát hành có đổi cách trợ lý được tự làm (lần đầu: bản rolling có migration 055 / 056).
Kết quả ghi thành `docs/runbooks/dien-tap-tat-khan-<ngày>.md` theo mẫu diễn tập khôi phục 08/11 (người diễn tập, giờ Việt Nam, số đo, lệnh đã chạy).

## Ba nút, hiệu lực phải TỨC THÌ (không bộ đệm — chính sách đọc mỗi lần chạy)

| Nút | Lệnh | Ai bấm | Cần duyệt? | Kiểm |
|---|---|---|---|---|
| Tắt cả nhóm công cụ | `admin.set_agent_group` (`group=<module>.*`, `mode=deny`) | quản trị | không (siết) | trợ lý mất công cụ nhóm đó ngay lượt chat kế; lệnh đã nới trong nhóm cũng không chạy |
| Gỡ nới một lệnh | `admin.set_agent_policy` (`mode=""` về trần, hoặc `propose`) | quản trị | không (siết) | lượt kế: lệnh thành đề xuất; `core.agent_autoruns` không tăng |
| Tắt công tắc công ty | `admin.set_agent_trust` (`autorun_low_risk=false`) | quản trị | không (siết) | mọi lệnh dưới công tắc về đề xuất; lệnh đã nới riêng KHÔNG bị ảnh hưởng (đúng thiết kế — gỡ riêng) |

Bật lại bất kỳ nút nào = nới ⇒ phiếu cho chủ tài khoản (chuỗi `access_change`). Diễn tập phải thử cả chiều bật lại để thấy phiếu xuất hiện.

## Kịch bản (≈ 15 phút, dữ liệu mẫu)

1. Chuẩn bị: công ty demo đang ở preset Toàn quyền (công tắc bật); một người (sale) có việc / lead của mình. Ghi `SELECT * FROM core.agent_trust` và
   `SELECT command, mode FROM core.agent_policies` làm mốc.
2. Chứng minh đang tự làm: trong chat có người, nhờ trợ lý "dời hạn việc X sang ngày mai" ⇒ kết quả `ok` ngay, thẻ có nút Hoàn tác, nhật ký `via=agent`,
   `ref=autorun:<id việc>`; `core.agent_autoruns` +1.
3. **Nút 1** — tắt nhóm `flow.*`: lặp lại câu ở bước 2 ⇒ trợ lý không có công cụ dời hạn (câu trả lời nói không làm được, không có thẻ ghi); giờ bấm → giờ hiệu
   lực ghi ra (kỳ vọng: cùng phút).
4. Mở lại nhóm (`mode=""`) — không cần duyệt vì về trạng thái "theo quyền người hỏi"; ghi chú: nếu công ty từng siết nhóm trước đó thì đây là nới và cần duyệt.
5. **Nút 2** — gỡ nới một lệnh đã nới riêng (nếu demo có): câu ở bước 2 ⇒ thẻ đề xuất, `why` ghi "trần code: propose".
6. **Nút 3** — tắt công tắc: câu ở bước 2 với lệnh chưa nới riêng ⇒ thẻ đề xuất; `admin.agent_policies` không còn dòng "Công tắc công ty".
7. Bật lại công tắc ⇒ phiếu chờ chủ tài khoản; chủ tài khoản duyệt ⇒ bước 2 lại `ok`.
8. Hoàn tác: bấm Hoàn tác trên thẻ của bước 2 ⇒ việc về hạn cũ; sửa việc bằng tay rồi bấm Hoàn tác thẻ cũ ⇒ bị từ chối "đã được sửa sau đó".
9. Vòng quét tự hạ: giả lập 2 lần người sửa lại việc trợ lý vừa làm (hoặc đặt `max_errors_7d=1` tạm) ⇒ chạy `admin.review_agent_policies` bằng quyền hệ
   thống (`scripts/` hoặc chờ vòng quét) ⇒ dòng `propose` cho đúng lệnh + thông báo chủ tài khoản. Trả ngưỡng về cũ.

## Số đo phải ghi

| Số đo | Kỳ vọng |
|---|---|
| Thời gian từ bấm nút tới lượt chat kế bị chặn | ≤ 1 lượt (không có bộ đệm chính sách) |
| Lệnh ghi lọt qua sau khi tắt | 0 (đếm `core.audit_log` `via=agent status=ok` sau mốc) |
| Phiếu duyệt khi bật lại | đúng 1 phiếu / nút, người duyệt = chủ tài khoản |
| Hoàn tác đúng / từ chối đúng | 1 / 1 |

Lỗi gặp khi diễn tập ⇒ sửa code + thêm test trước khi ghi "đạt"; không ghi "đạt có ghi chú".
