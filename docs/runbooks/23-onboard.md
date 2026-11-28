# Runbook 23 · Đưa một công ty vào dùng (onboard)

Người soạn: Claude Opus 5.5 (Anthropic), 28/11/2026. Quyết định nền: ADR 0263 (nạp lịch sử: 0185, đầu kỳ / khoá kỳ: 0119, bản cài production: 0194).
Viết cho người làm onboard: đọc một lượt là biết đang ở chặng nào, thiếu gì, bấm gì. Mã trong runbook là dữ liệu GIẢ.

Một công ty chuyển từ hệ cũ sang Barista đi qua **7 chặng**. Mỗi chặng có **đầu ra phải có** — thiếu đầu ra thì chưa sang chặng sau. Trạng thái nằm ở màn
**Quản trị › Đưa vào dùng › Đưa công ty vào dùng** (danh sách việc); số liệu chuyển nằm ở **Quản trị › Đưa vào dùng › Lượt chuyển dữ liệu**.

| Chặng | Làm gì | Đầu ra bắt buộc | Barista lo bằng |
|---|---|---|---|
| 1. Khảo sát theo chuẩn | Cho khách xem quy trình CÓ SẴN của gói, chỉ ghi phần lệch | Bảng fit-gap (chuẩn / cấu hình / phát triển), quy trình vận hành đã ký | Việc `base.fit_gap`, `base.process_signed` |
| 2. Cấu hình | Lệch nào giải được bằng dữ liệu thì cấu hình (vai, quyền, chuỗi duyệt, cài đặt, từ vựng, mẫu quy trình, luật chia lead) | Cấu hình xong, người dùng thật đã mời | Việc `base.configured`, `base.people`; màn Quản trị |
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
- Kệ tài liệu không tự tạo: quản trị tạo kệ ở Tài liệu sau khi vào.

Sau đó người làm onboard được mời với vai có quyền **Đưa vào dùng · quản lý** (mặc định: Quản trị hệ thống, Giám đốc) và quyền chạy các lệnh mà bộ chuyển dùng.

## 1–2. Khảo sát, cấu hình

Làm trong app. Mỗi việc xong ⇒ ở màn Đưa công ty vào dùng bấm **Đã xong** trên dòng đó và ghi **bằng chứng** (đường dẫn biên bản / tệp, số đếm, người rà) — người
khác đọc là kiểm lại được. Việc bắt buộc không bỏ qua được; việc "Nên có" bấm **Bỏ qua** được. Sai thì **Mở lại**.

## 3. Viết một bộ chuyển dữ liệu

Bộ chuyển của MỘT khách nằm ở repo riêng của khách (có phiên bản + test), không ở lõi. Nó chỉ làm ba việc: đọc hệ cũ → bản ghi trung tính; ánh xạ bản ghi
→ đầu vào lệnh Barista; khai chỉ số đối chiếu. Mọi thứ khác (chạy lệnh, chống trùng, phát hiện thay đổi, ngoại lệ, đối chiếu, nhật ký) là của lõi.

Quy tắc:
- **Tên bảng / cột của hệ cũ chỉ sống trong `extract()`** (bất biến 11). Bản ghi trung tính dùng tên của Barista, có `source_id` (mã ở hệ cũ, chuỗi).
- **Không tự điền dữ liệu thiếu.** Thiếu / sai ⇒ `raise Problem("bad_source", "…")`; tham chiếu chưa nạp ⇒ `ids.need(...)` tự ném `missing_ref`.
- **Thực thể theo thứ tự phụ thuộc**: người trước, khách sau, giao dịch sau cùng.
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
        r = await ob.load(UUID("<mã công ty>"), CONVERTER, by_email="nguoi-lam-onboard@congty.vn", kind="mock")
        print(r.status, r.created, r.updated, r.skipped, r.exceptions, [x for x in r.mismatches])
    finally:
        await db.close()

asyncio.run(main())
```

Bộ chạy làm gì với từng bản ghi:

| Tình huống | Kết quả |
|---|---|
| Chưa có trong sổ mã gốc | chạy `create` (`historical=True`), ghi sổ (nguồn, thực thể, `source_id`) → mã Barista + dấu vân tay |
| Có, dấu vân tay (băm bản ghi trung tính) giữ nguyên | bỏ qua (không gọi lệnh) |
| Có, dấu vân tay đổi, thực thể khai `update` | chạy `update`, cập nhật dấu |
| Có, dấu vân tay đổi, không khai `update` | ngoại lệ "Nguồn đổi sau khi nạp" — sửa tay hoặc khai `update` |
| Thiếu `source_id` / trùng mã trong cùng bản trích / `Problem` / lệnh từ chối / lệnh phải qua duyệt | ngoại lệ ghi lý do + chi tiết, không ghi gì |

- Chạy lại **cùng nguồn bao nhiêu lần cũng không tạo trùng** (sổ mã gốc + khoá chống lặp theo dấu vân tay: chết giữa chừng chạy lại an toàn).
- Thay đổi phát hiện bằng **so dấu vân tay trên toàn bộ bản kéo lại**, không dựa vào cột "ngày sửa" của hệ cũ (không tin được).
- Mọi lệnh chạy chế độ nạp lịch sử: không thông báo, không việc theo dõi, lead nằm đỗ (runbook 16). Vẫn chạy **kiểm im** (`scripts/check_quiet_after_load.py`)
  trên bản sao DB trước khi cho máy chủ trỏ vào.
- Người chạy (`by_email`) phải có quyền chạy từng lệnh của bộ chuyển; lệnh công ty bắt duyệt ⇒ dòng thành ngoại lệ "cần duyệt" (bỏ luật duyệt trong lúc nạp, hoặc
  nạp bằng vai khác).

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
