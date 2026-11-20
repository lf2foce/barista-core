# 14 · Demo gói Đào tạo: chuỗi ba trung tâm (dữ liệu giả)

Gói `edu` là bản phân phối riêng ở repo `barista-packs` (rời lõi 12/11/2026 — 0155): cài vào môi trường của lõi trước
(`uv pip install --python backend/.venv/bin/python --no-deps -e ../barista-packs/edu`, từ gốc repo lõi; `uv sync` của lõi gỡ gói đi) — hồ sơ nạp mẫu `edu` / `edu-demo` đi cùng gói.

Dùng để thử gói `edu` như một chuỗi trung tâm thật và so với phần mềm quản lý trung tâm có sẵn trên thị trường (CRM, lớp, điểm danh, học bù, học phí,
giờ dạy, phụ huynh, nhiều cơ sở, báo cáo) — tìm chỗ Barista còn thiếu. Mọi tên người / trung tâm là bịa, SĐT đầu 010 (bất biến 9). Vì sao gói làm vậy:
0116.

Hai bộ dữ liệu, KHÁC nhau:

| Hồ sơ | Dùng cho | Quy mô |
|---|---|---|
| `--profile edu` | bộ eval trợ lý (`barista-packs/edu/scripts/eval_edu.py`), test tay nhanh | 2 cơ sở, 3 lớp, vài người học, có người đã rút đồng ý, một phiếu huỷ chờ duyệt |
| `--profile edu-demo` | demo, so tính năng, chụp màn | 3 cơ sở, 15 lớp, ~165 người học, 7 tuần lịch sử |

## Nạp

DB riêng (không dùng `barista_erp_dev`). Tạo DB một lần như `backend/scripts/dev-db.sh` (chủ `barista_owner`, `GRANT CONNECT` cho `barista_app`):

```
createdb -O barista_owner barista_erp_edu && psql -d postgres -c "GRANT CONNECT ON DATABASE barista_erp_edu TO barista_app"
export BARISTA_DATABASE_URL=postgresql://barista_app:barista_app@localhost:5432/barista_erp_edu
export BARISTA_OWNER_DATABASE_URL=postgresql://barista_owner:barista_owner@localhost:5432/barista_erp_edu
cd backend && uv run --no-sync python -m barista.seed --reset --profile edu-demo        # ~20 giây (--no-sync: giữ gói vừa cài)
BARISTA_DEV_AUTH=1 uv run --no-sync uvicorn barista.main:app --port 8302                # + khoá model nếu muốn hỏi trợ lý
cd ../web && BARISTA_CORE_URL=http://127.0.0.1:8302 BARISTA_DIST_DIR=.next-edu pnpm exec next dev -p 3102
```

Worktree mới chưa có `backend/.env` (kho tệp): chạy `bash backend/scripts/dev-db.sh` hoặc đặt `BARISTA_FILES_DIR` (thư mục bền) + `BARISTA_FILES_STORE=local` + `BARISTA_FILES_SIGN_SECRET` trước lệnh nạp, không thì migration kho tệp dừng.
Bản trên VPS: **edu.phenau.com** (runbook 11, "Thêm bản cài edu"); nạp một lần khi DB trống.

Nạp lại cùng lệnh `--reset` (xoá sạch DB đó). Hạt giống ngẫu nhiên cố định ⇒ cùng ngày chạy ra cùng bộ tên; lịch sử luôn tính lùi từ HÔM NAY.

## Có gì trong dữ liệu

- **Cơ sở** (đội con của trụ sở): CS-1 Sông Hồng, CS-2 Hồ Tây, CS-3 Cầu Giấy. Mỗi cơ sở 3 phòng (P101, P102, P103 có đàn) là nguồn lực trên Lịch.
- **Chương trình / gói bán:** Tiếng Anh thiếu nhi (48 buổi + 2 tặng, 12 triệu, vắng có báo được học bù), IELTS (36 buổi, 18 triệu), Toán tư duy
  (24 buổi, 6 triệu), Piano nhóm 4 bạn (16 buổi, 8 triệu). Điều khoản để GIẢ ĐỊNH (chờ chủ trung tâm); ngưỡng "sắp hết buổi" = 3.
- **Lớp:** 5 lớp mỗi cơ sở, mã `<cơ sở>-<lớp>`: `TA1` (T2/T4 17:30), `TA2` (T3/T5 17:30), `IE` (T3/T5 18:00; lớp `1-IE` có trợ giảng),
  `TD` (T7/CN 08:00), `PN` (T3/T5 19:15). Buổi đã xếp từ 7 tuần trước tới 3 tuần sau.
- **Người học:** ~130 chuyển từ "hệ thống cũ" (nhập số dư, mã gói `NHAP-CU-…`, ngày bán lùi 2–5 tháng), ~35 bán mới hôm nay (giáo vụ, và tư vấn viên ở
  lớp tiếng Anh CS-1; mỗi lớp tiếng Anh có một cặp anh chị em chung gói, giảm 500 nghìn). Mỗi trẻ có người giám hộ là người trả.
- **Lịch sử:** mọi buổi đã qua được giáo viên điểm danh (đa số có mặt; ~4% vắng, ~5% vắng có báo, ~5% muộn) và xác nhận ⇒ sổ buổi trừ theo điều khoản.
  Lớp IELTS để buổi gần nhất CHƯA xác nhận (bàn việc giáo viên, nhắc quá 24 giờ).
- **Học phí qua Kế toán (BẢN DEMO — 0147 lát A, công tắc `edu.tuition_in_acct` bật cho công ty demo, mặc định tắt ở nơi khác):** 28 gói bán mới (27 + 1 người lớn)
  có khoản phải thu "doanh thu nhận trước": 12 gói đóng một lần, 9 chia 2 đợt (50% + 50% sau 30 ngày), 6 theo tháng 3 đợt; ngày bán lùi 2–38 ngày trước hôm nay
  nên có đợt đã quá hạn. Tổng 49 khoản (cả 28 gói) = 327 triệu phải thu, đã thu 157,14 triệu. Tiền về là **21 dòng sao kê** của
  tài khoản `NH-ST` (đối soát theo mã gói trong nội dung): 19 dòng ghi phiếu thu, 1 gói trả thiếu một phần (đợt đầu 60%, kế toán có việc chênh lệch), 1 dòng
  tiền về không rõ nội dung (3.333.000 đ) nằm ở Đối soát sao kê. ~10 gói đã trả đủ, 11 đợt quá hạn ⇒ 11 việc "Khách trả tiền" cho người bán. Màn **Còn nợ**
  (Đào tạo): `gv1` / `ql1` thấy phần của mình / cơ sở mình, `ketoan` thấy cả công ty (kèm Kế toán › Khoản phải thu, Đối soát sao kê, Số dư sổ cái:
  `deferred:…`), giáo viên không có màn. Giả định kế toán: xem ADR 0147 (bổ sung 11/11).
- **Câu chuyện cài sẵn:**
  - Người học ĐÃ HẾT buổi nhưng vẫn học: lớp `TA1` mỗi cơ sở + `2-TD` có một người, buổi cuối được xác nhận cùng lớp và gắn cờ "đã học chưa trừ";
    giáo vụ có thông báo. Người SẮP HẾT (còn 2): lớp `TA2`, `IE` mỗi cơ sở.
  - Nguy cơ rời lớp (0164): lớp `TD` mỗi cơ sở + `1-IE` có một người vắng 3 buổi đã xác nhận gần nhất (một lần có báo); lớp `PN` ở CS-1 / CS-3 có một
    người vừa dùng hết buổi ở buổi đã xác nhận cuối, chưa mua gói mới ("Hết buổi, chưa tái tục").
  - Đồng ý: 5 gia đình đã RÚT đồng ý xử lý dữ liệu của con, 1 học viên IELTS cơ sở 2 tự rút — cột "Đồng ý dữ liệu" và khối đỏ trên thẻ suất học.
  - Trung tâm huỷ buổi tới của `1-TA1` (giáo viên tập huấn) ⇒ cả lớp có quyền học bù; hai người đã được xếp bù vào buổi tới của `1-TA2`.
  - Khách hàng: 5 lead của tư vấn viên (nguồn quảng cáo / giới thiệu / tự đến); hai lead học thử buổi tới ở `1-TA1` / `1-IE`, một bé học thử chỉ ghi tên.
  - Một học viên người lớn mua IELTS ở CS-1 rồi xin huỷ trước buổi đầu ⇒ phiếu chờ quản lý CS-1 duyệt.

## Đăng nhập theo vai

Đăng nhập dev: `<tên>@sangtaonho.demo` (cần `BARISTA_DEV_AUTH=1`, chỉ nhận từ máy local).

Màn chọn vai hiện CHỨC DANH từng người (gói `hr` bật trong công ty demo — Hồ sơ làm việc, chức danh giả trong `seed_edu_demo.TITLES`) và ghi rõ vai quyền ở dòng mô tả ("Vai Quản trị hệ thống — …"). `chu` là **Chủ chuỗi trung tâm** nhưng giữ vai Quản trị hệ thống: thấy học vụ, gói bán, khách của mọi cơ sở và người dùng / quyền, KHÔNG thấy tiền (khoản phải thu, sổ cái, dòng tiền — vai Kế toán / Giám đốc).

| Vai | Đăng nhập | Thấy / làm được gì để thử |
|---|---|---|
| Quản trị (chủ) | `chu` | mọi cơ sở; chương trình, gói bán, nhập số dư, quy định |
| Giám đốc | `giamdoc` | đọc toàn chuỗi (người học, lớp, gói đã bán, sổ buổi); không sửa học vụ |
| Kế toán | `ketoan` | gói đã bán toàn chuỗi (giá, giảm, người trả); không thấy điểm danh |
| Quản lý cơ sở | `ql1`, `ql2`, `ql3` | cơ sở mình: người học, lớp, buổi, sổ buổi (đảo dòng), duyệt huỷ gói (`ql1` có phiếu chờ), Lịch cơ sở, nhắn tin (chưa gọi video — xem 0116 "Nền 08/11") |
| Giáo vụ | `gv1`, `gv2`, `gv3` | người học mình phụ trách, lớp, xếp buổi, học bù, học thử, bán gói; Lịch cơ sở mình (tìm phòng rảnh); nhắn tin (chưa gọi video) |
| Giáo viên | `thayminh`, `cothu`, `thayson` (CS-1), `colan`, `thaykhoa`, `coyen` (CS-2), `thaynam`, `comai`, `covy` (CS-3) | buổi mình dạy, điểm danh, xác nhận buổi; tên người học, không người trả / giá |
| Trợ giảng | `trogiang` | giờ dạy của mình ở lớp `1-IE`, không danh sách người học |
| Tư vấn viên | `tuvan` | lead, bán gói tiếng Anh ở CS-1 |

Vai phụ huynh chưa có (lát sau, ADR riêng).

## Kiểm nhanh sau khi nạp

- `gv1` → Đào tạo → "Người học & số buổi": KPI "Đã học chưa trừ" = 1, "Đồng ý đã rút" ≥ 1; mở người có cờ đỏ ⇒ khối đầu thẻ.
- `thayson` (giáo viên IELTS CS-1) → bàn việc: một buổi chưa xác nhận.
- Thẻ thông tin (0164): `giamdoc` / `chu` → Đào tạo → "Tổng quan chuỗi" (ba cơ sở cạnh nhau, học phí quá hạn theo cơ sở bán); `gv1` → "Sắp hết buổi",
  "Nguy cơ rời lớp" (mỗi dòng một lý do) → mở một người ⇒ hồ sơ (còn lại, chuyên cần 4 tuần, buổi tới, lý do, lớp, điểm danh gần đây); "Lớp" → `1-IE`
  ⇒ thẻ lớp (phòng, chuyên cần theo tuần, ai sắp hết buổi). `ql1` thấy cơ sở mình, giáo viên không thấy danh sách, `ketoan` không có màn. Chụp màn:
  `pnpm -C web shots:edu-cards <thư mục>` (BARISTA_WEB_URL trỏ web đang chạy).
- `ql1` → phiếu chờ duyệt "Huỷ gói đã bán".
- Trợ lý (cần khoá model) với `ql2`: "Gia đình bạn … còn đồng ý cho trung tâm giữ dữ liệu không?" ⇒ phải nói ĐÃ RÚT, ai rút, ngày.
