# Runbook 04 · Việc quản trị thường gặp (làm trong app, không cần lập trình)

| Việc | Ở đâu |
|---|---|
| Thêm người, đổi vai / đội, khoá tài khoản | Quản trị › Người dùng |
| Thêm vai trò, đặt quyền (phạm vi + hành động) | Quản trị › Vai trò & quyền — luôn phải còn ít nhất một người quản trị |
| Bắt một lệnh phải qua duyệt (vd huỷ giao dịch qua giám đốc) | Quản trị › Chuỗi & luật duyệt |
| Siết việc trợ lý được làm | Quản trị › Trợ lý được làm gì (chỉ siết được, không nới quá trần) |
| Chính sách hoa hồng mới | Quản trị › Chính sách hoa hồng — thêm bản có ngày hiệu lực, không sửa bản cũ |
| Thông tin công ty trên giấy tờ | Quản trị › Thông tin công ty trên giấy tờ |
| Ánh xạ tài khoản MISA | Kế toán › Ánh xạ tài khoản (mặc định là GIẢ ĐỊNH) |
| Tài liệu cho trợ lý tra | Tài liệu & giấy tờ › Kho tài liệu (chọn đúng kệ = ai được đọc) |

## Nới cho trợ lý tự làm một lệnh (thang tin cậy — 0143)

1. Quản trị › Trợ lý được làm gì: cột "Đề xuất / N ngày", "Duyệt nguyên văn", cột "Nới được" (lệnh khai `promotable`). Ô KPI "Đủ thành tích để nới".
2. Ngưỡng là của công ty: nút **Ngưỡng tin cậy** (mặc định ≥ 20 đề xuất, ≥ 95% duyệt nguyên văn / 30 ngày; tự hạ khi ≥ 3 lỗi / 7 ngày). HẠ ngưỡng
   phải qua chủ tài khoản duyệt (chuỗi đổi quyền); nâng ngưỡng thì không.
3. Dòng đủ thành tích có nút **Nới: tự làm** ⇒ điền trần số lần / ngày (bắt buộc) và trần tiền (bắt buộc nếu lệnh có ô tiền) ⇒ phiếu cho chủ tài khoản.
   Sau duyệt, trợ lý chạy thẳng lệnh đó trong trần; vượt trần trong ngày ⇒ lệnh thành đề xuất như cũ. Siết nhóm công cụ vẫn thắng.
3b. Muốn cả LỚP lệnh rủi ro thấp đảo được làm ngay (0144): nút **Ngưỡng tin cậy** › bật "Lệnh rủi ro thấp đảo được: trợ lý làm ngay, có Hoàn tác" +
   trần số lần / ngày / lệnh ⇒ phiếu cho chủ tài khoản. Tắt lại không cần duyệt. Lệnh bị người sửa / huỷ nhiều sẽ tự bị siết riêng về đề xuất
   (dòng "Công ty siết" trên màn), công tắc vẫn bật cho lệnh khác.
4. Thu hồi: "Đổi" ⇒ chế độ trống (về trần) — không cần duyệt. Tự hạ: vòng quét `admin.review_agent_policies` gỡ nới khi LỖI + số lần NGƯỜI
   SỬA / HUỶ việc trợ lý đã tự làm (cột "Bị sửa / huỷ") đạt ngưỡng trong 7 ngày và báo chủ tài khoản; nới lại như lần đầu.

```sql
-- lệnh đang được nới + bộ đếm hôm nay
SELECT p.command, p.daily_cap, p.money_cap_vnd, p.basis->>'approved_pct' AS pct_luc_dat, a.runs, a.money_vnd
FROM core.agent_policies p LEFT JOIN core.agent_autoruns a ON a.tenant_id = p.tenant_id AND a.command = p.command AND a.day = core.today()
WHERE p.mode = 'allow' AND p.basis IS NOT NULL;
```
