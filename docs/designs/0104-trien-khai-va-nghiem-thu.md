# ADR 0104 · Mã triển khai và nghiệm thu local

**Người triển khai/đánh giá:** Codex (OpenAI). **Ngày:** 06/11/2026, giờ Việt Nam.
**Phạm vi cho phép:** người dùng yêu cầu viết code theo hướng và các mặc định đã đề xuất. Không phát hành production, không nhập dữ liệu khách đầu tiên thật vào hệ thống chạy.
**Trạng thái (Claude (Anthropic), 06/11/2026):** đã commit trên `main`, chưa push, chưa phát hành. Phần 1–3 dưới đây giữ nguyên bản của Codex tại lúc viết; đợt sửa sau ở §5.

## 1. Đã triển khai

| Phần | Mã/schema thực tế | Hành vi |
|---|---|---|
| Identity tối thiểu | `modules/parties.py`, schema `parties`, capability `directory` | Cá nhân/tổ chức có UUID độc lập login; lookup đúng tenant, người ngoài không tạo user giả. Finance cần directory; seed cài dependency closure. Xem ADR 0105. |
| Nghĩa vụ và ứng | `modules/acct/settlements.py`, `acct.payables` | Ghi nhận gross/khấu trừ có căn cứ, nghĩa vụ ròng, ứng và khoản cần thu hồi; mỗi nguồn có business ref/payload; cùng key khác dữ liệu lỗi. |
| Thực chi | `acct.payouts`, `payout_allocations`, `payment_requests` | Một nghĩa vụ nhiều lần chi; reserve dưới khoá; approval cho bên ngoài; snapshot instruction; chờ chuyển, đã chuyển, huỷ và đang tra soát tách rõ. |
| Cấn/đảo/hoàn | `advance_offsets`, `payable_adjustments`, `recovery_receipts` | Cấn ứng theo nguồn, đảo phần cấn có lịch sử, thu hồi không tự tăng ngân hàng; nhận hoàn gắn sao kê và không ghi cash lần hai. |
| Nhiều người hưởng | `modules/commission/allocations.py`, `commission.payees` | Basis giá căn/phí/pool/tiền cố định; budget riêng; pool đóng mới cần tổng 100%; chia dư làm tròn ổn định. Version phân bổ chặn ghi đè dữ liệu đã đổi; đã mở thì không sửa thẳng quyền. |
| Điều kiện mở | `modules/commission/releases.py`, `rules.py`, `release_decisions`, `release_confirmations` | Chính sách snapshot, đợt mở luỹ kế; ALL/ANY theo phí đã thu, lịch khách thanh toán, ngày, xác nhận manual có căn cứ. Có sweep ngày và phản ứng khi khách trả; đọc trạng thái nguồn sau khi khoá, không lấy event cũ làm sự thật cuối. Giảm rồi mở lại dùng decision mới, không ghi trùng target cũ. |
| Màn ví và báo cáo | Giữ `commission.wallet`, `wallets`, `requests`, `accruals` | KPI đọc nghĩa vụ/ứng mới; lịch sử phân biệt ghi nhận và thực chuyển; phiếu hiển thị trạng thái chi từ acct. RLS người hưởng chỉ lộ phần của mình. Dự phóng tính cả người ngoài và nghĩa vụ đã ghi nhận chưa lập chi. |
| Khóa bảo vệ | RLS + unique/FK tenant + triggers + transaction locks | Chặn sửa facts đã ghi, sửa số tiền/bên nhận payout, phân bổ/cấn sai bên hoặc pháp nhân; hai lệnh chi không cùng lấy một phần nghĩa vụ. Không UPDATE số dư tiền làm nguồn thật. |

`acct.py` và `commission.py` được chuyển thành package theo ADR 0088; entry ở `__init__.py`, thao tác vẫn giữ module key. Ledger vẫn là cửa ghi kép duy nhất ở runtime. Không tạo `commission.payouts` hoặc bộ sổ thứ hai.

## 2. Migration và dữ liệu đã có

- **016_parties.sql:** identity từ memberships và chủ ví cũ; một login ở hai tenant có hai party khác nhau. Người đã rời membership không tự được cấp lại login.
- **017_acct_payables.sql:** tạo facts/settlement mới; chuyển số dư ví **theo từng pháp nhân** sang phải trả hoặc phải thu ứng bằng bút toán phân loại một lần. Bảo toàn postings cũ, tiền ngân hàng, chi phí và clearing. Không đoán khoản đã chi thuộc từng giao dịch nếu lịch sử chưa đủ quan hệ đó.
- **018_commission_payees.sql:** một payee gốc từ accrual cũ, policy snapshot và số đã mở lịch sử; thêm version phân bổ, decisions và manual confirmations. Số lịch sử đã mở không được phát lại thành chi phí mới.

017/018 có marker maintenance trong `migrations/deployment.json`: trước phát hành phải dừng writer cũ, backup và diễn tập restore; không rolling code cũ ghi ví song song code mới. Những migration này **chỉ chạy trên các DB test/contract riêng ở local trong phiên này**; chưa chạy DB app dev đang làm việc hoặc production. Không sửa baseline.

Kiểm upgrade: ghi nhận cũ 18 triệu, ngân hàng đã chuyển 10, clearing còn 4, ví phải trả còn 4; pháp nhân thứ hai có ứng 6 triệu. Sau upgrade: cash/expense/clearing không đổi, ví cũ về 0, phải trả 4 và ứng 6 giữ đúng pháp nhân; chạy lại migration không thêm tiền. Test cũng giữ nguyên ID/nội dung postings cũ.

## 3. Bằng chứng kiểm

- Backend: `uv run ruff check src tests`, `uv run pyright`, `uv run pytest -q`.
- Bộ đầy đủ: **594 test**; `test_commission_payees.py` có **16 test mới**, thêm rehearsal có tiền thật trong DB test ở `test_upgrade.py`. Đây là fixtures giả trên Postgres thật, không phải giao dịch thực của khách.
- Test mới gồm nhiều người/mẫu số, privacy, người ngoài có approval, chi từng phần, idempotency/payload conflict, cạnh tranh reserve, pending/unknown, ứng/cấn, gross/net/thuế fixture, huỷ sau chi và hoàn 3/10 triệu, rollback sau khi ghi journal, tenant/pháp nhân, pool 100.001 đồng và các đợt mở giảm–mở lại.
- App Mac: `swift build` và `swift run BaristaDesk --check-contract http://127.0.0.1:18304` trên DB riêng `barista_erp_0104_contract`, dữ liệu giả. Kết quả trên mã hoàn tất: **Hợp đồng khớp**, các vai mẫu đều giải mã được màn/form. Server kiểm riêng đã dừng sau kiểm; DB giả giữ riêng để có thể kiểm lại.

Các ca business C01–C20 trong [hợp đồng](0104-hop-dong-commission-acct.md) là bộ nghiệm thu thiết kế; không quy đổi máy móc 20 hàng thành 20 test đã kiểm riêng biệt. Test xanh chưa thay thế pilot nghiệp vụ và kiểm kế toán.

## 4. Giới hạn còn giữ rõ

1. **Thuế thật:** acct nhận số tiền/khấu trừ và snapshot căn cứ do nguồn cung cấp; chưa có bộ tự xác định toàn bộ VAT/TNCN cho mọi loại cá nhân/tổ chức. Fixture giữ 2 triệu trên gross 20 triệu không khẳng định một thuế suất hợp pháp. Điều chỉnh huỷ nghĩa vụ đã có thuế bị chặn để kế toán xác nhận phương án, thay vì tự đảo sai thuế.
2. **Huỷ quyền hưởng lịch sử trước 0104:** số dư ví được chuyển theo bên nhận/pháp nhân, không bịa phân bổ lịch sử từng khoản. Nếu huỷ quyền đã mở từ trước mà chưa đủ attribution, consumer dừng với lỗi cần đối chiếu đầu kỳ, không đánh dấu tài chính đã xử lý xong. Lệnh ứng lịch sử không đủ bằng chứng cũng được chặn khi yêu cầu huỷ; lệnh rút thường có metadata đầu kỳ được xử lý riêng.
3. **MISA:** dữ liệu mẫu đã có ánh xạ GIẢ ĐỊNH cho `payable` 3388, `advance` 141, `recovery` 1388, `tax:withheld` 3335, `revenue:advance_charge` 515, `unapplied:bank` 3388; `seed --upgrade` thêm phần thiếu, không đè phần kế toán đã sửa. Kế toán vẫn phải chốt, kể cả profile theo loại bên nhận / pháp nhân. Màn xuất vẫn báo `????` khi thiếu; tên đối tượng lấy từ account snapshot.
4. **Chuyển TMS/Sheet:** mới nạp thử định danh + hồ sơ của một dự án trên DB riêng (0106 §6); giao dịch và tiền chưa chạy. Cần source identity, mapping, cutoff, bằng chứng file và đối soát trước pilot. Chưa chuyển toàn bộ khách CRM sang party; chưa làm payroll, bảo hiểm, CCCD/merge engine hay đổi object storage.
5. **Ngân hàng:** xác nhận thủ công có mã giao dịch/ngày/quyền là nguồn vận hành hiện tại; không có lệnh gửi ngân hàng tự động. Unknown giữ reservation đến khi kế toán ghi kết quả tra soát rồi xác nhận hoặc huỷ; huỷ tự động từ nguồn vẫn dừng, không retry chi mù.

Những điểm trên là phạm vi thực tế của bản code, không được ghi ADR hoặc màn sản phẩm như tính năng đã có đầy đủ. Bước tiếp là rehearsal với mẫu nghiệp vụ đã ẩn thông tin và chốt policy/mapping kế toán trước phát hành.

## 5. Đợt sửa sau khi kiểm (Claude (Anthropic), 06/11/2026)

**Trước phát hành:** migration 017, 018 là maintenance (dừng writer cũ, backup, diễn tập restore); 019–022 chỉ thêm. `deploy` chỉ chạy `seed` không `--upgrade`, nên 020 tự tạo chuỗi `settlement_payment`, quyền đọc danh mục cho giám đốc, quyền dùng gói `directory`. Ánh xạ MISA mẫu và gói `hr` cho bản đang chạy cần `seed --upgrade` / bật gói.

| Lỗi | Cách sửa | Giữ bằng (`tests/`) |
|---|---|---|
| Lệnh "cần tra soát" kẹt | Kế toán ghi kết quả tra soát (bắt buộc) thì huỷ được; `void_payables` vẫn dừng; mig 019 thay trigger, thêm `status_note` | `test_settlement_fixes` |
| Trưởng nhóm thấy ví cả đội | `wallets` quyết bằng phạm vi `ledger`; số dư qua `acct.owner_positions` khi all, còn lại chỉ chính mình; bỏ `self_identity` | như trên |
| MISA 244/363 dòng `????` | `seed.ACCOUNT_MAP`, `ensure_account_map` chỉ thêm; thêm cả `unapplied:bank` (162 dòng còn lại sau 5 tiền tố đã nêu) | như trên |
| `accruals` mất bậc, doanh số, ngày về | Khôi phục cho dòng chính; tỷ lệ chốt tay không nói bậc; `earned_at` chỉ đặt khi chuyển | như trên |
| Màn phải trả, form thiếu nhãn | Tên qua `parties.names`, pháp nhân, còn lại, nhãn Việt; `field()` mọi Input mới; màn thừa hưởng `min_scope` của truy vấn | như trên |
| Đề nghị chi lỗi sau phát hành | Mig 020 | `test_upgrade` |
| Test trượt theo thứ tự | `fresh_sale` riêng từng test tiền; đọc chính sách đang hiệu lực; chạy xuôi và ngược đều xanh | cả bộ |
| Bên ngừng còn số dư | `resolve_for_payment` trả cả bên đã ngừng kèm `active`; chỉ `set_payees` chặn người MỚI; khoá thành viên = ngừng, không cờ thứ hai | `test_settlement_fixes` |
| `_left` từng khoản | Một công thức `_balances`; 5.000 nghĩa vụ: vị thế 575 → 63 ms, `_unsettled` 552 → 73 ms, dòng tiền 582 → 69 ms (máy dev) | đo tay |

**Thêm ngoài danh sách:** thành viên mới chưa có party nên không chọn được trong form (mig 021).

**Giới hạn giao diện:** "Chỉ xem trước tổng" báo ở dòng thông báo lỗi của form vì form server mô tả chưa có khối xem trước; không ghi gì, form giữ nguyên để sửa. Sau khi chốt có thẻ Phân bổ. **Chưa có:** thành phần ngoài hoa hồng, hoá đơn theo đợt, lệnh nhập lịch sử (0106 §3, §6).

**Bổ sung 06/11, Claude (Anthropic):** ví báo có số dư nhưng rút lỗi "sai pháp nhân" khi số dư ở pháp nhân khác mặc định (đã tái hiện) ⇒ phiếu rút ghi pháp nhân trả tiền, xem 0099. Việc hoa hồng mới ghi vào pháp nhân mặc định là quyết định của 0099, không phải lỗi; đổi khi khách đầu tiên trả lời G19.
