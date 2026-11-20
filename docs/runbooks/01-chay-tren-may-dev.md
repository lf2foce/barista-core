# Runbook 01 · Chạy trên máy dev

```bash
bash backend/scripts/dev-db.sh                      # một lần: role barista_owner / barista_app, DB barista_erp_dev + _test + _accounting + _upgrade
cd backend && uv sync
uv run python -m barista.seed --reset                  # xoá sạch DB dev, migrate, nạp dữ liệu mẫu (GIẢ)
BARISTA_DEV_AUTH=1 \
BARISTA_LLM_API_KEY_FILE=/đường/dẫn/.env BARISTA_LLM_API_KEY_VAR=TEN_BIEN_KHOA \
  uv run uvicorn barista.main:app --port 8300          # khoá model: BARISTA_LLM_API_KEY, hoặc đọc một biến từ file .env khác
cd ../macos && swift run BaristaDesk                   # app Mac
```

- `seed --reset` nạp hồ sơ `sample`: CÔNG TY MẪU trung tính của lõi (thương mại – dịch vụ) + phần mẫu của gói ngành ĐANG CÀI khai mình thuộc hồ sơ này.
  Repo lõi không còn gói ngành nào (ADR 0187 / 0191) ⇒ không cài gói thì chỉ có công ty trung tính; `--profile pay-demo` (Lương) là của lõi.
- **Sàn môi giới mẫu (Bất động sản + Hoa hồng — gói ở `barista-packs`)**: cài hai gói vào môi trường lõi, rồi MỌI lệnh chạy bằng `uv run --no-sync`
  (`uv run` thường và `uv sync` đồng bộ lại môi trường theo lockfile ⇒ GỠ gói — cài lại):
  ```bash
  # từ gốc repo lõi; --python là bắt buộc (không có nó uv pip cài vào môi trường ảo đang kích hoạt)
  uv pip install --python backend/.venv/bin/python --no-deps -e ../barista-packs/real_estate -e ../barista-packs/commission
  backend/.venv/bin/python -c "from importlib.metadata import entry_points as e; print(sorted(x.name for x in e(group='barista.packs')))"   # có commission, real_estate
  cd backend
  uv run --no-sync python -m barista.seed --reset                     # công ty mẫu + sàn mẫu (dự án, căn, giao dịch, hoa hồng) như trước khi gói rời lõi
  uv run --no-sync python -m barista.seed --reset --profile san-moi-gioi   # hồ sơ của gói: sàn mẫu của bản cài sàn ("Sàn mẫu"); --profile san-moi-gioi-demo = demo đậm 9 tháng
  BARISTA_DEV_AUTH=1 uv run --no-sync uvicorn barista.main:app --port 8300
  ```
  Gỡ gói: `uv sync`. DB đã có bảng của gói mà môi trường không cài gói: lõi vẫn chạy, coi gói là vắng (thao tác / màn của gói không có, dữ liệu + dòng sổ
  migration giữ nguyên; cài lại là chạy tiếp) — ADR 0155 / 0191. Hồ sơ `san-moi-gioi` / `san-moi-gioi-demo` chỉ có khi gói Bất động sản đang cài.
- Bộ test: `uv run pytest -q` = bộ test LÕI (không gói ngành nào trong repo). Có cài gói ⇒ dùng `uv run --no-sync python -m pytest -q` (bộ lõi vẫn phải xanh;
  các test cấu trúc quét cả mã của gói đang cài — chạy riêng: `uv run --no-sync python -m pytest -q $(grep -v '^#' tests/pack_guards.txt)`). Test của một
  gói: `uv run --no-sync python -m pytest -q ../../barista-packs/<gói>/tests` (từ `backend/`; test của `commission` cần `real_estate` đã cài và ngược lại).
  Kiểm cả hai gói trên một commit lõi sạch: `bash scripts/check_packs.sh --core-ref <commit> --pack real_estate --pack commission` ở repo gói.
  Máy 8 GB: không chạy hai bộ cùng lúc.
- **Muốn DB dev đầy đủ để bấm thử mọi màn** (công ty giả kiểu sàn môi giới, gói của bản cài khách đầu tiên, dữ liệu theo ngày hôm nay): `bash backend/scripts/dev_reset.sh` thay cho `seed --reset` — runbook 18 (cũng ghi DB nào giả, DB nào là dữ liệu thật của khách, không dùng cho demo).
- Đăng nhập dev: màn đầu chọn vai (`<vai>@sanmau.demo`), công ty tạo trước đứng đầu. Sau khi `seed --reset`, mã công ty đổi ⇒ app tự về màn chọn vai.
- Quay demo / kiểm giao diện không cần gõ: `BARISTA_ASK="câu hỏi" swift run BaristaDesk` ⇒ app tự gửi một câu khi mở.
- Đổi model: `BARISTA_LLM_MODEL=anthropic:claude-…` (chuỗi của Pydantic AI).
- Thử lại lỗi tạm, nhà cung cấp dự phòng (`BARISTA_LLM_FALLBACK_MODELS`), cầu dao: runbook 15.

## Bản cài thứ hai: dịch vụ kế toán (0063)

Cùng một bộ code, DB riêng — bản khách đầu tiên không lẫn công ty / gói kế toán. Chạy song song với bản chính:

```bash
export BARISTA_DATABASE_URL=postgresql://barista_app:barista_app@localhost:5432/barista_erp_accounting
export BARISTA_OWNER_DATABASE_URL=postgresql://barista_owner:barista_owner@localhost:5432/barista_erp_accounting
cd backend && uv pip install --no-deps -e ../../barista-packs/accounting_service   # gói ở repo barista-packs (uv sync gỡ — cài lại sau mỗi lần sync)
uv run --no-sync python -m barista.seed --reset --profile accounting      # công ty dịch vụ mẫu + 3 khách (hồ sơ do gói khai)
BARISTA_DEV_AUTH=1 BARISTA_WEB_URL=http://127.0.0.1:3101 uv run --no-sync uvicorn barista.main:app --port 8301   # link gửi khách trỏ web này
cd ../web && BARISTA_CORE_URL=http://127.0.0.1:8301 BARISTA_DIST_DIR=.next-accounting pnpm exec next dev -p 3101
```

- Mở web bằng **http://127.0.0.1:3101** (không phải `localhost`) — cookie theo tên máy, `localhost:3100` của bản chính không bị ghi đè.
- App Mac trỏ bản này: `BARISTA_URL=http://127.0.0.1:8301 swift run BaristaDesk`.
- Đăng nhập dev: `chu` (quản trị) / `minh` (soát xét) / `lan`, `hoa` (phụ trách) `@ketoanmau.demo`; nút ghi tên công ty — chọn "Dịch vụ kế toán mẫu" để xem Tiến độ khách.

### Trợ lý với model thật (bản kế toán)

Khoá: thêm `BARISTA_LLM_API_KEY_FILE=<file .env có khoá> BARISTA_LLM_API_KEY_VAR=<tên biến>` khi chạy core 8301.

- `uv run --no-sync python ../../barista-packs/accounting_service/scripts/demo_accounting.py` — một vòng: hỏi kỳ còn thiếu gì → đề xuất yêu cầu → xác nhận (link) → khách nộp qua link → trợ lý đối
  chiếu, đề xuất nhận.
- `uv run --no-sync python ../../barista-packs/accounting_service/scripts/eval_accounting_assistant.py --runs 5` — đo trợ lý có đi THẲNG không (bước, công cụ lạc đề, chạm trần, đề xuất đúng). Chạy
  lại sau mỗi lần chỉnh luật / đổi model / đổi trần bước. Mốc 02/11 (gemini-3.8-flash, trần 8): A 5/5 · B 4/5 · C 5/5 · D 5/5, tối đa 4 bước.

### Gói mặc định & quyền dùng gói (`BARISTA_PACKS`, quyết định 0065)

`BARISTA_PACKS` = gói MẶC ĐỊNH của bản cài (trống = mọi gói, máy dev): mọi công ty bật được. Gói khác vẫn bày ở chợ gói là "Liên hệ để mở";
khách trả tiền ⇒ nhà cung cấp cấp quyền rồi quản trị công ty bật:

```bash
uv run python -m barista.entitle --tenant "Sàn mẫu" --list
uv run python -m barista.entitle --tenant "Sàn mẫu" --grant hosting --by "<người vận hành>" --note "<hợp đồng>"
```

- Khách đầu tiên: `BARISTA_PACKS=real_estate,kpi,finance,commission,marketing,talk` (khoá cũ `sales` / `brokerage` đổi 08/11/2026 — N10) (Bất động sản, Hoa hồng, Cho thuê, Đào tạo, Tổng đài = gói ngoài lõi ở `barista-packs`: bản cài ghim ở `"packs"` của file bản cài, máy dev cài thêm rồi bật).
- Dịch vụ kế toán: `BARISTA_PACKS=finance,books,accounting_service,talk,training` (gói `accounting_service` cài từ `barista-packs`).
- Trợ lý chỉ có công cụ của gói công ty đang bật; trần `BARISTA_AGENT_MAX_TOOL_CALLS` mặc định 20.
