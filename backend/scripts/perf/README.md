# Bộ đo hiệu năng RLS quy mô khách đầu tiên

Tài liệu gốc: ghi chú nội bộ (barista-docs) (số đo §2–§4, §7, §10). Bộ này chạy TAY trên một DB đo riêng, không chạy trong `pytest`.

```sh
# DB đo riêng (tạo bằng superuser cục bộ; barista_owner không có CREATEDB)
psql -d postgres -c "CREATE DATABASE barista_erp_perf OWNER barista_owner" -c "GRANT CONNECT ON DATABASE barista_erp_perf TO barista_app" \
     -c "ALTER DATABASE barista_erp_perf SET synchronous_commit = off"
psql -d barista_erp_perf -c "CREATE EXTENSION IF NOT EXISTS vector SCHEMA public"
export BARISTA_DATABASE_URL=postgresql://barista_app:barista_app@localhost:5432/barista_erp_perf
export BARISTA_OWNER_DATABASE_URL=postgresql://barista_owner:barista_owner@localhost:5432/barista_erp_perf
export BARISTA_DBOS_DATABASE_URL=$BARISTA_OWNER_DATABASE_URL BARISTA_FILES_STORE=local BARISTA_FILES_DIR=/tmp/perf-files BARISTA_FILES_SIGN_SECRET=$(openssl rand -hex 32)
cd backend
uv run python -m barista.seed --profile san-moi-gioi                      # sàn môi giới mẫu (~5 giây)
PGPASSWORD=barista_owner psql -h localhost -U barista_owner -d barista_erp_perf -X -q -v ON_ERROR_STOP=1 -f scripts/perf/gen.sql   # ~30.000 lead, 150.000 tin (vài phút)
uv run python scripts/perf/counts.py scripts/perf/counts.sql > counts.txt    # số dòng mỗi vai thấy — so bằng diff giữa hai bản
uv run python scripts/perf/bench.py --runs 10 --out after.json > after.txt  # p50/p95 từng màn × vai qua executor.query (READ ONLY, RLS thật)
uv run python scripts/perf/cmp.py before.json after.json                    # bảng p95 trước → sau
```

- `gen.sql` chạy bằng owner và dùng biến psql `:'tid'` cho mã công ty (`\gset`). Từ N11b (ADR 0146) `core.tenant()` đọc claims do lõi ký nên
  `set_config('app.tenant_id', …)` trong script owner KHÔNG còn tác dụng — đừng quay lại cách đó. `setseed(0.37)` ⇒ cùng số dòng mỗi lần.
- `counts.py` / `bench.py` nạp người thật bằng `seed.who` và đi qua `db.tenant_tx` ⇒ đo đúng đường app (claims ký, RLS `barista_app`).
- Số máy khác / giờ khác không so trực tiếp được: luôn đo "trước" và "sau" cùng một máy, cùng dữ liệu, cách nhau vài phút.

## Quy mô đủ (bản XL, 11/11/2026 — ADR 0172 §1)

`gen_xl.sql` thay cho `gen.sql` (cùng cách chạy, sau `seed --profile san-moi-gioi`): ~800 người trong 30 đội KD trên cây 4 tầng (công ty › 3 khu vực › 6 khối ›
đội), 30.000 lead / 19.000 khách, 29.000 giao dịch (2.000 ký · 600 cọc · 400 giữ chỗ · 26.000 huỷ), ~6.200 căn, hoa hồng 2 người hưởng / giao dịch ký,
60.000 dòng sổ cái, ~100.000 tin. Đo các màn danh sách + báo cáo quản trị theo vai sale / trưởng đội (26 người) / GĐ khối (6 đội) / vùng / giám đốc / kế toán:

```sh
uv run python scripts/perf/bench.py --xl --set report --runs 10 --out after.json > after.txt
```

So "trước / sau" công bằng: tạo bản sao DB đo bằng `CREATE DATABASE … TEMPLATE …` (nhớ `REVOKE TEMP ON DATABASE <bản sao> FROM PUBLIC, barista_app` — quyền
cấp DB không theo bản sao), chạy code cũ trên bản sao và code mới trên bản gốc, xen kẽ 2 vòng, lấy min của p50 / p95.
