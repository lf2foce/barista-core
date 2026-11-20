# shellcheck shell=bash
# Các pha KIỂM dùng chung của deploy/release.sh (trước khi gửi) và scripts/premerge.sh (trước khi gộp main) — MỘT bản, không chép hai nơi.
# File này được `source`, không chạy riêng. Bash 3.2 (macOS) + bash của VPS; người gọi đã `set -euo pipefail`.
#   stage_tree <lõi.tar> <file bản cài> <thư mục SHIP> <thư mục WORK> [<ref repo gói>]
#       lõi (tar) + gói của bản cài ở tag đã ghim (deploy/packs.py bundle — kiểm tag / ghim / requires-core) ⇒ SHIP/release.tar.gz, bung vào WORK;
#       đặt biến PACKS (khoá gói, cách nhau dấu cách). Có <ref> (chỉ premerge --packs-main): gói ở ref đó của repo gói thay cho tag.
#   check_core <WORK> [tham số pytest…]   lõi MỘT MÌNH: uv sync --frozen trong WORK/backend (môi trường RIÊNG), ruff, pyright, pytest
#   check_packs <WORK> "<khoá…>"          cài mọi gói vào môi trường của WORK, test cấu trúc của lõi (tests/pack_guards.txt), rồi từng gói
#   drop_test_dbs                         xoá DB test mang mã lần chạy BARISTA_TEST_RUN_ID (gọi trong bẫy EXIT)
#   tree_digest <WORK> [core|web]         dấu nội dung (sha256) vừa dựng — gọi NGAY sau stage_tree, trước khi test. Mặc định: backend/ + deploy/ (gồm gói);
#       ``core``: như mặc định nhưng BỎ deploy/packs/ (bộ test lõi là hợp đồng của lõi, chạy khi CHƯA cài gói nào ⇒ không phụ thuộc gói); ``web``: web/
#   stamp_ok <dấu> / stamp_write <dấu> <nhãn>   dấu "đã kiểm đủ": <dấu đủ> = lõi TOÀN BỘ + gói ở tag ghim; core-<dấu lõi> = check_core TOÀN BỘ;
#       web-<dấu web> = typecheck + lint + test của web. release.sh bỏ chạy lại pha nào đã có dấu (cùng lõi, bản cài khác ⇒ chỉ test gói của bản cài đó).
#       FORCE_TESTS=1 ⇒ luôn chạy. Hạn 3 ngày.
# Môi trường Python luôn là WORK/backend/.venv: không đụng .venv của repo / worktree nào đang dùng.
unset UV_PROJECT_ENVIRONMENT VIRTUAL_ENV
# DB test RIÊNG của lần kiểm (backend/tests/conftest.py): bỏ mọi DB chỉ định sẵn ⇒ barista_erp_test_<mã>(_gwN); pytest tự xoá khi xong, bẫy EXIT xoá nốt.
unset BARISTA_TEST_DATABASE_URL BARISTA_TEST_OWNER_DATABASE_URL BARISTA_TEST_UPGRADE_DATABASE_URL
CHECKS_ADMIN_URL="${BARISTA_TEST_ADMIN_URL:-postgresql://localhost:5432/postgres}"
CHECKS_DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

drop_test_dbs() {
  [ -n "${BARISTA_TEST_RUN_ID:-}" ] || return 0
  command -v psql >/dev/null || return 0
  psql "$CHECKS_ADMIN_URL" -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'barista_erp_%_${BARISTA_TEST_RUN_ID}%'" 2>/dev/null |
    while read -r db; do psql "$CHECKS_ADMIN_URL" -qc "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE)" || true; done
}

stage_tree() {
  local core_tar="$1" instance_file="$2" ship="$3" work="$4" ref="${5:-}"
  PACKS=$(python3 "$CHECKS_DEPLOY/packs.py" bundle "$instance_file" "$core_tar" "$ship/release.tar.gz" ${ref:+"$ref"})
  [ -z "$PACKS" ] || echo "Gói đi kèm${ref:+ (ở $ref, KHÔNG phải tag ghim)}: $PACKS"
  tar -xzf "$ship/release.tar.gz" -C "$work"
}

CHECKS_STAMPS="${BARISTA_CHECK_STAMPS:-$HOME/.cache/barista-checks}"

tree_digest() {
  python3 - "$1" "${2:-all}" <<'PY'
import hashlib, os, sys
root, part = sys.argv[1], sys.argv[2]
skip = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".mypy_cache", ".next"}
h = hashlib.sha256(part.encode() + b"\0")
for top in (("web",) if part == "web" else ("backend", "deploy")):
    for d, dirs, files in os.walk(os.path.join(root, top)):
        dirs[:] = sorted(x for x in dirs if x not in skip and not (part == "core" and d == os.path.join(root, "deploy") and x == "packs"))
        for f in sorted(files):
            p = os.path.join(d, f)
            h.update(os.path.relpath(p, root).encode() + b"\0")
            with open(p, "rb") as fh:
                h.update(hashlib.sha256(fh.read()).digest())
print(h.hexdigest())
PY
}

stamp_ok() {
  [ "${FORCE_TESTS:-}" != 1 ] || return 1
  local f="$CHECKS_STAMPS/$1.ok"
  [ -f "$f" ] && [ -n "$(find "$f" -mmin -4320 2>/dev/null)" ] && { echo "Đã kiểm đủ lõi + gói cho ĐÚNG nội dung này: $(cat "$f")"; return 0; }
  return 1
}

stamp_write() {
  mkdir -p "$CHECKS_STAMPS"
  echo "$2 — $(date '+%d/%m %H:%M')" > "$CHECKS_STAMPS/$1.ok"
}

# môi trường Python RIÊNG của WORK (đúng lockfile) — check_core gọi; release.sh gọi thẳng khi bỏ check_core (đã có dấu lõi) mà vẫn test gói
prepare_env() {
  (cd "$1/backend" && uv sync --frozen -q)
}

check_core() {
  local work="$1"; shift
  prepare_env "$work"
  (cd "$work/backend" && uv run --no-sync ruff check src tests ../deploy && uv run --no-sync pyright && uv run --no-sync pytest -q "$@")
}

# web: cài đúng lockfile, typecheck, lint, test — độc lập với Python ⇒ release.sh chạy SONG SONG với check_core / check_packs
check_web() {
  (cd "$1/web" && pnpm install --frozen-lockfile --reporter=silent && pnpm -s typecheck && pnpm -s lint && pnpm -s test)
}

# Cài MỌI gói của bản cài vào cùng môi trường — đúng bộ ảnh api sẽ chạy — TRƯỚC khi test gói nào: test của một gói có thể là kịch bản xuyên gói
# ([tool.barista] test-requires — deploy/packs.py đã kiểm bản cài ghim đủ). Rồi chạy lại các test CẤU TRÚC của lõi (tests/pack_guards.txt): chúng quét
# cả mã của gói ngoài đang cài (0191), rồi test từng gói.
check_packs() {
  local work="$1" packs="$2" key
  [ -n "$packs" ] || return 0
  for key in $packs; do
    (cd "$work/backend" && uv pip install -q --no-deps -e "../deploy/packs/$key")
  done
  # shellcheck disable=SC2046
  (cd "$work/backend" && uv run --no-sync python -m pytest -q $(grep -v '^#' tests/pack_guards.txt))
  # từng gói: HAI LUỒNG song song (máy 8 GB — không hơn; mỗi gói pytest đã -n 2), mỗi gói một mã DB test riêng (cùng tiền tố ⇒ drop_test_dbs vẫn xoá);
  # nhật ký từng gói in theo thứ tự sau khi xong. Gói nào hỏng ⇒ báo đủ tên, thoát != 0.
  local logs="$work/.pack-logs" lane0="" lane1="" i=0 failed="" rc
  mkdir -p "$logs"
  for key in $packs; do
    if [ $((i % 2)) = 0 ]; then lane0="$lane0 $key"; else lane1="$lane1 $key"; fi
    i=$((i + 1))
  done
  _lane() {
    local k
    for k in $1; do
      if (cd "$work/backend" && BARISTA_TEST_RUN_ID="${BARISTA_TEST_RUN_ID:-}$k" && export BARISTA_TEST_RUN_ID &&
          uv run --no-sync ruff check "../deploy/packs/$k" && uv run --no-sync pyright "../deploy/packs/$k/src" &&
          uv run --no-sync python -m pytest -q "../deploy/packs/$k/tests") > "$logs/$k.log" 2>&1
      then echo ok > "$logs/$k.rc"; else echo fail > "$logs/$k.rc"; fi
    done
  }
  _lane "$lane0" & local p0=$!
  _lane "$lane1" & local p1=$!
  wait "$p0" || true
  wait "$p1" || true
  for key in $packs; do
    echo "-- gói $key"
    tail -n 25 "$logs/$key.log"
    rc="$(cat "$logs/$key.rc" 2>/dev/null || echo fail)"
    [ "$rc" = ok ] || failed="$failed $key"
  done
  if [ -n "$failed" ]; then echo "Gói KHÔNG ĐẠT:$failed" >&2; return 1; fi
}
