#!/usr/bin/env bash
# DỰNG LẠI DB DEV GIẢ (runbook 18) — công ty "Sàn mẫu (dữ liệu giả)": nền = hồ sơ san-moi-gioi-demo (9 tháng lịch sử, câu chuyện phân tích) của gói Bất động sản + các gói của bản cài
# sàn môi giới + ngân sách / Meta / chấm công / lương / đào tạo…, rồi lớp BỒI (seed_devmock) cho mọi màn.
#
# NHANH: phần nặng (migration + nền + lớp bồi tĩnh; đo 15/11: ~1–4 phút máy rảnh, gấp mấy lần khi máy bận) dựng MỘT lần vào DB MẪU `<db>_template`; mỗi lần reset chỉ xoá DB dev, nhân bản từ DB mẫu (vài giây)
# rồi chạy lớp bồi "hôm nay" (lead cần gọi, hẹn, đợt đóng tiền — vài giây; cả lần reset ~15 giây) để mốc ngày luôn mới. DB mẫu tự dựng lại khi migration / gói / mã seed đổi (băm), hoặc quá 14 ngày.
#
#   bash backend/scripts/dev_reset.sh               # DB barista_erp_dev
#   BARISTA_DEV_DB=barista_erp_dev_x bash …         # DB khác dạng barista_erp_dev_<tên> / barista_erp_devmock* (không bao giờ DB dữ liệu thật)
#   --rebuild      dựng lại DB mẫu dù chưa đổi gì
#   --light        nền gọn hồ sơ san-moi-gioi (không 9 tháng lịch sử; dựng mẫu nhanh hơn)
#   --force        xoá DB dev dù đang có kết nối (API / web nên tắt trước)
#
# Cần: Postgres local, python lõi đã có phụ thuộc (BARISTA_PY — mặc định backend/.venv/bin/python; KHÔNG uv sync / uv run), repo barista-packs cạnh repo này (BARISTA_PACKS_DIR).
# Gói nạp qua thư mục site riêng (dev_packs.py) — không cài gì vào .venv dùng chung.
set -euo pipefail
cd "$(dirname "$0")/.."
FORCE=0; PROFILE=san-moi-gioi-demo; REBUILD=0
for a in "$@"; do case "$a" in --force) FORCE=1;; --light) PROFILE=san-moi-gioi;; --rebuild) REBUILD=1;; *) echo "Không hiểu $a"; exit 2;; esac; done

DB="${BARISTA_DEV_DB:-barista_erp_dev}"
# Hàng rào: chỉ DB dev — không bao giờ DB dữ liệu thật / của phiên khác (DB bản sao dữ liệu thật, DB đo, barista_erp_mgmt_view…)
case "$DB" in
  barista_erp_dev|barista_erp_dev_*|barista_erp_devmock*) ;;
  *) echo "Dừng: $DB không phải DB dev để xoá (chỉ barista_erp_dev, barista_erp_dev_*, barista_erp_devmock*)"; exit 2;;
esac
TPL="${DB}_template"
PY="${BARISTA_PY:-$PWD/.venv/bin/python}"
[ -x "$PY" ] || { echo "Không thấy python lõi: $PY — đặt BARISTA_PY=<đường dẫn python đã có phụ thuộc> (không dùng uv sync / uv run)"; exit 2; }
PACKS_DIR="${BARISTA_PACKS_DIR:-$(cd "$(git rev-parse --path-format=absolute --git-common-dir)/../.." && pwd)/barista-packs}"
[ -d "$PACKS_DIR/real_estate" ] || { echo "Không thấy repo gói: $PACKS_DIR — đặt BARISTA_PACKS_DIR"; exit 2; }
# gói của bản cài sàn môi giới (real_estate, commission, telephony) + Meta / Ngân sách; đổi danh sách bằng DEV_PACKS="a b c"
DEV_PACKS="${DEV_PACKS:-real_estate commission telephony budget meta_ads}"
# gói bật thêm cho công ty dev: ngân sách, Meta, chấm công, đào tạo, báo cáo quản trị, Word, gọi video (+ Lương bật ở lớp bồi, SAU hoa hồng — seed_devmock)
CORE_PACKS="${CORE_PACKS:-budget,meta_ads,timekeeping,training,mgmt,word,meet}"
MAX_AGE_DAYS="${DEV_TEMPLATE_MAX_DAYS:-14}"

T0=$(date +%s)
bash scripts/dev-db.sh >/dev/null   # role + DB dev + backend/.env (kho tệp, khoá ký) — chạy lại an toàn
PSQL=(psql -v ON_ERROR_STOP=1 -h "${PGHOST:-localhost}" -d postgres -q -At)
exists() { "${PSQL[@]}" -c "SELECT 1 FROM pg_database WHERE datname = '$1'" | grep -q 1; }
conns() { "${PSQL[@]}" -c "SELECT count(*) FROM pg_stat_activity WHERE datname = '$1' AND pid <> pg_backend_pid()"; }
drop_db() {   # $1 = tên DB; có kết nối khác ⇒ dừng, trừ --force
  exists "$1" || return 0
  local n; n=$(conns "$1")
  if [ "$n" != "0" ]; then
    [ $FORCE = 1 ] || { echo "Dừng: $n kết nối đang mở vào $1 (API / web / phiên khác?). Tắt chúng rồi chạy lại, hoặc thêm --force."; exit 2; }
    "${PSQL[@]}" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$1' AND pid <> pg_backend_pid()" >/dev/null
  fi
  "${PSQL[@]}" -c "DROP DATABASE $1"
}
new_db() {
  "${PSQL[@]}" -c "CREATE DATABASE $1 OWNER barista_owner" -c "GRANT CONNECT ON DATABASE $1 TO barista_app"
  psql -v ON_ERROR_STOP=1 -h "${PGHOST:-localhost}" -d "$1" -q -c "CREATE EXTENSION IF NOT EXISTS vector SCHEMA public" 2>/dev/null || true
}

SITE="$PWD/.dev-site"
export PYTHONPATH="$PWD/src:$("$PY" scripts/dev_packs.py "$SITE" "$PACKS_DIR" $DEV_PACKS)"
unset BARISTA_ENV BARISTA_PACKS BARISTA_DEMO_COMPANY   # máy dev: mọi gói đã cài đều bật được; tên công ty mặc định đã là "Sàn mẫu (dữ liệu giả)"
use_db() {
  export BARISTA_DATABASE_URL="postgresql://barista_app:barista_app@localhost:5432/$1"
  export BARISTA_OWNER_DATABASE_URL="postgresql://barista_owner:barista_owner@localhost:5432/$1"
}

# Băm những thứ làm DB mẫu cũ đi: migration (lõi + gói), phiên bản gói, mã seed của lõi và phần mẫu của gói, danh sách gói / hồ sơ
KEY=$( { echo "$PROFILE|$DEV_PACKS|$CORE_PACKS"
         find src/barista/migrations -type f -name '*.sql' | sort | xargs shasum
         shasum src/barista/seed*.py
         for p in $DEV_PACKS; do grep -h '^version' "$PACKS_DIR/$p/pyproject.toml"; find "$PACKS_DIR/$p/src" -type f \( -path '*migrations*' -o -path '*sample*' \) \( -name '*.sql' -o -name '*.py' \) | sort | xargs shasum; done
       } | shasum | cut -c1-16 )
COMMENT=$("${PSQL[@]}" -c "SELECT coalesce(shobj_description(oid, 'pg_database'), '') FROM pg_database WHERE datname = '$TPL'" 2>/dev/null || true)
BUILT=${COMMENT##*:}; HAVE=${COMMENT%:*}; HAVE=${HAVE#devtpl:}
AGE_OK=0; if [ -n "$BUILT" ] && [ $(( ($(date +%s) - BUILT) / 86400 )) -lt "$MAX_AGE_DAYS" ]; then AGE_OK=1; fi
if [ $REBUILD = 1 ] || ! exists "$TPL" || [ "$HAVE" != "$KEY" ] || [ $AGE_OK = 0 ]; then
  echo "== DB mẫu $TPL: dựng (hồ sơ $PROFILE · gói: $DEV_PACKS) — chỉ khi migration / gói / seed đổi hoặc quá $MAX_AGE_DAYS ngày; lần này lâu"
  drop_db "$TPL"; new_db "$TPL"; use_db "$TPL"
  "$PY" -m barista.seed --reset --profile "$PROFILE" --with-packs "$CORE_PACKS"
  "$PY" -m barista.seed_devmock --static
  "${PSQL[@]}" -c "COMMENT ON DATABASE $TPL IS 'devtpl:$KEY:$(date +%s)'"
  echo "== DB mẫu dựng xong sau $(( $(date +%s) - T0 )) giây"
else
  echo "== DB mẫu $TPL còn mới (băm $KEY, dựng $(( ($(date +%s) - BUILT) / 3600 )) giờ trước) — dùng lại"
fi

echo "== Nhân bản $TPL → $DB"
drop_db "$DB"
"${PSQL[@]}" -c "CREATE DATABASE $DB OWNER barista_owner TEMPLATE $TPL" -c "GRANT CONNECT ON DATABASE $DB TO barista_app" \
  -c "REVOKE TEMP ON DATABASE $DB FROM PUBLIC, barista_app"   # quyền cấp DB không đi theo DB mẫu — migration 057 đặt lại ở DB gốc, lõi từ chối chạy nếu app còn TEMP
use_db "$DB"
echo "== Lớp bồi theo hôm nay (lead cần gọi, hẹn, đợt đóng tiền)"
"$PY" -m barista.seed_devmock --daily
echo "== File Excel thử nhập"
"$PY" scripts/dev_fixtures.py "$BARISTA_OWNER_DATABASE_URL"
echo "== Xong sau $(( $(date +%s) - T0 )) giây"
cat <<MSG

Đăng nhập dev (không mật khẩu; màn đầu chọn vai) — công ty "Sàn mẫu (dữ liệu giả)":
MSG
psql -h "${PGHOST:-localhost}" -d "$DB" -At -F '  ' -c "SELECT rpad(u.email, 26), rpad(m.role, 15), u.name FROM core.memberships m JOIN core.users u ON u.id = m.user_id WHERE m.active ORDER BY array_position(ARRAY['director','admin','accountant','team_lead','sale','sales_admin','marketing_head','marketer','coordinator','payroll','hr_officer','office','staff'], m.role), u.email" | sed 's/^/  /'
cat <<MSG

Chạy thử (python của lõi trực tiếp, KHÔNG uv run):
  PYTHONPATH="$PYTHONPATH" BARISTA_DATABASE_URL=$BARISTA_DATABASE_URL BARISTA_OWNER_DATABASE_URL=$BARISTA_OWNER_DATABASE_URL \\
    BARISTA_DEV_AUTH=1 "$PY" -m uvicorn barista.main:app --port 8300
  cd ../web && pnpm dev        # http://localhost:3100
Excel thử nhập: $PWD/dev-fixtures/
MSG
