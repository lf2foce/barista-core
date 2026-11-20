# Runbook 16 · Nạp dữ liệu từ hệ cũ (migrate) — làm sao để dữ liệu cũ không "sống dậy"

Người soạn: Claude (Anthropic), 12/11/2026. Quyết định nền: 0185. Đọc trước MỌI lần nạp dữ liệu hệ cũ
vào một bản cài, kể cả nạp thử cho demo.

## Rủi ro (đã xảy ra thật, 12/11/2026)

Barista có các vòng nền chạy mỗi phút lo "việc hôm nay": hạn gọi lead, thu hồi / chia lại lead, nhắc nợ quá hạn, hồ sơ kẹt chặng, việc trợ lý theo dõi. Dữ liệu
nhiều năm tuổi nạp vào như dữ liệu thường thì các vòng này coi là **vừa tới hạn hôm nay** và làm việc thật trên đó. Số đo trên một bản demo (số gộp, không có dữ liệu
cá nhân):

| Thứ xảy ra | Số đo | Vì sao |
|---|---|---|
| Thông báo trong app | ~47.700 trong 12 phút | 3.376 lead "quá hạn chăm sóc lại" × ~11 trưởng nhóm, mỗi lead một tin; cộng 7.345 tin hàng chờ |
| Lead đổi người giữ | ~900 | vòng quét thu hồi và chia lại lead cũ theo đồng hồ hiện tại |
| Lượt gọi model của trợ lý | 575 trong 6 phút | 128 khoản phải thu cũ quá hạn ⇒ 128 việc nhắc nợ ⇒ trợ lý chạy trên từng việc |
| CPU máy chủ API ~90%, trang báo cáo 2–7 giây | 3.764 việc theo dõi đang mở | mỗi giao dịch cũ mở một việc "thu phí" / hồ sơ; bộ giữ nhịp kiểm từng việc. Sau khi bản sửa đã lên, CPU vẫn cao cho tới khi đóng các việc này |

Lần đó không có gì ra ngoài app (chưa nối email / Zalo / SMS, chưa có thiết bị nhận thông báo đẩy). **Trên bản cài đã nối kênh ngoài, cùng lỗi này sẽ gửi tin thật
cho nhân viên và khách.** Sửa bằng cách tăng CPU là sai hướng: máy không thiếu, hệ thống đang làm việc không nên làm.

## Quy tắc

1. **Dữ liệu hệ cũ là tham chiếu.** Không nhắc việc, không báo trưởng nhóm, không thu hồi / chia lại, không mở việc theo dõi, không lượt trợ lý.
2. **Mọi lời gọi ghi của lớp nạp truyền `historical=True`:** `imports.run_import(p, lệnh, dòng, dry_run=False, historical=True)` và
   `executor.run(p, lệnh, dữ_liệu, historical=True)`. Cờ chỉ bật được từ mã trong tiến trình; không có ở API hay màn hình. Thiếu cờ ở MỘT script là đủ gây sự cố.
3. **Không bao giờ đưa lên bản cài một DB chưa qua bước kiểm im lặng** (dưới). Không dump từ DB dev đã có máy chủ chạy trên đó: vòng quét của máy chủ dev đã làm bẩn nó.
4. **Module / gói mới có vòng quét hoặc bên nghe sự kiện** phải tự hỏi: "chạy trên bản ghi 5 năm tuổi thì nó làm gì?" — và thêm trường hợp vào
   `backend/tests/test_historical_load.py` (test này chạy mọi vòng quét đã đăng ký sau một chuỗi lệnh nạp lịch sử).

## Các bước mỗi lần nạp

1. **Nạp vào DB riêng, không có máy chủ nào chạy trên đó** (không `uvicorn`, không vòng quét). Ghi lại giờ bắt đầu.
2. **Chạy lớp nạp** với `historical=True` (quy tắc 2).
3. **Kiểm dấu trên dòng** — mỗi câu phải trả 0:
   ```sql
   SELECT count(*) FROM core.notifications WHERE created_at > :luc_bat_dau;
   SELECT count(*) FROM leadflow.lead_state WHERE imported_at IS NULL;          -- chỉ đúng khi DB chưa có lead sống nào
   SELECT count(*) FROM crm.leads WHERE loaded_at IS NULL;                      -- như trên
   SELECT count(*) FROM flow.cases WHERE NOT historical;                        -- như trên
   SELECT count(*) FROM acct.receivables WHERE followed_at IS NULL AND status IN ('open', 'partial');
   SELECT count(*) FROM work.items WHERE status IN ('active', 'waiting');       -- việc theo dõi đang mở của dữ liệu nạp
   ```
   Bản cài đã có dữ liệu sống thì so với số trước khi nạp thay vì với 0.
4. **Kiểm im lặng trên một BẢN SAO** (lần kiểm tự ghi vào DB nếu dữ liệu chưa im; không đặt khoá model):
   ```bash
   createdb -T <db_vừa_nạp> <db_kiểm>
   cd backend && BARISTA_DATABASE_URL=…/<db_kiểm> BARISTA_OWNER_DATABASE_URL=…/<db_kiểm> uv run python scripts/check_quiet_after_load.py
   ```
   Script chạy mọi vòng quét + giao hết sự kiện 3 lượt, so thông báo / `sla_events` / việc theo dõi / lượt gọi model / sự kiện / bút toán / người giữ lead. Phải in
   `IM`. In `KHÔNG IM` ⇒ tìm vòng quét gây ra, sửa ở gốc (lõi), nạp lại — không lên bản cài.
5. **Đưa lên bản cài** theo runbook 11 (đổi DB trong lượt bảo trì; cổng mật khẩu trả 401 trước khi có dữ liệu thật).
6. **Sau khi lên, theo dõi 10 phút:** số thông báo, `leadflow.sla_events`, `work.items` đang mở, `core.ai_usage` không tăng; `docker stats` của API về mức nghỉ.

## DB đã nạp TRƯỚC 0185 (không có dấu)

Nạp lại là cách sạch nhất. Không nạp lại được thì đánh dấu tay **khi chưa có máy chủ chạy trên DB đó**, rồi làm bước 4:

```sql
UPDATE crm.leads SET loaded_at = created_at WHERE loaded_at IS NULL;
UPDATE flow.cases SET historical = true WHERE NOT historical;
UPDATE acct.receivables SET followed_at = now() WHERE followed_at IS NULL AND status IN ('open', 'partial');
UPDATE leadflow.lead_state SET imported_at = now() WHERE imported_at IS NULL;
UPDATE work.items SET status = 'cancelled', last_note = 'Dữ liệu nạp từ hệ cũ: tham chiếu, không theo dõi (0185)' WHERE status IN ('active', 'waiting');
```

Các câu này coi MỌI dòng là dữ liệu nạp — chỉ đúng với DB chưa vận hành thật.

## Dời, khôi phục hoặc sao DB đã nạp (kể cả ngày migrate thật)

Mỗi dòng dưới đây là một lỗi đã gặp ngày 11–12/11/2026. Làm theo thứ tự.

| Bước | Vì sao (chuyện đã xảy ra) |
|---|---|
| Tạo DB đích do đúng vai chủ sở hữu: `createdb -O barista_owner <tên>`; `pg_restore -j 2 --no-owner --role=barista_owner` | DB tạo bằng tài khoản quản trị rồi restore bằng `barista_owner` ⇒ không tạo được schema, 6.027 lỗi, DB rỗng mà lệnh vẫn "xong" |
| `REVOKE TEMP ON DATABASE <tên> FROM PUBLIC, barista_app` | quyền mặc định của Postgres; tài khoản ứng dụng không được tạo bảng tạm |
| **Đóng việc theo dõi đang mở của dữ liệu nạp** (câu `UPDATE work.items … 'cancelled'` ở mục trên) rồi đếm lại phải bằng 0 | bộ giữ nhịp kiểm TỪNG việc đang mở: 3.764 việc ⇒ API ~90% CPU, báo cáo chậm, kể cả khi vòng quét đã im. Vòng quét im KHÔNG có nghĩa là việc theo dõi im — hai cơ chế khác nhau |
| Đếm đối chiếu sau khi restore: số lead, phiếu chi phí, lịch, pháp nhân, tổng sổ cái = 0, `leadflow.lead_state` chưa đánh dấu = 0 | restore lỗi quyền vẫn trả về như thành công; chỉ đếm mới thấy |
| Chạy `scripts/check_quiet_after_load.py` trên một BẢN SAO trước khi cho bất kỳ máy chủ nào trỏ vào | một máy chủ dev trỏ nhầm vào DB nạp đã làm bẩn nó sau 12 phút (63.000 thông báo, đổi người giữ lead) |
| Trên bản cài: tắt hẳn dịch vụ (`scale 0`, chờ hết container), ngắt kết nối, đổi tên DB cũ thành `…_cu_<ngày>` (KHÔNG xoá), rồi mới đổi tên DB mới vào | giữ đường lui; DB cũ là bằng chứng khi đối soát |
| Trước khi có dữ liệu thật: gọi không mật khẩu vào `/`, `/login`, `/api/auth/dev-logins` phải ra 401 | bản cài từng hở 3 phút khi lên dữ liệu thật |
| Sau khi lên 10 phút: thông báo, `sla_events`, việc đang mở, lượt gọi model không tăng; CPU về mức nghỉ | bản sửa đã lên mà CPU vẫn cao là dấu hiệu còn việc theo dõi mở |

**Trước khi XOÁ một DB (dọn ổ đĩa, dựng lại):** 0 kết nối không có nghĩa là không ai cần. Hỏi các phiên / người đang làm việc trước; `pg_dump -Fc` ra file rồi mới `dropdb`
(bản nạp 1,3 GB nén còn ~40 MB). Ngày 12/11 một DB nạp đang được phiên khác dùng đã bị xoá khi dọn ổ; cứu được chỉ vì còn bản dump chụp trước đó.

**Bản dump phải ghi rõ nó là gì:** tên file mang ngày giờ chụp và trạng thái (`…-asof-<ngày>-<giờ>` = nguyên trạng; `…-prepared-…` = đã migrate + đánh dấu lịch sử).
Dữ liệu thật của khách chỉ ở máy / VPS của chủ sở hữu, trong `docs/clients/<khách>/import/` (không vào git).

## Rủi ro còn mở (chưa chặn — kiểm tay mỗi lần nạp)

| Vòng nền | Làm gì trên dữ liệu nạp | Mức |
|---|---|---|
| `commission.refresh_releases` | mở đợt chi hoa hồng theo ngày cho giao dịch cũ — **tiền**, không thông báo | cao khi nạp giao dịch có hoa hồng chưa chi; kiểm số dòng đợt chi trước / sau |
| `flow.remind_due` | nhắc việc tới hạn của hồ sơ nạp (một tin gộp / người / ngày) | thấp |
| `books.open_due` | mở bù kỳ kế toán khi kỳ bắt đầu đặt lùi | thấp |
| Phiếu duyệt của lệnh nạp bị đặt luật duyệt | báo người duyệt (đúng thiết kế: người phải bấm) | biết trước |
| Vòng quét của gói ngoài lõi | tuỳ gói — test của gói phải có trường hợp nạp lịch sử | kiểm khi bật gói mới |

## Ngày chuyển giao (cut-over) — chưa xây

Lead nạp đang "đỗ": vòng quét bỏ qua cho tới khi có người giao tay. Khi khách chốt ngày chuyển hẳn sang Barista cần một lệnh **kích hoạt hàng loạt**: chọn tập lead
nào thành lead sống, đồng hồ tính từ lúc kích hoạt, chia theo luật hay giữ người cũ. Kích hoạt 8.000 lead cùng lúc mà không thiết kế là lặp lại đúng sự cố trên (lần
này là thông báo đúng luật nhưng vẫn không ai đọc nổi) — phải kích hoạt theo đợt, có bản gộp. Việc theo dõi công nợ cũng vậy: khoản nào bắt đầu nhắc từ ngày chuyển
do kế toán trưởng của khách chọn.
