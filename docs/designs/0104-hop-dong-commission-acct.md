# 0104 · Hợp đồng commission ↔ acct và bộ ca nghiệm thu

**Người thiết kế/đánh giá:** Codex (OpenAI). **Ngày:** 06/11/2026.
**Trạng thái:** hợp đồng thiết kế đi kèm ADR 0104. Đã có mã local; xem [phạm vi/kiểm thử](0104-trien-khai-va-nghiem-thu.md). Tên thao tác logic cần đối chiếu ghi chú đó, không coi toàn bộ mục tiêu thiết kế là chức năng đã hoàn tất.

## 1. Phạm vi triển khai đầu tiên

Một bộ phân bổ nhiều bên hưởng; tỷ lệ trên giá căn, trên phí/pool hoặc tiền cố định; nhiều đợt mở; bên ngoài không login; thu nhiều đợt và chi một phần; ứng/cấn; điều chỉnh/huỷ. Giữ một ledger và một đường thực chi ở acct. Bộ ca là dữ liệu giả, không phải số tiền hay chính sách khách đã chốt.

Chưa làm: CCCD/merge engine toàn diện, generic rule editor, object storage mới, kê khai thuế/hóa đơn điện tử, ngân hàng tự chuyển, tự động nhận webhook không có đối soát. Thao tác xác nhận chuyển phải giữ bằng chứng và quyền kế toán; gọi API nội bộ không đồng nghĩa đã gửi ngân hàng.

## 2. Định danh và ranh giới capability tối thiểu

**Đề xuất chủ dữ liệu:** capability độc lập `parties` với một identity tenant-scoped và login link tuỳ chọn. Chốt thành ADR nền tảng riêng trước migration của capability này; không cần thêm đầy đủ contacts/identifiers/merge ngay.

Identity tối thiểu: id UUID, tenant, kind person/organization, display_name, trạng thái active/inactive, nguồn tạo, các mốc audit. Login link có tenant, party_id, user_id và quyền đại diện riêng nếu có; không suy quyền đại diện tổ chức chỉ từ một liên kết. Không mặc định email/SĐT là unique identity hoặc tự gộp theo tên. CRM và acct giữ vai trò/ngữ cảnh của mình, tham chiếu party qua hợp đồng.

- `parties.resolve_for_payment` (đề xuất internal): chỉ callers đã khai và chỉ reference trong nghiệp vụ được phép; trả id/kind/name/status/version tối thiểu. Không trả CCCD, danh sách liên hệ hoặc tài khoản ngân hàng.
- `parties.self_identity` (đề xuất ban đầu): đã bỏ 06/11/2026, màn "của tôi" đọc theo login nên không ai cần.
- Tạo/sửa party là command có quyền qua executor, không cấp quyền tạo danh mục vô hạn cho mọi call_system. Finance không bắt buộc sales/CRM.
- Recipient/bank instruction cho khoản chi do acct quản lý với snapshot/version được duyệt; không tự thay các lệnh đã duyệt khi hồ sơ danh mục đổi.
- Người ngoài được kế toán có quyền xử lý và tra theo party; không tạo tài khoản đăng nhập giả. Trưởng nhóm thấy dữ liệu theo grant, không vì cùng tên hoặc cùng user_id.
- Không FK/join SQL chéo từ commission sang parties/acct. Contract kiểm cùng tenant, active tại lúc tạo và projection tối thiểu; identity đã dùng không hard-delete, sửa tên không sửa snapshot chứng từ cũ.

**File/bằng chứng:** dùng reference do module chủ chứng từ công bố, kiểm quyền đối tượng trước khi xem/tải. Các nghiệp vụ tiền lưu ref + loại/mục đích + người xác nhận; không nhét bytes hoặc URL public vào bảng payee. Nếu cơ chế hiện có chưa cung cấp contract cần thiết, thêm adapter hẹp trước ca đó; storage migration không là prerequisite mặc định. Invoice, minh chứng thu/chi và xác nhận người nhận có vòng đời riêng, không một invoice_no trên toàn payee.

## 3. Chủ dữ liệu và grain

| Chủ | Đối tượng logic đề xuất | Grain / khóa nghiệp vụ |
|---|---|---|
| Commission | Bộ phân bổ (nâng accrual model hoặc root mới, chốt trong migration design) | Tenant + nghiệp vụ nguồn + thành phần phí/thưởng + revision |
| Commission | Payee | Bộ phân bổ + bên nhận + vai/thành phần; một bên có thể nhiều vai có căn cứ |
| Commission | Release decision | Payee + release component + action version; ghi target luỹ kế, delta, policy và căn cứ |
| Commission | Adjustment | Tham chiếu bản chốt gốc, lý do, người duyệt, revision; không sửa tiền đã chốt |
| Acct | Payable | Tenant + source owner + release decision ref; bên nhận, pháp nhân, tiền tệ, gross/net/tax và bút toán |
| Acct | Advance | Một khoản ứng được duyệt, bên nhận/pháp nhân, tài khoản và các lần cấn/hoàn |
| Acct | Payout/reservation/allocation | Một payout có nhiều allocation; mỗi allocation xác định payable/advance và số tiền; một payable nhiều payout |
| Acct | Tax component / evidence links | Thành phần thuế/cấn/chứng từ gắn quyết định và kỳ; không dùng generic deduction để che thiếu nghĩa vụ |

Budget của bộ phân bổ, nghĩa vụ, thuế, ứng và chuyển ngân hàng là các thước đo khác nhau. Không cộng mọi “dòng đợt” legacy thành một con số.

Tối thiểu constraint: NOT NULL/kiểu/miền tiền/trạng thái, unique tenant + business key; FK nội module giữ tenant; currency/pháp nhân khi allocation; mọi bảng được bảo vệ RLS. Aggregate cap/reservation kiểm dưới khoá; invariant không chỉ nằm trong UI.

## 4. Hợp đồng thao tác đề xuất

### 4.1 Tiền phí và điều kiện mở

Giữ `acct.issue_receivable` cho phí công ty; không lập lại doanh thu lúc chia người hưởng. Commission đọc facts nguồn theo ID, cùng tenant/pháp nhân qua contract hẹp; cần paid luỹ kế, amount/basis tương ứng, status và version. Dữ liệu khách trả thuộc deals/nguồn tương ứng, không tự gọi khoản khách phải trả là phí công ty.

Sự kiện payment/đảo/huỷ chỉ đánh thức; consumer đọc trạng thái mới nhất, khoá bộ phân bổ và so mục tiêu với quyết định đã ghi. Event version cũ không làm giảm/mở lần hai theo payload cũ.

### 4.2 `acct.recognize_payable` — đề xuất mới

- **Caller ban đầu:** commission; nguồn giả cho tests qua registry khai rõ. Khi có module dùng thật thứ hai thì bổ sung contract cùng test; không wildcard.
- **Input:** source/ref/version, party_id, entity_id, currency=VND, gross_vnd, net_vnd, tax components có basis/version/evidence, accounting_profile, effective_on, basis_hash. Tenant/principal lấy từ executor, không tin input tenant.
- **Preconditions:** source thuộc module gọi (`ctx.owned_by`); party/pháp nhân hợp lệ; caller đã kiểm và chốt quyền hưởng; tổng net + các khoản được phân loại đúng = gross theo profile; không account tùy ý. Căn cứ thuế chưa đủ thì không ghi nhận phần yêu cầu phân loại đó, chuyển ngoại lệ có tên.
- **Effect:** tạo payable cùng bút toán ledger trong transaction; nếu có tax, tách phải trả ròng và nghĩa vụ thuế theo profile. Không ghi tiền ngân hàng.
- **Return tối thiểu:** payable_id, recognized gross/net, journal_id, version; không danh mục mọi khoản tiền.
- **Retry:** cùng source/ref/version + cùng canonical payload trả đúng bản cũ; khác payload lỗi CONFLICT, không dùng ON CONFLICT DO NOTHING để che khác biệt.

Đường cũ commission._earn_to phải được thay hoặc di chuyển trong cùng đợt nâng cấp; không cùng ghi chi phí/ví và ghi payable cho một số tiền. Nghĩa vụ acct là nguồn tài chính có thẩm quyền của bản ghi mới; commission chỉ giữ projection/ref cho UI và đối soát.

### 4.3 `acct.prepare_payout` — đề xuất mở rộng/thay contract hiện tại

- **Caller:** command của người có quyền hoặc commission sau chuỗi duyệt, qua executor. call_system không miễn kiểm approval/basis.
- **Input:** request/approval ref, recipient instruction version, entity/currency, allocation list (payable/advance ref + amount), idempotency key. Không chỉ user_id + tổng tiền.
- **Preconditions:** một party/pháp nhân/tiền tệ phù hợp; snapshot người nhận khớp duyệt; tổng allocation = số chi; không vượt net còn được chi sau các khoản reserve/cấn. Kiểm lại căn cứ tại lúc thực hiện sau duyệt.
- **Effect:** reserve/đánh dấu phần đã cam kết và ghi phải trả → clearing; payout pending_transfer, không transferred. Hai lệnh đồng thời phải serialise trên nghĩa vụ và các account liên quan.
- **Return:** payout_id, pending_transfer, amount_vnd, allocation refs.
- Nếu ngân hàng đang xử lý/không rõ kết quả: giữ trạng thái ngoại lệ/in_progress và reservation; không tự mở lại khoản đó để chi lần hai.

Hợp đồng cũ acct.create_payout chỉ tạo một record sau khi caller đã ghi clearing, vì vậy không thể gọi thêm prepare_payout mới trên cùng request. Migration phải chuyển toàn bộ caller; không để hai hợp đồng cùng ghi cho một request.

### 4.4 Xác nhận, huỷ, điều chỉnh và ứng

| Thao tác đề xuất / hiện hữu cần nâng cấp | Bên gọi và kiểm soát | Kết quả |
|---|---|---|
| `acct.confirm_payout` (có sẵn, mở rộng) | Kế toán đúng grant; bank ref/ngày/evidence; lock payout; kiểm trạng thái | Ghi clearing/ngân hàng một lần, transferred; emit ref/source/amount/version, không gửi bank/PII vào event |
| `acct.cancel_payout` (mới) | Đúng quyền/duyệt, chỉ khi chắc chưa chuyển; unknown bank state phải tra soát | Đảo clearing, huỷ allocations/reservation có lịch sử; không tăng bank giả |
| `acct.adjust_payable` (mới) | Module chủ nguồn + căn cứ/duyệt revision; lock nghĩa vụ và pending payouts | Điều chỉnh chưa chi; phần đã chi sinh recovery theo quyết định được duyệt, không tự lấy tiền về |
| `acct.create_advance` (mới) | Module nguồn cung cấp hạn mức/version và approval; acct kiểm reserve/exposure trong transaction | Ghi quyền đòi ứng/clearing, lập payout; ngân hàng chỉ giảm khi confirm |
| `acct.apply_advance` (mới) | Đúng party/entity/currency, consent/policy/approval cho cấn | Cấn nghĩa vụ với ứng có allocation, không thêm chi phí hoặc bank movement |
| `acct.settlement_for_source` (mới) | Caller chỉ đọc đúng source mình sở hữu, refs cụ thể, trả fields đã khai | recognized/net/open/reserved/transferred/offset/recovery riêng, không gộp thành một paid |
| `commission.my_entitlements` (mới/projection) | Principal và grants; đọc projection phần mình | Quyền hưởng, đã mở, công nợ, đang chuyển/đã chuyển; không người hưởng khác hoặc PII |

Các event kết quả chỉ cho module nguồn cập nhật projection/đánh thức việc. Projection không có quyền ghi sổ hoặc tự phát sinh lần chi mới.

### 4.5 Transaction, idempotency và lỗi

- Xác nhận release decision + recognize payable đồng bộ trong transaction executor. Lỗi ở nửa sau rollback cả decision/payable/journal/outbox.
- Flow duyệt nhiều ngày không giữ transaction mở; lúc chạy lại nạp quyền/căn cứ mới, so basis version, revalidate under lock. Draft/approval không phải tiền đã ghi.
- Lock theo thứ tự ổn định: bộ phân bổ khi tính budget; nghĩa vụ/advance/recipient nếu cần và account theo ID chuẩn khi settlement. Rà cả thứ tự ledger/handler hiện có trước code để không tạo deadlock.
- Quyết định target luỹ kế cuối cùng trừ phần đã được công nhận theo action/version, không dùng timestamp event làm key. Tổng recognized sau adjustment = tổng decisions đã thực hiện.
- Lỗi đề xuất: BASIS_CHANGED, IDEMPOTENCY_CONFLICT, OVER_BUDGET, OVER_RESERVED, RECIPIENT_MISMATCH, CROSS_ENTITY, PERMISSION_DENIED; tên mã cuối cùng khớp framework khi code.
- Không đánh dấu transferred chỉ vì consumer hoàn tất. Manual bank confirmation là bằng chứng vận hành theo quyền; chưa phải tự xác minh độc lập với ngân hàng.
- Gửi ngân hàng thực tế nếu có sau này phải có dispatch identity, trạng thái không rõ và reconcile; phạm vi hiện tại chưa tích hợp gửi.

## 5. Bút toán minh hoạ và các bất biến

**Đây là mô hình vận hành đề xuất và số fixture, không phải tư vấn chuẩn kế toán/thuế hay mức được khách chấp nhận.** Mapping account, thời điểm, pháp nhân và quy định cần kế toán chốt trước dùng thật.

| Hành động | Nợ | Có |
|---|---|---|
| Mở quyền/ghi nghĩa vụ 10 triệu, không thuế fixture | Chi phí 10 | Phải trả 10 |
| Duyệt lập chi 4 triệu | Phải trả 4 | Clearing 4 |
| Xác nhận ngân hàng chuyển | Clearing 4 | Bank 4 |
| Ứng 6 triệu, chưa mở quyền, không phí fixture | Phải thu ứng 6 | Clearing 6; khi chuyển: Nợ clearing/Có bank 6 |
| Mở quyền 10 triệu rồi cấn ứng 6 | Chi phí 10 / phải trả 10 như trên; sau đó Nợ phải trả 6 | Có phải thu ứng 6; còn phải trả 4 |
| Huỷ pending payout 4, chắc chưa chuyển | Clearing 4 | Phải trả 4; không chạm bank |
| Huỷ quyền đã chuyển 10, có căn cứ thu hồi đủ | Phải thu hồi 10 | Điều chỉnh giảm chi phí 10 theo profile được duyệt; bank chưa thay đổi |
| Nhận hoàn 3 trên khoản thu hồi 10 | Bank 3 | Phải thu hồi 3; còn đòi 7 |

Đơn vị bảng: triệu VND. Nếu thuế đã giữ/nộp hoặc người nhận không còn nghĩa vụ hoàn theo hợp đồng thì không áp ca huỷ đơn giản; cần adjustment/tax/recovery riêng.

Invariants cần query kiểm chứng:

- Mỗi journal cân bằng trong đúng tenant/pháp nhân; journal đã ghi không UPDATE/DELETE.
- Quyền hưởng/budget và nghĩa vụ không cùng một số: quyền chưa mở được báo riêng; nghĩa vụ chỉ từ decisions đã ghi nhận.
- Net liability còn lại = net recognized sau điều chỉnh − cấn đã hoàn tất − amounts đã chuyển sang clearing. Cash transferred chỉ từ payout đã xác nhận, có điều chỉnh/hoàn riêng.
- Clearing = tổng phần payout đang cam kết chưa chuyển/huỷ; thuế, phí ứng và recovery có account/nguồn riêng.
- Tổng allocations một payout = payout amount, không phân bổ vượt nghĩa vụ/ứng được duyệt; không trộn party/pháp nhân/tiền tệ.
- Không dùng tổng bank transferred làm oracle cho gross/net: thuế, ứng, cấn và hoàn là các chiều riêng.

## 6. Bộ tình huống nghiệm thu

**Đây là yêu cầu nghiệm thu; trạng thái kiểm thực tế nằm trong [ghi chú triển khai](0104-trien-khai-va-nghiem-thu.md).** Tiền bên dưới là VND nguyên đồng; “triệu” chỉ cách viết gọn. Mỗi ca độc lập, trừ bước được nêu trong cùng hàng. Không ca nào xác nhận thuế suất/phí ứng thực tế.

| ID | Input / hành động | Kết quả cần chứng minh |
|---|---|---|
| C01 — tỷ lệ trên giá căn | Giá 2 tỷ; phí công ty 70 triệu (3,5%); A 1,8%, B 0,3%, C 0,2%; budget 46 triệu | Quyền A=36, B=6, C=4 triệu; tổng=46; tổng tỷ lệ=230 bps, hợp lệ; không tạo payee công ty 24 triệu; doanh thu phí vẫn 70 |
| C02 — mở hai đợt | Quyền như C01; policy mở theo tỷ lệ phí thu; thu lần đầu 35/70, sau đó thêm 35 | Mục tiêu lần đầu A=18/B=3/C=2 triệu, tổng 23; đủ phí target A=36/B=6/C=4, delta thêm 23; replay không thêm nghĩa vụ |
| C03 — nhiều lần chi | A đã được ghi nhận 18 triệu; chuẩn bị chi 10, confirm; sau đó chi 8 | Bank giảm 18 tổng; nghĩa vụ chưa thanh toán 0; hai payouts/allocations riêng; trước confirm lần đầu bank vẫn chưa giảm |
| C04 — người ngoài | B là tổ chức không user/login, có identity và instruction hợp lệ; payable 6 triệu | Kế toán lập/confirm chi 6; không tạo user; liên kết login tùy chọn không là điều kiện chi; không dùng giả wallet:user |
| C05 — ứng trước thu | Quyền A=36, chưa thu phí/chưa mở; policy cho ứng 6, phí ứng fixture=0; duyệt/confirm 6; sau đó mở 18 và cấn 6 | Trước mở: bank−6, phải thu ứng=6, chi phí=0; sau mở/cấn: chi phí=18, phải trả còn 12, ứng còn 0; không chi 18 lần nữa |
| C06 — huỷ pending | Payable 10, lập chi 4 rồi huỷ khi xác minh chưa chuyển | Trước huỷ: liability=6, clearing=4, bank delta=0; sau huỷ: liability=10, clearing=0, bank delta=0; allocations có lịch sử |
| C07 — huỷ sau chuyển | Đã ghi nhận và chuyển đủ 10, không thuế; quyết định thu hồi toàn bộ; người nhận hoàn 3 | Lúc quyết định: phải thu hồi=10, không bank tăng; sau hoàn: recovery=7, bank tăng 3; không ghi “đã thu hồi đủ” |
| C08 — pool và rounding | Pool 100.001 đồng, tỷ lệ A/B/C=3333/3333/3334 bps; floor từng phần, dồn dư cho người có bps lớn nhất rồi ID | Floor=33.330/33.330/33.340, dư=1 cho C; cuối=33.330/33.330/33.341; tổng đúng 100.001, retry giữ nguyên |
| C09 — thuế fixture | Gross 20 triệu; hồ sơ giả đã xác minh yêu cầu giữ 2 triệu tại lúc ghi nhận, chưa nộp; net 18 | Chi phí 20 = phải trả ròng 18 + nghĩa vụ thuế 2; confirm net payout chỉ giảm bank 18; tax payable còn 2. Không suy mức thuế thực từ fixture |
| C10 — budget/đồng thời | Budget 46; đã chốt 40; hai lệnh độc lập mỗi lệnh thêm 4 cùng lúc | Chỉ một lệnh chốt, tổng 44; lệnh kia lỗi, không tổng 48; journal/decision không nửa vời |
| C11 — reserve cạnh tranh | Payable net 18, hai request mỗi request chi 12, cùng được xử lý | Chỉ một request reserve, còn available 6; request kia lỗi/stale; một clearing 12, bank chưa giảm |
| C12 — retry/payload | Gọi recognize/prepare/confirm hai lần cùng business action; rồi cùng key đổi tiền/recipient | Cùng payload trả bản cũ không thêm journal; khác payload CONFLICT; confirm đổi bank evidence không âm thầm ghi đè |
| C13 — rollback | Gây lỗi sau decision, trước/sau ghi payable nhưng trước commit | Decision/payable/journal/outbox mới đều rollback; chạy lại tạo đúng một bộ, không repair SQL tay |
| C14 — event ngược/đảo | Nguồn đã thu đủ 70 nhưng event 35 tới sau; sau đó nguồn thật đảo về 35 khi chưa chi | Event cũ không mở sai/thêm tiền; reversal thật hạ target từ 46 xuống 23 bằng adjustment có căn cứ; chặn phần vượt đang chờ chi |
| C15 — quyền | Sale A đọc bộ phân bổ có A/B; A tự duyệt chi; kế toán scope hẹp yêu cầu tổng công ty; ID tenant khác | A chỉ thấy phần A; không thấy tỷ lệ/PII B, không tự duyệt; query toàn công ty bị chặn scope hẹp; tenant khác không lộ tồn tại/dữ liệu |
| C16 — đổi chính sách/căn cứ | Policy v1 chốt A=36; v2 mới A=40; đổi recipient sau approve | V1 vẫn 36; muốn thêm 4 phải revision/adjustment được duyệt; đổi recipient làm basis stale và cần duyệt lại |
| C17 — upgrade | DB cũ có earned 18, đã chuyển 10, pending transfer 4, ví khả dụng 4 | Chuyển số liệu: transferred lịch sử=10, clearing=4, liability còn=4, quyền ghi nhận lịch sử=18; không thêm chi phí 18/bank 10 lần hai; tổng ledger theo pháp nhân không lệch |
| C18 — pháp nhân | A có nghĩa vụ 10 thuộc E1; request chi từ E2 hoặc cấn advance E2 | Từ chối tự cấn/chi chéo; không âm thầm lấy entity mặc định. Nếu cần giao dịch liên pháp nhân, đi nghiệp vụ riêng có bút toán đủ |
| C19 — lỗi/chưa rõ ngân hàng | Payout 4 đã được giao xử lý, chưa rõ chuyển hay chưa; người dùng retry | Không tạo payout mới hoặc giải phóng 4; trạng thái ngoại lệ, kế toán tra soát rồi confirm/cancel có bằng chứng |
| C20 — tổng thật sự trên pool | Policy pool đóng 100, hai dòng chỉ chia 60+30%; policy khác cho giữ lại riêng được định nghĩa | Pool đóng thiếu 10 bị chặn khi chốt; không áp constraint đó cho C01; muốn giữ lại phải khai semantics/amount rõ, không tự thêm công ty làm payee |

C17 là fixture toán học theo số dư các account cũ, không khẳng định migration sẽ đổi mọi lịch sử thành một payout/payable mới. Lập source mapping/manifest và anchor lịch sử để giải thích mà không phát lại lệnh.

## 7. Checklist triển khai theo chặng

| Chặng | Deliverable | Cửa kiểm |
|---|---|---|
| D1 | ADR 0104 + contract + fixture, xác nhận các điểm §10 ADR | Một nghĩa cho mỗi thuật ngữ; còn mở có người/cửa chốt |
| D2 | ADR identity tối thiểu và migration/module nếu chọn parties; test resolver/self identity | Không phụ thuộc BĐS, tenant/RLS, user không bắt buộc, quyền đại diện rõ |
| D3 | Migrations acct: nghĩa vụ/ứng/allocation/recipient; commands và thử bằng nguồn giả | Không gọi sales/catalog/deals bắt buộc; C03–C07/C09/C11–C13/C18/C19 |
| D4 | Commission allocation/release/policy + tích hợp; migrate một người cũ | C01/C02/C08/C10/C14–C17/C20; không ghi song song đường cũ/mới |
| D5 | Projection/app/seed/report/drill/simulation và đối soát | Màn số tiền phân biệt quyền, ghi nhận, ứng, pending, transferred; app contracts |
| D6 | Rehearsal DB cũ + nguồn khách rồi pilot | Mapping/cutoff, không nhân đôi, exceptions có chủ xử lý, recovery/restore đã diễn tập |

Migrations đánh số lúc triển khai, mỗi file khai module, không sửa baseline. Khi có code chạy checks backend theo CLAUDE.md và app contract theo phạm vi. Bản code local đã có kiểm chức năng trên Postgres thật và app contract; phạm vi và kết quả ở ghi chú triển khai, chưa là nghiệm thu production.
