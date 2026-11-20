# Hoa hồng bốn nhịp: được hưởng → đối chiếu với bên trả → đã trả → còn lại — ghi chú thiết kế (bản thảo, CHƯA là ADR, chưa có code)

> Người soạn: Claude (Anthropic), phiên "báo cáo quản trị", 11/11/2026, theo yêu cầu của firstcc-01. **Số đo** lấy từ bản sao sheet kế toán của khách trên máy (tổng hợp, không tên người). **Nhận định và đề xuất tách riêng, chưa chốt.** Chưa có trả lời của khách cho Q-B53 / Q-B54 / Q-B55; phần "định nghĩa từ sheet" là suy ra từ quan hệ giữa các cột, không phải khách xác nhận.

## 1. Định nghĩa các nhịp, suy ra từ sheet

Sheet giữ hai tầng số trên cùng một giao dịch (1.680 dòng):

**Tầng khoản phí của bên trả (CĐT) — theo giao dịch**
- Phí được hưởng → **tiền về đã đối chiếu** 355,99 tỷ (bên trả đã ký đối chiếu số phí) → **bên trả đã thanh toán** 331,80 tỷ → còn lại 59,62 tỷ; riêng **chưa đối chiếu** 35,42 tỷ.
- 45 giao dịch đã đối chiếu mà bên trả chưa trả đồng nào. ⇒ **đối chiếu đi TRƯỚC tiền về**: bên trả chốt số phải trả, rồi mới chuyển.

**Tầng người hưởng (sale, quản lý…) — theo người × giao dịch**
- Được hưởng 161,96 tỷ (sale) → đã đối chiếu 160,06 tỷ → đã trả 133,09 tỷ → còn lại.
- **"Còn lại" = được hưởng − đã trả, đúng 1.680/1.680 dòng** (không phụ thuộc cột đối chiếu). Cột "đối chiếu" của người hưởng chỉ là phần của họ trong số phí đã đối chiếu, để theo dõi, không quyết định số còn lại.
- **Trả người hưởng chỉ sau khi tiền về:** trong 1.126 dòng đã trả cho sale, chỉ 3 dòng (0,15 tỷ) trả khi bên trả chưa trả đồng nào — gần như toàn bộ chi sau tiền thật, khớp cách Barista đang làm (`commission.payees.earned_vnd` nhả theo phí thực nhận).

## 2. "Còn lại" âm: 517 dòng, −27,33 tỷ

- 517/1.680 dòng sale có còn lại âm (đã trả hơn được hưởng). **336 dòng trong đó có "được hưởng" = 0** nhưng vẫn có tiền đã trả (−26,34 tỷ, 96% giá trị âm); 465/517 dòng không có hình thức / trạng thái giao dịch. Cộng cả thưởng chỉ giải thích 210 dòng, −0,95 tỷ.
- Tổng "còn lại" thuần 28,87 tỷ = **56,20 tỷ dương (700 dòng) − 27,33 tỷ âm**. Số dương 56,20 tỷ khớp sổ HHMG của kế toán (còn lại 55,52 tỷ, lệch 1,2%) — có vẻ sổ kế toán chỉ cộng phần dương và bỏ phần âm khi tổng hợp (suy ra từ hai con số khớp nhau, chưa được khách xác nhận).
- **Nhận định:** các dòng âm là khoản đã chi mà sheet không ghi khoản được hưởng (giao dịch cũ chi trước khi có công thức hoặc chuyển từ hệ cũ); Barista có `legacy_payouts` cho đúng loại lịch sử này. Chờ Q-B53 để kết luận (trả vượt thật, thu hồi, hay thiếu dòng được hưởng).

## 3. Nó thuộc về đâu: khoản phí của bên trả, không phải người hưởng

Đối chiếu là sự kiện giữa công ty và BÊN TRẢ phí, theo giao dịch (một biên bản đối chiếu nói số phí cho các giao dịch). Phần "đã đối chiếu" của từng người hưởng chỉ là **tỷ lệ của họ × số phí đã đối chiếu**, tính ra được, không phải dữ liệu riêng.

**Đề xuất (chưa chốt):**
1. **Nhịp đối chiếu trên khoản phải thu phí (acct, theo giao dịch)**: một bảng nhỏ ghi từng lần đối chiếu: khoản phải thu, số tiền bên trả đã chốt, ngày, mã biên bản / tham chiếu, người ghi — chỉ thêm, huỷ bằng bản ghi đảo; có lệnh nhập (importable) để nạp biên bản từ file. Số "đã đối chiếu" = tổng các lần; "chưa đối chiếu" = phí − đã đối chiếu.
2. **Nhịp theo người hưởng là TÍNH RA**, không lưu: được hưởng (`gross_vnd`) · đã đối chiếu = được hưởng × (đã đối chiếu / phí) · đã trả (từ lệnh chi + lịch sử) · còn lại = được hưởng − đã trả, **hiện cả số âm có nhãn "đã chi vượt / chưa có khoản được hưởng", không cắt về 0, không giấu khi cộng**. Thưởng là dải riêng (cùng bốn nhịp).
3. **Không đổi điều kiện nhả hoa hồng:** nhả theo phí thực nhận như hiện nay. Nếu khách muốn nhả theo đối chiếu: là một lựa chọn trong luật nhả (dữ liệu của chính sách — `earn_on` hiện có `contract_signed` / `fee_received`, thêm `fee_reconciled`), mặc định giữ `fee_received`; chỉ làm khi khách xác nhận (Q-B53).

**Dùng ở đâu:** (a) dự báo tiền về ("đã đối chiếu, chờ chuyển" 59,62 tỷ là dòng tiền chắc chắn hơn phí chưa đối chiếu 35,42 tỷ); (b) màn "hoa hồng của tôi": được hưởng — sắp về — đã trả; (c) đối soát kế toán thay cho cột sheet. Dùng chung cho mọi ngành trả thù lao theo kết quả có bên thứ ba xác nhận (bảo hiểm, nhượng quyền, tuyển sinh có đối soát với trường): không có chữ bất động sản.

## 4. Quản lý và vai khác (Q-B54)
Quản lý có cùng bốn nhịp nhưng chỉ 403/1.680 giao dịch có hoa hồng (49 người), đã trả 2,58 / 5,93 tỷ = 44% so với 82% của sale: hoặc quy tắc trả sau sale, hoặc chưa chi. Nếu là quy tắc ⇒ cần "điều kiện nhả theo vai" trong mẫu chia (cùng chỗ với chuỗi vai của nhánh cơ chế); chờ Q-B54.

## 5. Chưa chốt / cần khách
- Q-B53: nghĩa chính xác cột đối chiếu / tiến độ; nguồn gốc 517 dòng âm. Nếu "đối chiếu" thực ra là biên bản đối chiếu trả hoa hồng của sàn với người hưởng (không phải với CĐT) thì nhịp này thuộc người hưởng chứ không thuộc khoản phí — đây là điểm có thể làm đổi thiết kế mục 3.
- Q-B54: quy tắc trả quản lý. Q-B55: khoản lệch 2,6 tỷ giữa sổ HHMG và sheet giao dịch.
