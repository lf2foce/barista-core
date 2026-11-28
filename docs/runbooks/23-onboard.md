# Runbook 23 · Đưa một công ty vào dùng (onboard)

Người soạn: Claude Opus 5.5 (Anthropic), 28/11/2026; bổ sung sau diễn tập trên dữ liệu cỡ thật (mã đích, phiếu duyệt, người chạy theo thực thể, sự kiện,
thông báo): Claude Fable 5.2 (Anthropic), 28/11/2026. Quyết định nền: ADR 0263 (nạp lịch sử: 0185, đầu kỳ / khoá kỳ: 0119, bản cài production: 0194).
Viết cho người làm onboard: đọc một lượt là biết đang ở chặng nào, thiếu gì, bấm gì. Mã trong runbook là dữ liệu GIẢ.

Một công ty chuyển từ hệ cũ sang Barista đi qua **7 chặng**. Mỗi chặng có **đầu ra phải có** — thiếu đầu ra thì chưa sang chặng sau. Trạng thái nằm ở màn
**Quản trị › Đưa vào dùng › Đưa công ty vào dùng** (danh sách việc); số liệu chuyển nằm ở **Quản trị › Đưa vào dùng › Lượt chuyển dữ liệu**.

| Chặng | Làm gì | Đầu ra bắt buộc | Barista lo bằng |
|---|---|---|---|
| 1. Khảo sát theo chuẩn | Cho khách xem quy trình CÓ SẴN của gói, chỉ ghi phần lệch | Bảng fit-gap (chuẩn / cấu hình / phát triển), quy trình vận hành đã ký | Việc `base.fit_gap`, `base.process_signed` |
| 2. Cấu hình | Lệch nào giải được bằng dữ liệu thì cấu hình (vai, quyền, chuỗi duyệt, cài đặt, từ vựng, mẫu quy trình, luật chia lead) | Cấu hình xong, người dùng thật đã mời | Việc `base.configured`, `base.config_imported`, `base.people`; màn Quản trị; gói cấu hình (xuất ở bản thử, nhập ở công ty thật — dưới đây) |
| 3. Chuyển dữ liệu thử | Ít nhất 2 lượt chuyển thử, mỗi lượt đọc đối chiếu | 2 lượt thử ĐẠT (không lệch, hoặc lệch đã ký) | Bộ chuyển (`barista.api.onboard`) + màn Lượt chuyển dữ liệu; việc `base.mock_runs` tự kiểm |
| 4. Kiểm thử chấp nhận | Người dùng thật chạy trọn nghiệp vụ trên dữ liệu đã chuyển | Biên bản kiểm thử | Việc `base.uat` |
| 5. Đào tạo theo vai | Mỗi vai một buổi trên màn của vai | Danh sách người đã học | Việc `base.training` |
| 6. Ngày chuyển | Khoá hệ cũ → lượt cuối → đối chiếu + ký → đầu kỳ → kiểm im → go / no-go → bấm chuyển | Biên bản go / no-go, ngày chuyển ghi trong Barista | `onboard.go_live` (nút "Chuyển sang Barista") |
| 7. Trực sau chuyển | 2–4 tuần theo dõi lỗi + mức dùng | Nhật ký sự cố | Việc `base.hypercare`; màn Sức khoẻ dữ liệu, Báo cáo chạy song song |

Gói đang bật góp thêm việc của gói vào danh sách (vd gói Tài chính: "Ghi số dư đầu kỳ khớp tổng kiểm"; gói Khách hàng: "Kích hoạt lead đỗ theo đợt").

## 0. Tạo công ty thật (một lần, trên máy chủ của bản cài)

Bản cài `production` không nạp dữ liệu mẫu. Tạo công ty + bật gói + cài mặc định + mời quản trị đầu tiên bằng cửa nhà vận hành (runbook 17):

```bash
cd backend
uv run python -m barista.operator create-company --name "Công ty Mẫu" --admin-email quantri@congty.vn --admin-name "Nguyễn Văn Mẫu" \
    --packs crm,finance --by "<người vận hành>" --why "<hợp đồng / lý do>" --dry-run      # xem trước
uv run python -m barista.operator create-company --name "Công ty Mẫu" --admin-email quantri@congty.vn --admin-name "Nguyễn Văn Mẫu" \
    --packs crm,finance --by "<người vận hành>" --why "<hợp đồng / lý do>"
```

- Gói bắt buộc tự kèm (`crm` kéo theo `directory`); gói luôn bật không cần nêu.
- Người được mời thành **chủ tài khoản** (người duy nhất bấm được ngày chuyển, và người duyệt mặc định của chuỗi ký / ngày chuyển). Vai mặc định `admin`;
  giám đốc của khách: `--admin-role director`. Người đó đăng nhập bằng Google với ĐÚNG email này (runbook 09).
- Trợ lý AI mặc định TẮT; bật bằng `--ai` khi khách đã đồng ý.
- Lần trước lỗi giữa chừng (công ty cùng tên chưa có ai) ⇒ chạy lại lệnh, nó làm tiếp. Công ty cùng tên đã có người ⇒ từ chối.
- Tham số chung của cửa nhà vận hành đứng TRƯỚC tên lệnh con (vd `python -m barista.operator --tenant <mã công ty> set-grant …`); `create-company` không
  cần `--tenant`. Lệnh chạy được cả trên bản thử (không chỉ bản `production`).
- Kệ tài liệu không tự tạo: quản trị tạo kệ ở Tài liệu sau khi vào.

Sau đó người làm onboard được mời với vai có quyền **Đưa vào dùng · quản lý** (mặc định: Quản trị hệ thống, Giám đốc). KHÔNG vai mặc định nào chạy được mọi
lệnh của một bộ chuyển có tiền (quản trị không ghi sổ, kế toán không mời người) — và không nên có: mời thêm **người chạy theo vai** (kế toán cho chứng từ tiền,
marketing cho chiến dịch…) và một **người duyệt** khác họ, xem "Người chạy, người duyệt" ở mục 3.

## 1–2. Khảo sát, cấu hình

Làm trong app. Mỗi việc xong ⇒ ở màn Đưa công ty vào dùng bấm **Đã xong** trên dòng đó và ghi **bằng chứng** (đường dẫn biên bản / tệp, số đếm, người rà) — người
khác đọc là kiểm lại được. Việc bắt buộc không bỏ qua được; việc "Nên có" bấm **Bỏ qua** được. Sai thì **Mở lại**.

### Mang cấu hình từ bản thử sang công ty thật (gói cấu hình — ADR 0263 mục C)

Cấu hình làm và duyệt trên bản thử không phải làm tay lại:

1. Ở bản thử: **Quản trị › Cấu hình công ty** › **Tải tệp cấu hình (.json)**. Tệp gồm mỗi phần cấu hình của các gói đang bật một khối và `config_version` (băm
   nội dung — hai công ty cấu hình giống hệt nhau có cùng số). Không có khách, giao dịch, tài khoản người dùng; người duyệt / thành viên ghi bằng email.
2. Ở công ty thật: **Quản trị › Tệp cấu hình đã tải lên** › **Tải tệp cấu hình lên** (tệp sửa tay sau khi xuất bị từ chối — xuất lại). Bấm dòng vừa tải để xem
   **so khác**: thêm / đổi / giữ nguyên / khác nhưng không ghi đè / chờ mời người, theo từng phần. Mục chỉ có ở công ty thật được đếm, không xoá.
3. Email chưa là thành viên công ty thật ⇒ "Cần mời … người trước": mời ở Quản trị › Người dùng (không tự tạo tài khoản), rồi nhập lại cùng tệp.
4. Bấm **Nhập cấu hình này** ⇒ MỘT phiếu chủ tài khoản cho cả tệp (chuỗi "Thay đổi quyền truy cập"; phiếu liệt kê mục thêm / đổi). Sau duyệt, Barista gọi đúng
   các lệnh sẵn có (thêm vai, đặt quyền, chuỗi duyệt, cài đặt, mẫu quy trình, luật chia lead…) theo thứ tự (vai trước quyền, đội trước luật, luật duyệt sau
   cùng); mục lỗi ghi lý do trên thẻ so khác, mục khác vẫn vào. Nhập lại cùng tệp không đổi gì; xong thì dòng hiện "Đã khớp".
5. Đánh dấu việc `base.config_imported` kèm `config_version`.

Phần nhập được: vai, cây đơn vị (trừ đội gốc = chính công ty), quyền, chuỗi duyệt, luật duyệt, cài đặt công ty, từ vựng, trường riêng, mẫu quy trình (khác ⇒
phiên bản mới), luật chia lead (chỉ thêm — luật cùng phạm vi + ngày hiệu lực mà khác thì không ghi đè), thành viên chia lead. **Chưa** mang theo: bậc chia lead
(khoá theo mã luật nội bộ), luật tự động (luật chạy bằng quyền người đặt — làm lại tay để người đặt là người ở công ty thật), thành viên đội / trưởng đội (là
người, không phải cấu hình).

## 3. Viết một bộ chuyển dữ liệu

Bộ chuyển của MỘT khách nằm ở repo riêng của khách (có phiên bản + test), không ở lõi. Nó chỉ làm ba việc: đọc hệ cũ → bản ghi trung tính; ánh xạ bản ghi
→ đầu vào lệnh Barista; khai chỉ số đối chiếu. Mọi thứ khác (chạy lệnh, chống trùng, phát hiện thay đổi, ngoại lệ, đối chiếu, nhật ký) là của lõi.

Quy tắc:
- **Tên bảng / cột của hệ cũ chỉ sống trong `extract()`** (bất biến 11). Bản ghi trung tính dùng tên của Barista, có `source_id` (mã ở hệ cũ, chuỗi).
- **Không tự điền dữ liệu thiếu.** Thiếu / sai ⇒ `raise Problem("bad_source", "…")`; tham chiếu chưa nạp ⇒ `ids.need(...)` tự ném `missing_ref`.
- **Thực thể theo thứ tự phụ thuộc**: người trước, khách sau, giao dịch sau cùng.
- **Mã đích — khai đúng MỘT trong ba** cho mỗi thực thể:
  - `id_key="party_id"`: lệnh tạo trả mã (UUID) ở khoá đó;
  - `target=lambda output, input, ids: …`: hàm lấy mã khi lệnh không trả thẳng — lệnh hành động trên bản ghi đã nạp (mã chính là bản ghi đó, lấy từ
    `input` / `ids`), lệnh lúc trả mã lúc không (trả `None` = dòng này không có mã);
  - `keyless=True`: lệnh chỉ GHI NHẬN (ký, ghi nhận một lần thanh toán, đặt một dòng cấu hình tiên quyết) — sổ mã gốc ghi dòng không mã đích. Thực thể
    khác hỏi "đã nạp chưa" bằng `ids.require("ky_hop_dong", rec["deal_id"])` / `ids.has(…)`; gọi `ids.need` / `ids.get` trên thực thể này là lỗi cấu hình.
  Khai sai (lệnh không trả mã như `id_key` nói, hàm `target` lỗi, tham chiếu thực thể không có trong bộ chuyển) ⇒ `ConverterError`, lượt DỪNG ngay ở dòng đầu
  — sửa bộ chuyển rồi chạy lại; lệnh đã ghi không bị tạo lần hai (khoá chống lặp trả kết quả cũ).
- **Cấu hình tiên quyết** mà bộ chuyển dựa vào (pháp nhân, điều khoản có ngày hiệu lực trong quá khứ…) khai thành thực thể `keyless` đứng đầu bộ chuyển: chạy ở
  chế độ nạp lịch sử như mọi lệnh khác, có sổ, chạy lại không lặp. Không viết bước cấu hình riêng ngoài khung.
- **Cần đọc phía Barista để dựng đầu vào** (vd lệnh xác nhận đòi mã + phiên bản của bản ghi Barista đã lập): `lookup=async (rec, input, reader) -> input` —
  truy vấn bằng quyền người chạy thực thể, chỉ chạy khi lệnh sắp chạy thật, KHÔNG vào dấu vân tay (phía Barista đổi không làm dòng thành "đổi sau khi nạp").
- **Chỉ dùng lệnh có sẵn** của Barista (`create` / `update`). Thiếu lệnh nhập lịch sử cho một loại dữ liệu ⇒ dừng loại đó và ghi rõ là chưa nạp — không ghi
  thẳng bảng, không mượn lệnh "hôm nay" phát lại phí / hoa hồng theo chính sách hiện tại.
- **Chỉ số đối chiếu** tính cùng một đại lượng ở hai phía (đếm, tổng tiền), theo tháng nếu được; phía Barista đọc bằng TRUY VẤN (`reader.query`), không SQL.
  Mỗi thực thể tự có chỉ số đếm `count.<thực thể>` (bản ghi trích ra ↔ bản ghi đã nạp, ngưỡng 0).

Ví dụ ngắn (dữ liệu giả):

```python
from barista.api import onboard as ob

ROWS = [{"source_id": "17", "name": "Người Giả Một", "paid": 1_000_000, "month": "2026-09"}]   # trong thực tế: đọc hệ cũ ở đây


def extract_people():
    for r in ROWS:   # tên cột cũ chỉ ở đây
        yield {"source_id": r["source_id"], "name": (r["name"] or "").strip()}


def person_input(rec, ids):
    if not rec["name"]:
        raise ob.Problem("bad_source", "thiếu tên")
    return {"display_name": rec["name"], "kind": "person"}


async def paid_in_barista(reader):
    # phía Barista: một truy vấn có sẵn trả số theo tháng — ví dụ minh hoạ, chọn truy vấn đúng nghiệp vụ của gói
    return {"2026-09": 1_000_000}


CONVERTER = ob.Converter(
    "hecu",                                   # khoá nguồn: chữ thường_gạch_dưới
    (ob.Entity("person", extract_people, "parties.create", person_input, "party_id",
               update="parties.update", update_input=lambda rec, ids, t: {"party_id": str(t), "display_name": rec["name"]}, label="Người"),),
    metrics=(ob.Metric("money.paid", "Tiền đã thu", lambda: {"2026-09": 1_000_000}, paid_in_barista, by="month", tolerance_pct=0.5),),
    mapping_version="2026-11-28",             # đổi mỗi lần sửa ánh xạ — hiện trên biên bản
)
```

Chạy (trên máy chủ của bản cài, hoặc bản thử) — mã trong tiến trình, không có ở HTTP, trợ lý không có:

```python
import asyncio
from uuid import UUID

import barista.modules  # noqa: F401 — nạp mọi gói
from barista.api import onboard as ob
from barista.config import settings
from barista.db import db

async def main():
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        r = await ob.load(UUID("<mã công ty>"), CONVERTER, by_email="nguoi-lam-onboard@congty.vn", kind="mock",
                          actors={"ke_toan": "ketoan@congty.vn"},          # người chạy của các thực thể khai actor="ke_toan"
                          approver_email="giamdoc@congty.vn")              # người THỨ HAI quyết phiếu duyệt lượt này sinh ra (tuỳ chọn)
        print(r.status, r.created, r.updated, r.skipped, r.exceptions, [x for x in r.mismatches])
        print(r.entities, r.reasons, r.warnings)    # số + giây theo thực thể, ngoại lệ gộp theo (thực thể, lý do), cảnh báo trước lượt
    finally:
        await db.close()

asyncio.run(main())
```

Bộ chạy làm gì với từng bản ghi:

| Tình huống | Kết quả |
|---|---|
| Chưa có trong sổ mã gốc | chạy `create` (`historical=True`), ghi sổ (nguồn, thực thể, `source_id`) → mã Barista + dấu vân tay |
| Có, dấu vân tay (băm ĐẦU VÀO LỆNH sau ánh xạ — cả lệnh tạo lẫn lệnh cập nhật — + `mapping_version`) giữ nguyên | bỏ qua (không gọi lệnh) |
| Có, dấu vân tay đổi (nguồn đổi HOẶC sửa ánh xạ), thực thể khai `update` | chạy `update`, tăng số lần cập nhật, cập nhật dấu |
| Có, dấu vân tay đổi, không khai `update` | ngoại lệ "Nguồn đổi sau khi nạp" — sửa tay hoặc khai `update` |
| Thiếu `source_id` / trùng mã trong cùng bản trích / `Problem` / lệnh từ chối | ngoại lệ ghi lý do + chi tiết, không ghi gì |
| Lệnh phải qua duyệt, lượt có `approver_email` | người duyệt đó quyết phiếu ngay (đúng `core.decide`); lệnh chạy ⇒ ghi sổ như thường |
| Lệnh phải qua duyệt, lượt không có người duyệt (hoặc người đó không có tên ở bước duyệt) | mã phiếu ghi vào sổ phiếu chờ; dòng là ngoại lệ "Lệnh đang chờ duyệt" |
| Lượt sau, phiếu của dòng ĐÃ được duyệt và lệnh đã chạy | GHI NHẬN vào sổ mã gốc (tính là tạo / cập nhật của lượt này) — không chạy lại, không còn là ngoại lệ |
| Lượt sau, phiếu bị từ chối (hoặc duyệt xong mà lệnh không chạy được, lượt không có người duyệt) | ngoại lệ "Lệnh từ chối" kèm lý do; chỉ gửi phiếu mới khi nguồn / ánh xạ của dòng đổi (hoặc đổi `mapping_version`) |
| Lượt sau, phiếu duyệt xong mà lệnh không chạy được, lượt CÓ `approver_email` | gửi phiếu MỚI (lần gửi tăng một), người duyệt quyết ngay — lệnh không chạy được vì thứ tự dữ liệu sẽ chạy khi dữ liệu đã đủ. Lỗi cố định (nguồn sai) vẫn là ngoại lệ "Lệnh từ chối", nhưng mỗi lượt để lại một phiếu "không chạy được" mang tên người duyệt — sửa nguồn trước lượt cuối |
| Đã nạp ở lượt trước nhưng bản trích lần này không còn | ngoại lệ "Không còn ở nguồn" — KHÔNG tự xoá ở Barista, người xử lý tay |

- Chạy lại **cùng nguồn bao nhiêu lần cũng không tạo trùng**: khoá chống lặp theo LẦN THAY ĐỔI — tạo một khoá cố định mỗi mã gốc, cập nhật mang số lần (`v1`, `v2`…). Chết giữa chừng chạy lại an toàn; giá trị đổi rồi quay về (A → B → A) vẫn được cập nhật đúng.
- **Lượt đạt** = không chỉ số lệch và không dòng ngoại lệ, hoặc có người ký chấp nhận — ký khi còn lệch / ngoại lệ phải ghi lý do. Có nhiều nguồn (vd hệ bán hàng + hệ kế toán) thì ngày chuyển đòi lượt cuối đạt của TỪNG nguồn.
- Thay đổi phát hiện bằng **so dấu vân tay trên toàn bộ bản kéo lại**, không dựa vào cột "ngày sửa" của hệ cũ (không tin được).
- Mọi lệnh chạy chế độ nạp lịch sử: không thông báo (kể cả phiếu "xin duyệt" / "tự duyệt" sinh trong lượt), không việc theo dõi, lead nằm đỗ (runbook 16).
  Vẫn chạy **kiểm im** (`scripts/check_quiet_after_load.py`) trên bản sao DB trước khi cho máy chủ trỏ vào — nó đếm cả thông báo sinh TRONG các lượt chuyển
  (phải là 0).
- **Sự kiện giữa các lệnh:** khung tự giao hết sự kiện đang chờ sau mỗi THỰC THỂ (bên nghe lập bản ghi mà thực thể sau cần — không cần máy chủ đang chạy, không
  viết vòng giao riêng). Bản ghi kế tiếp trong CÙNG thực thể cần kết quả của bên nghe ⇒ khai `settle=True` (giao sau mỗi lệnh; chậm hơn một nhịp mỗi dòng).
- **Một chỉ số đối chiếu lỗi khi tính** ⇒ riêng chỉ số đó ghi "Không tính được" (kèm lý do) và chặn như một dòng lệch cho tới khi sửa / ký; lượt vẫn xong.
- **Ngoại lệ đọc đủ:** màn / truy vấn `onboard.exceptions` luôn nêu TỔNG số dòng, lọc theo lý do + thực thể, chia trang ở máy chủ (`limit` / `offset`).

### Người chạy, người duyệt

- **Người chạy lượt** (`by_email`): cần quyền Đưa vào dùng · quản lý; chạy các thực thể không khai `actor`.
- **Người chạy theo thực thể:** bộ chuyển khai `Entity(…, actor="ke_toan")` (khoá do bộ chuyển đặt: `ke_toan`, `marketing`, `kinh_doanh`…); lúc chạy nêu
  `actors={"ke_toan": "<email>"}`. Mỗi nhóm thực thể chạy bằng quyền của đúng người giữ vai đó — nhật ký ghi đúng người. **Không tạo vai "toàn quyền", không
  cấp quyền tạm cho người chạy.** `Metric(…, actor=…)` tương tự cho truy vấn đối chiếu cần quyền riêng.
- **Kiểm trước:** trước khi mở lượt, khung kiểm quyền từng (người chạy, lệnh) và báo MỘT danh sách thiếu (`PermissionError`, chưa ghi gì). Lệnh đang có luật
  duyệt mà lượt không khai người duyệt ⇒ ghi ở `Report.warnings`.
  Kiểm trước ở mức LỆNH (tài nguyên × hành động × phạm vi). Điều kiện lệnh tự kiểm thêm trong thân (vd tạo bản ghi rồi GIAO cho người khác đòi quyền quản lý)
  chỉ lộ khi chạy — thành ngoại lệ "Lệnh từ chối" của từng dòng; chạy thử trên bản thử trước và giao thực thể đó cho vai có quyền quản lý.
- **Đổi người chạy giữa chừng không tạo trùng:** khoá chống lặp thuộc lượt nạp của công ty (nguồn × thực thể × mã gốc × lần thay đổi), không thuộc người.
- **Lệnh công ty bắt duyệt** (chuỗi duyệt là dữ liệu của công ty — không bỏ luật trong lúc nạp):
  - cách 1 — `approver_email="<người thứ hai>"` (hoặc danh sách, khi chuỗi có nhiều bước: một người không quyết hai bước của một phiếu): người đó phải là
    thành viên thật, có tên ở bước duyệt theo chuỗi, và KHÔNG là người chạy nào của lượt (người xin không tự duyệt — bất biến 6). Khung quyết các phiếu của
    lượt bằng đúng lệnh duyệt của lõi: phiếu + nhật ký ghi tên người duyệt, lệnh đích chạy bằng quyền người xin. Không có quyền hệ thống, không cửa sau;
  - cách 2 — không khai: phiếu nằm ở hộp duyệt, người duyệt bấm trong app (không có thông báo — báo họ ngoài app); lượt chạy lại sau đó ghi nhận các phiếu
    đã duyệt.
  Lối quyết phiếu trong tiến trình chỉ có ở lớp nạp (`executor.decide_in_load`): không có ở HTTP, trợ lý không dùng được.

### Chứng từ tiền không có lệnh sửa

Chứng từ tiền (phiếu thu, đợt phí, lần đã chi…) chỉ THÊM và ĐẢO — đúng thiết kế, không có lệnh `update`. Hệ quả cho lượt chuyển:
- **Lượt chuyển THẬT kéo lại TOÀN BỘ từ nguồn vào một công ty MỚI** (trống). Các lượt thử chạy trên bản thử / bản sao; lợi ích "chỉ nạp phần đổi" là của dữ liệu
  có lệnh sửa (người, danh mục, lịch) và của việc chạy lại trong buổi chuyển.
- Chạy lại trên công ty đã nạp mà nguồn sửa một chứng từ tiền ⇒ ngoại lệ **"Nguồn đổi sau khi đã nạp"** (`changed_after_load`). Đó là việc của NGƯỜI: kế toán
  xem chứng từ ở Barista và ghi bút toán đảo / điều chỉnh bằng lệnh nghiệp vụ; khung không tự sửa tiền.

## 3 (tiếp). Đọc và ký đối chiếu

Màn **Lượt chuyển dữ liệu**: mỗi lượt một dòng (thử / cuối, số tạo / cập nhật / giữ nguyên / ngoại lệ, số chỉ số lệch, đã ký chưa). Mở một lượt ⇒ **biên bản đối
chiếu**: từng chỉ số × kỳ (hệ cũ, Barista, chênh, ngưỡng, khớp / lệch), ngoại lệ theo lý do, nút **Xem các dòng ngoại lệ**.

- Lệch ⇒ tìm nguyên nhân (ánh xạ sai, dữ liệu nguồn sai, thiếu lệnh) → sửa → chạy lượt mới. Lệch có lý do chấp nhận được (hệ cũ ghi trùng…) ⇒ **Ký biên bản
  đối chiếu** kèm ghi chú.
- Ký đi qua chuỗi duyệt **"Ký biên bản đối chiếu dữ liệu chuyển"** (`onboard_signoff`, mặc định: chủ tài khoản). Ai ký phía khách (kế toán trưởng / giám đốc tài
  chính) là dữ liệu: sửa chuỗi ở Quản trị › Chuỗi duyệt. Biên bản ghi người xin ký và người duyệt; không gỡ chữ ký được.
- Việc `base.mock_runs` tự đạt khi có ≥ 2 lượt thử ĐẠT (không lệch, hoặc lệch đã ký).
- Ngưỡng do khách ký: gợi ý đếm 0 %, tiền ± 0,5 % theo tháng.

## 6. Ngày chuyển

Thứ tự trong buổi:
1. Khoá nhập liệu ở hệ cũ (chỉ đọc) — đánh dấu `base.legacy_frozen`.
2. Chạy **lượt cuối**: `ob.load(..., kind="final", source_cut_at=<lúc khoá hệ cũ>)` — kéo lại toàn bộ, chỉ phần đổi mới chạy lệnh; chạy lại được nhiều lần
   trong buổi.
3. Đọc biên bản lượt cuối; còn lệch thì ký (hoặc sửa → chạy lại lượt cuối). Lượt cuối **mới nhất** là lượt được xét.
4. Ghi số dư đầu kỳ tại ngày cắt (gói Tài chính: Kế toán › Số dư đầu kỳ, khớp tổng kiểm) — đánh dấu việc của gói.
5. Kiểm im trên bản sao DB (`scripts/check_quiet_after_load.py` in `IM`) — đánh dấu `base.quiet_check`.
6. Họp go / no-go — đánh dấu `base.go_no_go` kèm biên bản.
7. **Chủ tài khoản** bấm **Chuyển sang Barista** (ngày chuyển = ngày đầu làm việc trên Barista).

`onboard.go_live` **từ chối** (kể cả trước khi gửi phiếu duyệt) khi: người bấm không phải chủ tài khoản; còn việc bắt buộc chưa xong; chưa có lượt cuối / lượt cuối
mới nhất chưa xong; lượt cuối còn chỉ số lệch chưa ký; công ty đã chuyển rồi. Trợ lý không bấm được. Đủ điều kiện ⇒ qua chuỗi **"Ngày chuyển sang Barista"**
(`onboard_go_live`, mặc định chủ tài khoản; chủ tài khoản duy nhất ⇒ chạy ngay, nhật ký ghi tự duyệt) rồi chạy **các bước ngày chuyển do gói khai**, theo thứ tự,
trong CÙNG một giao dịch (một bước lỗi ⇒ không bước nào ghi, lời báo nêu bước lỗi):

| Bước | Gói | Làm gì |
|---|---|---|
| Khoá kỳ kế toán tới hết ngày trước ngày chuyển | Tài chính | Tiến mốc khoá kỳ của mọi pháp nhân đang dùng (cơ chế khoá kỳ của sổ cái); đã khoá tới đó (vd lô đầu kỳ đã khoá) ⇒ giữ nguyên |

Kết quả từng bước + ngày chuyển hiện trên đầu màn Đưa công ty vào dùng. **Không có hoàn tác ngày chuyển.** Cần dừng tự động hoá ⇒ tắt luật ở Quy trình › Luật tự
động; mở lại kỳ kế toán ⇒ `acct.reopen_period` (có duyệt).

Chưa có lệnh — làm tay theo danh sách việc (không bịa bước tự chạy):
- **Kích hoạt lead đỗ** nạp từ hệ cũ (gói Khách hàng): giao lại từ hàng chờ / chuyển người giữ, theo đợt — đừng kích hoạt hàng nghìn lead cùng lúc (runbook 16).
- **Bật luật tự động** đang chạy thử (Quy trình › Luật tự động) sau ngày chuyển.
- **Ngày bắt đầu nhắc nợ** của khoản phải thu còn mở: kế toán trưởng của khách chọn — chưa có cài đặt riêng.

## 7. Trực sau chuyển

2–4 tuần: ghi nhật ký sự cố (đánh dấu `base.hypercare` kèm đường dẫn), xem Sức khoẻ dữ liệu hằng ngày, số dùng màn (Quản trị › lượt dùng).

## Gói khai phần của mình (cho người viết gói)

- **Việc trong danh sách**: `Pack(..., onboard_steps=(OnboardStep("khoa", chặng, "Việc gì", role="vai", required=True, evidence="Bằng chứng gì"),))` — khoá đầy đủ
  `<gói>.<khoa>`, chỉ hiện khi công ty bật gói.
- **Bước ngày chuyển** (việc làm được bằng cơ chế sẵn có của module): `@m.cutover("buoc", "Nhãn", order=30, resource="<tài nguyên>", why="…")` trên hàm
  `async (ctx, inp: CutoverIn) -> {"summary": …, "skipped"?: True}` — lệnh nội bộ `<module>.cutover_buoc`, chỉ `onboard` gọi; phải chạy lại an toàn (đã làm ⇒
  `skipped`). Chưa có lệnh thật ⇒ khai việc tay ở `onboard_steps`, không khai bước.
- **Tài nguyên cấu hình** (gói cấu hình công ty): `m.config("khoa", "Nhãn", read="<module>.config_khoa", write="<module>.<lệnh ghi sẵn có>", order=…,
  match=("ô khoá tự nhiên", …))` — truy vấn đọc (không đầu vào, `agent="deny"`) trả danh sách mục ĐÚNG dạng đầu vào của lệnh ghi: khoá tự nhiên, người theo
  email, không UUID của công ty nguồn, không dữ liệu nghiệp vụ, đã chuẩn hoá như lệnh lưu (xuất lại sau khi nhập phải ra đúng mục đó). Lệnh ghi chỉ thêm, không
  sửa được bản cũ ⇒ `mutable=False`. Thứ tự: thứ mình trỏ tới (vai, đội) có `order` nhỏ hơn.
