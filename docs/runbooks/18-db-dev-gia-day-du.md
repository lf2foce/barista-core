# Runbook 18 · DB dev giả đầy đủ (`barista_erp_dev`)

Một lệnh dựng lại DB dev bằng dữ liệu GIẢ của một sàn môi giới, để việc nào cũng bấm thử được. Người làm: Claude Opus 5.5, 15/11/2026.

```bash
bash backend/scripts/dev_reset.sh          # xoá + nhân bản lại barista_erp_dev từ DB mẫu (~15 giây), in danh sách đăng nhập
bash backend/scripts/dev_reset.sh --rebuild   # dựng lại DB mẫu dù chưa đổi gì
bash backend/scripts/dev_reset.sh --light     # nền gọn (hồ sơ san-moi-gioi, không 9 tháng lịch sử), dựng mẫu nhanh hơn (không cần 9 tháng lịch sử)
```

Cần Postgres local (`dev-db.sh` chạy tự động), python của lõi có đủ phụ thuộc (`BARISTA_PY`, mặc định `backend/.venv/bin/python`; **không** `uv sync` / `uv run` — làm hỏng môi trường dùng chung của các phiên khác) và repo `barista-packs` nằm cạnh (`BARISTA_PACKS_DIR`).
Đang có API / web nối vào DB thì script dừng — tắt chúng trước, hoặc `--force`.

## DB nào chứa gì

| DB | Là gì | Dùng để |
|---|---|---|
| `barista_erp_dev` | **GIẢ** — công ty "Sàn mẫu (dữ liệu giả)", dựng bằng lệnh trên | bấm thử, quay / chụp màn hình demo, dev hằng ngày |
| `barista_erp_dev_template` | **GIẢ** — bản dựng sẵn để nhân bản | không nối app vào; script tự quản |
| `barista_erp_accounting` | GIẢ — công ty dịch vụ kế toán (runbook 01) | thử gói kế toán |
| `barista_erp_test_<mã>_gw*`, `barista_erp_upgrade_*` | tạm của pytest | tự xoá khi xong |
| `barista_<khách>_v3_*`, `<khách>_olap` (tên thật: hàng rào trong `scripts/dev_reset.sh`), `barista_erp_mgmt_view` | **DỮ LIỆU THẬT của khách đầu tiên** | chỉ đọc phục vụ migrate / báo cáo quản trị; **KHÔNG** dùng cho demo, ảnh chụp, thử nghiệm; script reset từ chối mọi tên ngoài `barista_erp_dev*` |
| `barista_erp_<tên khác>` | DB của phiên khác | không đụng |

## Chạy (nhanh) thế nào

Phần nặng dựng **một lần** vào `barista_erp_dev_template`: migration → công ty mẫu hồ sơ `san-moi-gioi-demo` của gói Bất động sản (9 tháng lịch sử: ~1.200 lead, ~120 giao dịch, hoa hồng, đối soát, chiến dịch, 8 cuộc gọi…) → các gói bật thêm → lớp bồi `seed_devmock --static`. Mọi thứ đi qua CÁC LỆNH (executor) bằng đúng vai nên số đúng; dựng mẫu mất vài phút (đo 15/11: 60–230 giây khi máy rảnh; máy đang chạy bộ test của phiên khác thì gấp 3–5 lần).
Mỗi lần reset: `DROP` DB dev, `CREATE DATABASE … TEMPLATE …` (vài giây), rồi `seed_devmock --daily` (vài giây) làm ngày mới: lead quá hạn / chưa gọi / hẹn gọi hôm nay / khách nóng / sắp nguội cho 6 sale, lịch hẹn hôm nay + ngày mai, đợt đóng tiền của khách trong 7 ngày tới.
DB mẫu tự dựng lại khi **băm** đổi: migration (lõi + gói), phiên bản gói, mã `seed*.py` của lõi và `sample/` của gói, danh sách gói / hồ sơ — hoặc DB mẫu cũ quá 14 ngày (`DEV_TEMPLATE_MAX_DAYS`), vì mốc của phần tĩnh (chấm công tháng trước, kế hoạch Gantt…) tính từ ngày dựng.

Gói nạp thế nào: lõi tìm gói qua entry point `barista.packs` trong `*.dist-info`. `scripts/dev_packs.py` ghi các `dist-info` đó vào `backend/.dev-site/` (gitignore) từ `pyproject.toml` của từng gói ở `barista-packs` và đặt mã gói lên `PYTHONPATH` — **không cài gì vào `.venv`** nên không phiên nào khác thấy gói lạ. Danh sách gói: `DEV_PACKS` (mặc định = gói bản cài khách đầu tiên cần: `real_estate commission telephony` + `budget meta_ads`). Muốn đúng tag: `git -C ../barista-packs worktree add /tmp/p real_estate/v1.6.1` rồi `BARISTA_PACKS_DIR=/tmp/p`.

## Trong công ty có gì

- Khung: 3 pháp nhân, cây Khu vực › Khối › Team, 20+ người đủ vai (giám đốc, trưởng nhóm, sale, marketing + trưởng phòng marketing, điều phối lead, kế toán, admin, sales admin, hành chính, nhân sự, lương, chấm công).
- Bán hàng: 2 dự án (Hồ Sen Residence `HSR` — kèm 4 ảnh phối cảnh trong Kho tài liệu —, Đồi Thông Park `DTP`), 300 căn, giao dịch ở mọi trạng thái (giữ chỗ, đã cọc, đã ký, huỷ trước và sau ký), lịch đóng tiền, hồ sơ giao dịch, giữ căn + hàng chờ.
- Hoa hồng: chính sách bậc, ví từng sale, rút / ứng (có phiếu chờ duyệt), người hưởng nhiều vai.
- Marketing: 3 chiến dịch Facebook / Google / TikTok, chi phí từng ngày, 3 mẫu quảng cáo mỗi chiến dịch với số theo ngày 6 tuần, luật chia lead + lịch sử chia lead nạp từ hệ khác, đồng chi trả.
- Tài chính: sổ cái, sao kê + đối soát (5 loại lệch), chi phí 4 tháng (một phần theo dự án), ngân sách năm nay đã duyệt + bản điều chỉnh chờ duyệt, so với thực tế.
- Việc: hai kế hoạch có Gantt (giai đoạn, việc chờ nhau, xong / đang làm / trễ), luật tự động hoá, việc nhờ phòng, phiếu chờ duyệt (ứng, chi, ngân sách, nghỉ phép).
- Nhân sự: chấm công theo IP văn phòng (tháng trước đã chốt, tháng này có cờ ngoài mạng / quên bấm ra), nghỉ phép / làm thêm, hợp đồng, lương tháng (kỳ nháp + chạy bóng), khoá đào tạo.
- Kho tài liệu (chính sách, quy chế hoa hồng, kịch bản, kệ kế toán, kệ ban giám đốc), thông báo, phòng trao đổi, tổng đài (cuộc gọi giả).
- File Excel thử nhập ở `backend/dev-fixtures/` (sinh lại mỗi lần): `khach-moi.xlsx`, `khach-tu-file-cu.xlsx` (tiêu đề lộn xộn + dòng lỗi), `ke-hoach-mo-ban.xlsx`, `ngan-sach-nam-sau.xlsx`, `giao-dich.xlsx`.

Tên / SĐT đều giả (SĐT đầu 010, không tồn tại ở VN). Mọi con số ở báo cáo là của dữ liệu giả.

## Đăng nhập

Không mật khẩu: bật `BARISTA_DEV_AUTH=1` ở API, web hiện màn chọn vai. Cuối lệnh in đủ danh sách `<vai>@sanmau.demo`. Hay dùng: `sale1` (An), `truonga` (trưởng team A), `giamdoc`, `ketoan`, `mkt` / `tpmkt`, `admin`, `luong`, `chamcong`.

## Chạy API + web trên DB này

```bash
cd backend
PYTHONPATH="$PWD/src:<in cuối lệnh reset>" BARISTA_DATABASE_URL=… BARISTA_OWNER_DATABASE_URL=… BARISTA_DEV_AUTH=1 \
  .venv/bin/python -m uvicorn barista.main:app --port 8300     # lệnh đầy đủ được in ở cuối dev_reset.sh
cd ../web && pnpm dev                                           # http://localhost:3100
```

## Đã biết / lưu ý

- **Lương và Hoa hồng cùng bật**: công ty bật gói Lương thì hoa hồng của người hưởng cá nhân đi vào sổ công của Lương (0150 §9), không vào ví. Vì vậy lớp bồi bật gói Lương SAU khi ví / rút / ứng đã dựng; hoa hồng phát sinh từ đó (giao dịch ký mới ở dev) sẽ đi theo kỳ lương, ví không tăng. Khách đầu tiên hiện không bật Lương.
- Meta Ads: gói cài + bật, nhưng KHÔNG có kết nối (cần token Meta thật) — màn kết nối trống; số quảng cáo theo ngày nạp bằng lệnh của lõi (`mkt.record_ad_day`) như một nguồn nhập tay.
- Soạn với AI / trợ lý hỏi đáp cần khoá model (`BARISTA_LLM_API_KEY…`, runbook 01); không khoá thì các nút gọi model báo lỗi.
- Lớp bồi làm thêm lead cho sale1–6 nên số cuộc gọi / lead trên DB dev KHÔNG khớp đáp án câu chuyện A–H của `san-moi-gioi-demo` (`scripts/eval_assistant.py --set demo` chạy trên DB riêng).
- `--daily` không idempotent (tạo lead mới) — chỉ chạy ngay sau nhân bản, đã nằm trong script.
