#!/usr/bin/env bash
# Role + database local. Hai role tách bạch: barista_owner chạy migration và quét xuyên công ty (BYPASSRLS);
# barista_app phục vụ mọi request, KHÔNG BYPASSRLS — quyền theo vai thực thi bằng RLS (xem docs/ARCHITECTURE.md §3).
set -euo pipefail
PSQL=(psql -v ON_ERROR_STOP=1 -h "${PGHOST:-localhost}" -d postgres -q)
"${PSQL[@]}" <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'barista_owner') THEN
    CREATE ROLE barista_owner LOGIN PASSWORD 'barista_owner' NOSUPERUSER BYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'barista_app') THEN
    CREATE ROLE barista_app LOGIN PASSWORD 'barista_app' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;
SQL
# DB test KHÔNG tạo ở đây: bộ test tự tạo + tự xoá DB riêng mỗi lần chạy (barista_erp_test_<mã> / barista_erp_upgrade_<mã>, tests/conftest.py)
for db in barista_erp_dev barista_erp_accounting; do
  "${PSQL[@]}" -Atc "SELECT 1 FROM pg_database WHERE datname = '$db'" | grep -q 1 \
    || "${PSQL[@]}" -c "CREATE DATABASE $db OWNER barista_owner"
  "${PSQL[@]}" -c "GRANT CONNECT ON DATABASE $db TO barista_app"
  # tìm theo nghĩa (0141): pgvector không "trusted" ⇒ chỉ superuser tạo được; máy không có gói pgvector ⇒ migration tự dùng mảng real[]
  if "${PSQL[@]}" -Atc "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'" | grep -q 1; then
    psql -v ON_ERROR_STOP=1 -h "${PGHOST:-localhost}" -d "$db" -q -c "CREATE EXTENSION IF NOT EXISTS vector SCHEMA public"
  fi
done
# Kho tệp (0122): không có thư mục mặc định trong code ⇒ máy dev đặt rõ ở backend/.env (không commit) — chỉ thêm khi chưa có
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
touch "$ENV_FILE"
grep -q '^BARISTA_FILES_DIR=' "$ENV_FILE" || echo "BARISTA_FILES_DIR=$HOME/.barista/files" >> "$ENV_FILE"
grep -q '^BARISTA_FILES_SIGN_SECRET=' "$ENV_FILE" || echo "BARISTA_FILES_SIGN_SECRET=$(openssl rand -hex 32)" >> "$ENV_FILE"
# App từ xa (0140): khoá dẫn xuất khoá ký webhook; máy dev gửi webhook tới http://localhost được
grep -q '^BARISTA_APPS_SIGNING_SECRET=' "$ENV_FILE" || echo "BARISTA_APPS_SIGNING_SECRET=$(openssl rand -hex 32)" >> "$ENV_FILE"
grep -q '^BARISTA_WEBHOOK_ALLOW_PRIVATE=' "$ENV_FILE" || echo "BARISTA_WEBHOOK_ALLOW_PRIVATE=1" >> "$ENV_FILE"
echo "OK: barista_erp_dev, barista_erp_accounting (owner barista_owner, app barista_app); DB test do pytest tự tạo theo lần chạy"
