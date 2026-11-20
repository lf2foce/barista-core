# 22 · Gói kết nối: nối Barista với một hệ ngoài

Dành cho người viết tích hợp giữa Barista và một hệ khác: CRM, nền website (WordPress…), CMS, tổng đài, sàn bán hàng, hãng vận chuyển, nền quảng
cáo. Đọc kèm runbook 21 (tạo, thử, tự kiểm, gửi một gói) và runbook 12 (hợp đồng đầy đủ của một gói).

## Chọn đúng cách trước

| Hệ của bạn cần | Dùng | Có phải viết gói không |
|---|---|---|
| GỌI VÀO Barista: đẩy lead, báo có nội dung mới, đọc số liệu | **App ngoài**: Quản trị › Ứng dụng đã kết nối cấp token theo vai | Không. Quản trị công ty tự cài app |
| Barista CHỦ ĐỘNG gọi ra: kéo dữ liệu theo lịch, nhận webhook rồi xử lý, đăng bài / tạo đơn trên hệ ngoài | **Gói kết nối** (runbook này) | Có |

## Lõi lo gì, gói lo gì

Lõi có sẵn khung kết nối chung. Mọi gói kết nối dùng chung nó, nên quản trị gặp cùng một màn và cùng một cách báo lỗi ở mọi tích hợp.

| Lõi (không viết lại) | Gói kết nối |
|---|---|
| Kết nối của từng công ty: tên, trạng thái, chế độ "chỉ ghi nhận", cài đặt | **Bộ nối**: lớp con `connectors.Connector` |
| Khoá API: quản trị dán trong app, mã hoá khi lưu, chỉ hiện bốn ký tự cuối | **Lệnh nhận**: `@m.connector(BộNối)`, nhận từng mục |
| Lịch kéo theo `every_min`, con trỏ, lùi dần khi lỗi, dừng khi gói tắt | **Form tạo kết nối**: lệnh của gói gọi `connectors.save` |
| Giao TỪNG MỤC trong điểm lưu riêng; mục lỗi vào hàng thử lại, không chặn cả lô | (tuỳ chọn) lệnh đẩy ra hệ ngoài |
| Cửa webhook `/v1/hooks/connectors/<công ty>/<kết nối>`: kiểm chữ ký, chống lặp sự kiện | |
| Màn Quản trị › Kết nối ngoài: thử kết nối, đồng bộ ngay, đặt khoá, tạm dừng, nhật ký đồng bộ | |
| Sức khoẻ dữ liệu: kết nối lỗi, lâu không đồng bộ, mục bị bỏ | |

Gói kết nối KHÔNG tự làm bảng kết nối, bảng khoá, biến môi trường chứa khoá hay cửa webhook riêng.

## 0. Hệ đó đã có gói chưa

Mỗi hệ ngoài chỉ nên có MỘT gói kết nối trên một bản cài (hai gói cùng hệ tranh nhau khoá bộ nối và đường nhận dữ liệu). Hệ đã có gói thì mở rộng gói đó
(gửi thay đổi cho bên vận hành) thay vì viết gói mới. Gói kết nối hiện có trong repo gói chính thức: `telephony` (tổng đài), `meta_ads` (quảng cáo Meta:
lead, chi phí, điều khiển quảng cáo, gửi kết quả bán — webhook chung mục 5b), `shipping` (hãng vận chuyển), `sales_channels` (kênh bán). Hỏi bên vận hành
trước khi bắt đầu một hệ mới để tránh hai đội làm cùng một việc.

## 1. Tạo gói từ khuôn

```bash
bash scripts/pack.sh new noi_cms ../goi-cua-toi/noi_cms --connector --title "Nối CMS"
```

Khuôn sinh một gói chạy được: bộ nối mẫu (API JSON, khoá Bearer, kéo theo con trỏ, webhook có chữ ký), lệnh nhận ghi vào bảng ví dụ, form tạo kết
nối, màn dữ liệu nhận, và test với hệ ngoài GIẢ. Tự kiểm ngay được (`pack.sh check`).

## 2. Viết bộ nối

```python
class Api(connectors.Connector):
    key = "noi_cms"                      # duy nhất trong bản cài
    label = "Nối CMS"
    secrets = (connectors.Secret("token", "Khoá API"), connectors.Secret("webhook", "Khoá webhook", required=False))
    every_min = 15                       # 0 = không kéo (chỉ webhook / chỉ đẩy ra)
    webhook = True                       # nhận webhook ⇒ bắt buộc khai khoá "webhook"

    async def check(self) -> str: ...                              # khoá dùng được ⇒ câu ngắn cho quản trị
    async def pull(self, cursor: str) -> connectors.Batch: ...     # một lô mục từ con trỏ + con trỏ mới (+ more=True nếu còn)
    def hook_parse(self, payload) -> connectors.Hook | None: ...   # mã sự kiện + mục (hoặc wake=True: chỉ đánh thức lượt kéo)
```

- Bộ nối chỉ đổi dữ liệu hệ ngoài về MỤC (dict JSON gọn). Không chạm DB, không biết quyền, không giữ trạng thái giữa các lượt (trạng thái là con trỏ).
- HTTP qua `connectors.client(base_url, headers)` cho địa chỉ CỐ ĐỊNH của nhà cung cấp: không theo chuyển hướng, có hạn thời gian, test thay được bằng
  hệ giả. Địa chỉ do công ty tự NHẬP (site WordPress, máy CMS riêng): khai tên cài đặt trong `url_settings` (lõi kiểm lúc lưu) và gọi bằng
  `await connectors.client_for(url, headers)` — chỉ https, chặn mọi địa chỉ nội bộ, để không ai trỏ được khung (kèm khoá) vào mạng của máy chủ. Link tải tệp do hệ ngoài trả về
  phải qua `await connectors.check_public_url(url)` trước khi tải (chặn địa chỉ nội bộ).
- Lỗi: `raise connectors.ConnectorError(loại, câu)`. Loại: `auth` (khoá sai / hết hạn — lõi thử lại thưa, chờ quản trị sửa khoá), `rate_limit`
  (`retry_after_s=`), `transient`, `invalid`, `other`. Câu lỗi là câu người đọc được; KHÔNG chép khoá hay thân phản hồi (có dữ liệu khách) vào câu.
- Con trỏ là chuỗi của bạn (mốc thời gian, mã trang…). Lõi chỉ ghi con trỏ mới SAU KHI lệnh nhận đã nhận xong lô; lỗi ⇒ con trỏ đứng, lượt sau kéo lại.

## 3. Viết lệnh nhận

```python
@m.connector(Api)
async def receive(ctx: Ctx, inp: connectors.Delivery) -> dict[str, Any]:
    # inp.connection_id, inp.item (một mục), inp.via ("pull" | "hook")
    ...
```

- Một mục mỗi lần, quyền hệ thống của công ty có kết nối, trong điểm lưu riêng. Từ chối một mục (`Invalid`…) hoặc lỗi bất kỳ ⇒ mục đó vào hàng thử lại
  (5 lần, giãn dần; mục bị bỏ tự xoá sau 30 ngày), các mục khác vẫn vào, con trỏ vẫn tiến.
- Câu lỗi của lệnh nhận hiện trên màn Nhật ký đồng bộ: không đưa dữ liệu khách (tên, SĐT, email) vào câu. Lỗi lạ chỉ lên màn bằng tên lớp lỗi.
- **Chống lặp theo mã của hệ ngoài là bắt buộc.** Lõi có thể giao lại một mục (lần trước lỗi giữa chừng, quản trị đặt lại con trỏ). Con trỏ để khỏi
  kéo lại; mã ngoài để kéo lại cũng không ghi trùng.
- Ghi vào Barista bằng LỆNH của phần sở hữu dữ liệu, không tự làm bảng song song. Ví dụ lead: gọi lệnh nhận lead của lõi (có chống trùng theo nguồn +
  mã ngoài). Gói chỉ giữ bảng riêng cho thứ của chính nó.
- Kết nối ở chế độ "chỉ ghi nhận": lõi tự giao bằng chế độ đó (dữ liệu vào đủ, không chia việc, không báo) — gói không làm gì thêm.

## 4. Form tạo kết nối

Lệnh của gói, đầu vào có kiểu; ô khoá khai `audit=False` (không vào nhật ký thao tác):

```python
@m.command("connect", resource="noi_cms", action="manage", title="Thêm kết nối CMS", agent="deny", category="setup")
async def connect(ctx: Ctx, inp: Connect) -> dict[str, Any]:
    return await connectors.save(ctx, Api, label=inp.label, settings={"site": inp.site}, secrets={"token": inp.token})
```

Gói khai `m.consumes("connector")`. `connectors.save` chỉ lưu được kết nối của bộ nối CHÍNH gói này khai. Sau khi tạo, quản trị thử, đặt lại khoá,
tạm dừng hay gỡ ở Quản trị › Kết nối ngoài (màn chung). `connectors.connections(ctx, Api)` liệt kê kết nối của gói (không có khoá) cho màn riêng.

## 5. Đẩy ra hệ ngoài (khi cần)

Gọi hệ ngoài ở BƯỚC CHUẨN BỊ của lệnh (`prepare=`), không trong thân lệnh (đang trong giao dịch DB):

```python
async def _publish(prep, inp):
    api = await connectors.open(prep, inp.connection_id)   # bộ nối đã có khoá, chỉ kết nối của gói này
    return await api.publish(inp.title, inp.body)

@m.command("publish", resource="noi_cms", title="Đăng lên CMS", prepare=_publish)
async def publish(ctx, inp):
    result = ctx.prepared   # None khi chạy thử / soạn đề xuất ⇒ báo người dùng, không gọi hệ ngoài ở đây
    ...
```

## 5b. Webhook chung cho cả app (khi hệ ngoài chỉ cho MỘT địa chỉ)

Một số nhà cung cấp (Meta) chỉ cho mỗi ứng dụng một địa chỉ webhook, dùng chung cho mọi Trang của mọi công ty. Khi đó bộ nối khai `shared_hook = True`
và viết bốn hàm lớp: `claims(settings)` (khoá định tuyến kết nối nhận — vd mã Trang trong cài đặt), `shared_check(headers, body)` (kiểm chữ ký bằng khoá
CỦA BẢN CÀI), `shared_verify(query)` (GET kiểm địa chỉ, nếu nhà cung cấp cần), `route_keys(payload)` (khoá nêu trong tin). Lượt kéo trả
`Batch(claimed=…)`: khoá mà token của kết nối thật sự đọc được.

Địa chỉ khai với nhà cung cấp: `/v1/hooks/shared/<khoá bộ nối>` của bản cài. Tin đến ⇒ kiểm chữ ký ⇒ tìm kết nối SỞ HỮU khoá ⇒ đặt lượt kéo ngay
(trễ ≤ 1 phút). Tin không mang dữ liệu đi đâu cả; dữ liệu vẫn vào qua lượt kéo bằng token của chính công ty. Khoá chỉ có hiệu lực sau khi lượt kéo xác
minh, nên một công ty gõ mã Trang của công ty khác không lấy được gì. Mỗi khoá một chủ; đổi chủ thật thì nhà vận hành gỡ kết nối cũ.

## 6. Test

Khuôn đã có sẵn các ca ranh giới; giữ và thêm ca của nghiệp vụ:

- kéo hai lần không ghi trùng;
- khoá sai ⇒ kết nối báo lỗi trên màn, khoá không lộ ở đâu;
- gói tắt ⇒ lịch dừng êm, không gọi hệ ngoài;
- công ty khác không thấy dữ liệu; người không có quyền không thêm được kết nối;
- mã chạy chỉ import `barista.api`.

Hệ ngoài trong test là hệ GIẢ: `connectors.use_transport(httpx.MockTransport(hàm_trả_lời))`. Không gọi mạng thật, không dùng khoá thật.

Người đọc mã gói kết nối (bên vận hành) kiểm thêm: mọi địa chỉ lấy từ cài đặt kết nối đều khai trong `url_settings` và gọi bằng `client_for`
(`pack.sh check` cảnh báo chỗ dùng `connectors.client(` với cài đặt trên cùng dòng — chỉ bắt được dạng đơn giản).

## 7. Gửi lên bản cài

Như runbook 21: `bash scripts/pack.sh check <thư mục gói>` ĐẠT ⇒ gửi tag cho bên vận hành để review mã rồi ghim. Bản cài cần biến
`BARISTA_CONNECTOR_KEY` (khoá mã hoá khoá kết nối; bên vận hành sinh một lần khi dựng bản cài). Thiếu biến này thì app từ chối lưu khoá và nói rõ.
Biến này phải nằm trong phần sao lưu bí mật của bản cài: khôi phục DB mà mất biến ⇒ mọi khoá kết nối phải dán lại. Đổi khoá: đặt khoá mới, chuyển
khoá cũ sang `BARISTA_CONNECTOR_KEY_OLD` (lõi giải bằng khoá cũ, lưu mới bằng khoá mới) tới khi quản trị đã lưu lại các khoá.
