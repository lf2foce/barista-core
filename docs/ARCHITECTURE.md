# Barista — kiến trúc lõi

Lõi vận hành doanh nghiệp theo GÓI: lõi (một cửa lệnh, quyền ép ở DB, sổ cái, outbox) + gói năng lực dùng chung + gói ngành; cấu hình từng
công ty là dữ liệu. Tài liệu này mô tả ranh giới và hợp đồng kỹ thuật của lõi; bảng module bên dưới gồm cả module các gói trong repo.
Phần riêng của khách đầu tiên (dòng dữ liệu môi giới BĐS, đối chiếu nhu cầu, rủi ro pháp lý, giả định chờ xác nhận):
tài liệu riêng của khách (`barista-docs/clients/`).

## 1. Quyết định chính

| # | Quyết định | Vì sao | Cái giá |
|---|---|---|---|
| 1 | **Modular monolith**: một process FastAPI + một Postgres, mỗi module một schema | Giữ căn + tạo giao dịch + gắn khách + ghi sổ phải nằm trong MỘT giao dịch DB; tách dịch vụ lúc này chỉ thêm lỗi phân tán | Phải tự giữ ranh giới (test kiểm SQL) |
| 2 | **Một cửa lệnh** (registry + executor) | Quyền, kiểm dữ liệu, khoá chống lặp, luật duyệt, nhật ký, sự kiện — làm một lần cho mọi cửa vào (app, Excel, sự kiện, agent) | Handler phải viết theo khuôn `(ctx, inp)` |
| 3 | **Quyền = dữ liệu, ép ở DB (RLS)** | Lệnh quên lọc vẫn không lộ; quản trị đổi quyền không cần lập trình | Policy phải đơn giản (hàm `core.can_see`) để không chậm |
| 4 | **Sổ cái ghi kép** cho mọi đồng tiền | "Đang nợ sale bao nhiêu, vì sao" luôn trả lời được; không có số dư bị ghi đè | Kế toán phải nghĩ theo Nợ / Có |
| 5 | **Outbox sự kiện** cùng giao dịch | Ký HĐ ⇒ hoa hồng + hoá đơn tự chạy, lỗi thì thử lại, không mất, không chạy đôi | Có độ trễ ~1 giây |
| 6 | **Chuỗi duyệt + luật duyệt là dữ liệu** | Thêm "huỷ giao dịch phải qua giám đốc" bằng một dòng cấu hình | — |
| 7 | **Giao diện do server mô tả** | Thêm màn / đổi menu / đổi quyền không phải phát hành lại app | Màn đặc thù (hồ sơ khách 360°) vẫn cần vẽ riêng |

## 2. Module

| Schema | Phân hệ | Giữ gì | Phát / nghe sự kiện |
|---|---|---|---|
| `core` | lõi + Quản trị | công ty, người, vai trò, **ma trận quyền**, cây tổ chức, nhật ký, outbox, phiếu / chuỗi / luật duyệt | — |
| `ledger` | lõi | tài khoản, bút toán, dòng (chỉ thêm) | — |
| `crm` | Kinh doanh (khách) | khách (1 SĐT = 1 khách), lead (khách × dự án), chăm sóc | phát `crm.lead_created`; nhận kết quả bán của gói ngành qua điểm cắm `crm.lead_outcome` |
| `acct` | Kế toán | khoản phải thu DÙNG CHUNG mọi gói (0057, 0079), dòng sao kê + phiếu thu (phân bổ của dòng), lệnh chi, chi trả thu hộ, ánh xạ MISA; bất biến tiền ở Sức khoẻ dữ liệu + vòng quét (0086) | phát `acct.payment_received`, `acct.receivable_overdue`, `acct.payout_transferred`, `acct.remittance_changed` |
| `mkt` | Marketing | chiến dịch, chi phí theo ngày | — (đếm lead qua `crm.lead_stats`, chỉ số đếm) |
| `leadflow` | Chia lead & hạn xử lý (gói crm — 0110) | luật chia, trạng thái hạn gọi / thu hồi, hàng chờ (0034) | vòng quét `enforce_sla` |
| `flow` | Quy trình & việc (nền; package — `cases` · `tasks` · `requests` · `plans` · `screens`, 0088) | mẫu quy trình, hồ sơ theo chặng, việc (0043) | nghe `subject.created / subject.changed` |
| `work` | Việc theo dõi nhiều ngày (gói nền; loại việc do gói khai — 0076) | việc trợ lý theo dõi, trạng thái (DBOS chỉ giữ nhịp — 0021, 0026) | nghe `acct.*` và sự kiện của gói ngành đang bật |
| `asks` | Yêu cầu gửi bên ngoài (gói nền, 0064 / 0075) | link một lần để bên ngoài trả lời / nộp file; nhắc hạn | phát `asks.item_answered` |
| `kb` · `papers` · `talk` | Tài liệu · Giấy tờ · Trao đổi | kho tài liệu có hiệu lực / trích nguyên văn; giấy tờ nháp → ban hành; trao đổi gắn hồ sơ | — |
| `training` | Đào tạo nội bộ (0061) | khoá học từ tài liệu, câu hỏi, bài giao, điểm | — |
| `books` | Sổ sách & nghĩa vụ (0063) | thiết lập + dữ kiện khách, kỳ tháng, nghĩa vụ + hạn nộp theo bộ quy định có phiên bản (`regulations/`, 0067), nhật ký nộp (chỉ thêm), yêu cầu chứng từ qua link (0064) | vòng quét `open_due` |
| `apps` | App tự ghép (nền) | app và dashboard là DỮ LIỆU: trang, khối, tập dữ liệu có chiều / chỉ số; mở bằng quyền người xem (0098, 0100, 0175, 0220) | — |
| `connect` | Ứng dụng kết nối (nền) | app từ xa (OAuth, quyền ≤ một vai), đăng nhập ứng dụng first-party, cửa nhà vận hành (0140, 0205–0207) — hệ ngoài GỌI VÀO Barista | — |
| `connectors` | Kết nối ngoài (nền) | khung kết nối chung — Barista GỌI RA hệ ngoài: kết nối của công ty, khoá mã hoá, lịch kéo có con trỏ, thử lại từng mục, cửa webhook chung, nhật ký đồng bộ; gói kết nối viết bộ nối + lệnh nhận (0244, runbook 22) | gọi lệnh nhận `<gói>.connector_<bộ nối>` của gói |
| `findings` | Báo cáo chạy song song | gom phát hiện của các module qua điểm cắm `<module>.findings_report` (0230) | — |
| `hr` | Hồ sơ làm việc | hồ sơ làm việc của nhân sự, chứng chỉ / giấy phép hành nghề (0202) | — |
| `kpi` | KPI & xếp hạng | bảng xếp hạng theo chỉ số do module khai (`Module.metric`) | — |
| `meet` | Gọi video | cuộc gọi gắn vào đối tượng của gói bất kỳ | — |
| `memory` | Trí nhớ (nền) | điều trợ lý nhớ có bằng chứng; phạm vi người / đối tượng / công ty | — |
| `mgmt` | Báo cáo quản trị | báo cáo quản trị theo kỳ, bản chụp có nguồn gốc số (0213) | — |
| `parties` | Cá nhân & tổ chức (gói `directory`) | danh bạ người / tổ chức, liên hệ, sự đồng ý (0111) | — |
| `pay` | Lương & thù lao | kỳ lương, thù lao, bảo hiểm / thuế theo bộ quy định có phiên bản | — |
| `schedule` | Lịch & nguồn lực (luôn bật — 0157) | MỘT lịch chung: sự kiện, nguồn lực, lịch hẹn; người tham dự ≠ nguồn lực (0158) | — |
| `timekeeping` | Chấm công | chấm công, ngày công, múi giờ công ty | — |
| `word` | Văn bản Word | mở / sửa đơn giản / xuất lại file Word ngay trong app (0227) | — |
| — | `admin` · `health` | không bảng riêng: quản trị trên `core`, màn điều hành / sức khoẻ dữ liệu đọc qua hợp đồng các module | phát `admin.member_deactivated` |

Danh sách thật (module → gói) ở `kernel/registry.py` (`MODULES`, `MODULE_PACK`); `tests/test_meta.py` kiểm module nào cũng có schema trùng tên
hoặc không có bảng. Gói ngành riêng là bản phân phối ở repo `barista-packs`, không liệt kê ở đây (0155): ngành — `real_estate` (bất động sản, 0186 / 0191), `commission`
(hoa hồng), `edu` (đào tạo, 0116), `telephony` (tổng đài, 0149), `trade_finance` (kiểm bộ chứng từ L/C, 0072), `hosting` (cho thuê, 0058),
`accounting_service` (dịch vụ kế toán nhiều khách, 0063 — công ty khách qua `barista.api.tenancy`), `equity_research` (phân tích chứng khoán, 0084);
năng lực / kết nối — `meta_ads` (0208), `budget` (0219), `inventory` · `sales_channels` · `shipping` (0217), `compose` (Ấn phẩm: tờ rơi / tờ giới thiệu /
slide / bài đăng từ dữ liệu, vẽ bằng khối `composer` của web — 0224, 0249; rời lõi 21/11/2026, 0254). Catalog, giao dịch và hoa hồng từng là
module của lõi, đã ra gói ngày 12/11/2026 (0191). Dịch vụ lõi chúng dùng bằng quyền hệ thống là HỌ thao tác (`m.consumes`):
`outreach` · `doc_read` · `paper_draft` · `receivable` · `remittance` · `follow_up` (`registry.OPEN_FAMILIES`) cùng các họ khác (`settlement`, `placement`,
`identity`, `ledger_ref`, `company_figures`…). Việc định kỳ: lệnh nội bộ khai `sweep=` (0066).

Gọi chéo: `ctx.call(op)` = quyền người gọi (RLS áp); `ctx.call_system(op)` = chỉ thao tác `internal=True`.
Màn tổng hợp (bảng điều hành của gói) KHÔNG dựng màn của module khác rồi dò ô số theo nhãn: module sở hữu công bố SỐ TÓM TẮT nội bộ cùng định nghĩa với ô số của màn mình (`health.summary`, `leadflow.summary`, `acct.cashflow_projection`, `core.approvals_summary` — api 1.74), bên tổng hợp đọc bằng quyền người xem (`ctx.call_as_optional(op, ctx.p)`: không xem được màn đó ⇒ None). Ô số của màn danh sách tính trên CẢ tập lọc bằng câu tổng; trang dòng ở SQL (`ui.paging()` + `ui.table(paged_total=)`).

## 3. Quyền

`core.grants`: vai trò × tài nguyên → **phạm vi** (`own` của mình · `team` đội mình làm trưởng + cây con · `all` cả công
ty) + **hành động** (`read` `write` `approve` `manage`). Executor đặt biến phiên `app.scopes`, `app.user_id`,
`app.teams`; mọi bảng có MỘT policy `core.can_see(resource, owner, team)` (bảng con theo bảng cha). Role `barista_app`
không BYPASSRLS. Ví hoa hồng là tài khoản sổ cái có chủ ⇒ sale chỉ thấy ví mình — cùng một luật.

Ma trận mặc định (sửa ở Quản trị › Vai trò & quyền): giám đốc xem hết + duyệt; trưởng nhóm `team`; sale `own`; kế toán
tiền + giao dịch `all`, không xem khách; marketing chỉ chiến dịch (số lead là số đếm, không tên / SĐT).

## 4. Đối chiếu nhu cầu khách

Riêng khách đầu tiên — chuyển sang tài liệu riêng của khách (`barista-docs/clients/`) §4 (08/11/2026).

## 5. Nhập liệu

Mỗi lệnh khai `importable=True` tự có: mẫu Excel (nhãn tiếng Việt + trang hướng dẫn), nhập file / dán từ Excel, **chạy
thử** (chạy thật trong điểm lưu rồi bỏ ⇒ báo đúng lỗi lúc nhập thật), nhập thật từng dòng với khoá chống lặp theo nội dung
⇒ nhập lại cùng file không nhân đôi. Tiền nhận "4.500.000.000", "4,5 tỷ", "90tr"; ngày "dd/mm/yyyy"; ô lựa chọn nhận
cả chữ hiển thị ("Quảng cáo" → `ads`).

## 6. Trợ lý (agent) — giao diện chính

Mở app là vào trợ lý (chat + thẻ, ngôn ngữ thiết kế chép từ Barista Desk); bảng / form là màn ghim mở vào khung làm việc
để soát và sửa hàng loạt. Hỏi để biết, thẻ để quyết, bảng để soát.

| Điều kiện | Hiện trạng |
|---|---|
| Agent làm được mọi việc UI làm được, cùng một cửa | ✅ công cụ = mọi thao tác trong sổ đăng ký người hỏi chạy được (`agent/chat.py`); mang theo mỗi lượt theo nhóm — 0095 (đề xuất) |
| Quyền agent ≤ quyền người | ✅ `Principal.as_agent()` — cùng grants, cùng RLS; test duyệt mọi thao tác × vai |
| Rào riêng cho agent, là dữ liệu | ✅ trần trong code (`agent=` của mỗi lệnh: đọc allow, ghi propose) ∩ `core.agent_policies` (công ty chỉ siết) — màn Quản trị › Trợ lý được làm gì |
| Ghi có kiểm soát | ✅ lệnh ghi ⇒ THẺ ĐỀ XUẤT đã chạy thử thật rồi bỏ (đề xuất hiện ra là chạy được); người bấm Xác nhận ⇒ chạy bằng quyền người |
| Duyệt chỉ do người | ✅ `core.decide` cấm agent ở cả trần lẫn handler |
| Số liệu không do model bịa | ✅ thẻ vẽ từ kết quả truy vấn; model chỉ nhận bản rút gọn để tóm tắt |
| Nhật ký phân biệt agent / người | ✅ `core.audit_log.via` |
| Cuộc trò chuyện riêng từng người | ✅ `core.chat_threads` RLS theo người (quản trị cũng không đọc được) |
| Model đổi được | ✅ Pydantic AI, một chuỗi cấu hình `BARISTA_LLM_MODEL` |
| Việc nhiều ngày (theo khách tới khi đóng tiền), agent chủ động theo sự kiện | ✅ 30/10 — `work` + DBOS: sự kiện mở / đánh thức việc, trợ lý của MỘT người xử lý ngoại lệ (0021) |
| Kho tài liệu + RAG theo quyền, giấy tờ, nguồn ngoài (Drive) | ✅ kho theo kệ, giấy tờ NĐ 30, lớp quyền nguồn ngoài (VÀ kệ) — xem `decisions/` 0015–0017 |
| Giọng nói, ghi chú hiện trường | ✅ Siri nổi + giọng nói trực tiếp (0042), nói ghi chú hiện trường (0074 D) |
| Chi phí / thời gian từng lượt, vết hành động | ✅ `core.ai_usage` (token, chi phí, công cụ, thời gian — 133 / 157 / 185); vết lượt ↔ người xác nhận trong nhật ký (0086) |
| Hạn mức chi phí AI theo công ty | ❌ đã đo, chưa chặn (ROADMAP) |
| Eval | ⚠️ `scripts/eval_*.py` chạy tay, chưa chạy tự động trước mỗi lần phát hành |

## 7. Đăng nhập

Đăng nhập Google (OIDC) tại core (0040, runbook 09): email đã xác minh → `auth.load()` đọc
quyền từ DB — danh tính đổi nguồn, quyền không đổi. Đăng nhập dev (chọn vai) CHỈ khi `BARISTA_DEV_AUTH` bật — bản demo đang bật cố ý để
ai cũng xem được (runbook 11 §Chế độ demo); bản cho khách thật phải tắt.

## 8. Rủi ro pháp lý / tích hợp (cần luật sư / đối tác xác nhận)

Riêng khách đầu tiên — chuyển sang tài liệu riêng của khách (`barista-docs/clients/`) §8 (08/11/2026).

## 9. Giả định chờ khách xác nhận

Riêng khách đầu tiên — chuyển sang tài liệu riêng của khách (`barista-docs/clients/`) §9 (08/11/2026).

## 10. Nguồn tham khảo

- Hiệu năng RLS (bọc hàm, tránh join trong policy): https://supabase.com/docs/guides/database/postgres/row-level-security-performance
- Gỡ RLS chậm thực tế: https://engineering.myhoai.com/posts/debugging-postgres-performance-under-row-level-security/
- Sổ cái ghi kép trong Postgres: https://pgrs.net/2025/06/17/double-entry-ledgers-missing-primitive-in-modern-software/ ·
  https://github.com/slaurmagan/pg_ledger/wiki
- Ranh giới dữ liệu modular monolith, schema-per-module: https://milanjovanovic.tech/blog/how-to-keep-your-data-boundaries-intact-in-a-modular-monolith ·
  https://milanjovanovic.tech/blog/schema-per-module-vs-database-per-module
