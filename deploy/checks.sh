# shellcheck shell=bash
# Các pha KIỂM dùng chung của deploy/release.sh (trước khi gửi) và scripts/premerge.sh (trước khi gộp main) — MỘT bản, không chép hai nơi.
# File này được `source`, không chạy riêng. Bash 3.2 (macOS) + bash của VPS; người gọi đã `set -euo pipefail`.
#   stage_tree <lõi.tar> <file bản cài> <thư mục SHIP> <thư mục WORK> [<ref repo gói>]
#       lõi (tar) + gói của bản cài ở tag đã ghim (deploy/packs.py bundle — kiểm tag / ghim / requires-core) ⇒ SHIP/release.tar.gz, bung vào WORK;
#       đặt biến PACKS (khoá gói, cách nhau dấu cách). Có <ref> (chỉ premerge --packs-main): gói ở ref đó của repo gói thay cho tag.
#   check_core <WORK> [tham số pytest…]   lõi MỘT MÌNH: uv sync --frozen trong WORK/backend (môi trường RIÊNG), ruff, pyright, pytest
#   check_core_tiered <WORK> <CORE_DIGEST> <LABEL> <RUN_UPGRADE>
#       tách lõi làm 2 pha có dấu riêng: core-main (ruff + pyright + pytest -m "not upgrade") và core-upgrade (pytest -m "upgrade").
#       RUN_UPGRADE=1 (release.sh, premerge --full, hoặc diff chạm backend/src / packkit) chạy cả hai và ghi thêm dấu tổng core-<CORE_DIGEST>.
#   check_packs <WORK> "<khoá…>" [<DIGEST> <LABEL>]
#       cài mọi gói vào môi trường của WORK, test cấu trúc của lõi (tests/pack_guards.txt), rồi từng gói. Có <DIGEST>: ghi và dùng lại dấu
#       guards-<DIGEST> cùng dấu từng gói pack-<khoá>-<DIGEST> ⇒ lỗi tạm thời ở 1 gói không mất công chạy lại lõi và các gói đã đạt trên cùng nội dung.
#   drop_test_dbs                         xoá DB test mang mã lần chạy BARISTA_TEST_RUN_ID (gọi trong bẫy EXIT)
#   tree_digest <WORK> [core|web|packs]   dấu nội dung (sha256) vừa dựng — gọi NGAY sau stage_tree, trước khi test. Mặc định: backend/ + deploy/ (gồm gói)
#       + mọi thứ khác test lõi đọc (scripts/packkit/, .agents/, web/src, macos/Sources, ios, 3 file docs/) + mọi file bản cài ở $BARISTA_OPS_DIR/instances;
#       ``core``: như mặc định nhưng BỎ deploy/packs/ (bộ test lõi là hợp đồng của lõi, chạy khi CHƯA cài gói nào ⇒ không phụ thuộc gói); ``web``: web/
#       ``packs``: chỉ thứ test gói dùng (backend/, gói, packkit, 3 file docs) ⇒ dấu guards-/pack-<khoá>- không đổi khi commit chỉ sửa web / app / vận hành
#   stamp_ok <dấu> [<nhãn>] / stamp_write <dấu> <nhãn>   dấu "đã kiểm đủ": <dấu đủ> = lõi TOÀN BỘ + gói ở tag ghim; core-<dấu lõi> = check_core TOÀN BỘ;
#       core-main-<dấu lõi> / core-upgrade-<dấu lõi> = từng tầng của lõi; guards-<dấu> / pack-<khoá>-<dấu> = cấu trúc + từng gói;
#       web-<dấu web> = typecheck + lint + test của web. premerge.sh và release.sh đều bỏ chạy lại pha đã có dấu.
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
skip = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".mypy_cache", ".next", ".build", "DerivedData"}
h = hashlib.sha256(part.encode() + b"\0")
# Dấu lõi phải phủ MỌI file bộ test lõi đọc, không chỉ backend/ + deploy/ (rà 22/11: sửa các file dưới đây sau một lượt xanh vẫn giữ dấu cũ ⇒ release.sh
# bỏ test Python oan): scripts/packkit/ (test_packkit nạp), .agents/ (test_packkit đọc SKILL.md của barista-pack), web/src + macos/Sources + ios
# (test_client_neutrality quét mã client; test_web_icons đọc icons.tsx; test_deploy_production đọc session.ts) và 3 file hợp đồng trong docs/.
# Lấy cả THƯ MỤC, không liệt kê từng file ⇒ test mới đọc thêm file trong đó không lọt. Thêm test đọc file ngoài các thư mục này ⇒ thêm vào đây.
# ``packs``: chỉ thứ test GÓI dùng tới — backend/ TOÀN BỘ (test gói import cả conftest lẫn test lõi), gói đi kèm + deploy/packs.py, scripts/packkit,
# 3 file docs (test_guards đọc runbook 12). KHÔNG web / app / .agents / deploy/ops / Dockerfile / file bản cài ⇒ commit chỉ đổi những thứ đó dùng lại dấu gói.
if part == "web":
    tops = ("web",)
elif part == "packs":
    tops = ("backend", os.path.join("deploy", "packs"), os.path.join("deploy", "packs.py"), os.path.join("scripts", "packkit"))
else:
    tops = ("backend", "deploy", os.path.join("scripts", "packkit"), ".agents", os.path.join("web", "src"), os.path.join("macos", "Sources"), "ios")
for top in tops:
    top_path = os.path.join(root, top)
    if os.path.isfile(top_path):
        h.update(top.encode() + b"\0")
        with open(top_path, "rb") as fh:
            h.update(hashlib.sha256(fh.read()).digest())
        continue
    if not os.path.isdir(top_path):
        continue
    for d, dirs, files in os.walk(top_path):
        dirs[:] = sorted(x for x in dirs if x not in skip and not (part == "core" and d == os.path.join(root, "deploy") and x == "packs"))
        for f in sorted(files):
            p = os.path.join(d, f)
            h.update(os.path.relpath(p, root).encode() + b"\0")
            with open(p, "rb") as fh:
                h.update(hashlib.sha256(fh.read()).digest())
if part != "web":
    for rel in ("docs/MODULE-MAP.md", "docs/MONEY-INVARIANTS.md", "docs/runbooks/12-them-mot-goi.md"):
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            h.update(rel.encode() + b"\0")
            with open(p, "rb") as fh:
                h.update(hashlib.sha256(fh.read()).digest())
# file bản cài (barista-ops/instances/*.json) cũng là đầu vào của bộ test (test_deploy_* so render của MỌI bản cài) ⇒ đổi một file bản cài (vd "gate")
# là nội dung khác, không dùng lại dấu cũ (rà 20/11). Web không đọc file bản cài.
ops = os.environ.get("BARISTA_OPS_DIR", "")
if part not in ("web", "packs") and ops and os.path.isdir(os.path.join(ops, "instances")):
    for f in sorted(os.listdir(os.path.join(ops, "instances"))):
        if f.endswith(".json"):
            with open(os.path.join(ops, "instances", f), "rb") as fh:
                h.update(b"instance:" + f.encode() + b"\0" + hashlib.sha256(fh.read()).digest())
print(h.hexdigest())
PY
}

stamp_ok() {
  [ "${FORCE_TESTS:-}" != 1 ] || return 1
  local f="$CHECKS_STAMPS/$1.ok"
  [ -f "$f" ] && [ -n "$(find "$f" -mmin -4320 2>/dev/null)" ] && { echo "Đã kiểm (${2:-$1}) cho ĐÚNG nội dung này: $(cat "$f")"; return 0; }
  return 1
}

stamp_write() {
  mkdir -p "$CHECKS_STAMPS"
  echo "$2 — $(date '+%d/%m %H:%M')" > "$CHECKS_STAMPS/$1.ok"
}

# môi trường Python RIÊNG của WORK (đúng lockfile) — chỉ đồng bộ một lần trong cùng WORK
prepare_env() {
  [ -x "$1/backend/.venv/bin/python" ] || (cd "$1/backend" && uv sync --frozen -q)
}

check_core() {
  local work="$1"; shift
  prepare_env "$work"
  (cd "$work/backend" && uv run --no-sync ruff check src tests ../deploy && uv run --no-sync pyright && uv run --no-sync pytest -q "$@")
}

check_core_tiered() {
  local work="$1" core_digest="$2" label="$3" run_upgrade="${4:-1}" t
  if stamp_ok "core-$core_digest" "lõi đủ"; then
    return 0
  fi
  # Khi cần chạy ĐỦ mà chưa có dấu lõi chính: chạy MỘT lượt pytest gộp để 4 worker gối việc (~190s thay vì 180s + 130s nối tiếp) rồi ghi cả 3 dấu.
  if [ "$run_upgrade" = 1 ] && ! stamp_ok "core-main-$core_digest" "lõi chính"; then
    t=$SECONDS
    check_core "$work"
    stamp_write "core-main-$core_digest" "$label"
    stamp_write "core-upgrade-$core_digest" "$label"
    stamp_write "core-$core_digest" "$label"
    echo "  lõi đủ (ruff + pyright + pytest): $((SECONDS - t))s"
    return 0
  fi
  if ! stamp_ok "core-main-$core_digest" "lõi chính"; then
    t=$SECONDS
    check_core "$work" -m "not upgrade"
    stamp_write "core-main-$core_digest" "$label"
    echo "  lõi chính (ruff + pyright + pytest): $((SECONDS - t))s"
  fi
  if [ "$run_upgrade" = 1 ]; then
    if ! stamp_ok "core-upgrade-$core_digest" "lõi upgrade"; then
      t=$SECONDS
      prepare_env "$work"
      (cd "$work/backend" && uv run --no-sync pytest -q -m "upgrade")
      stamp_write "core-upgrade-$core_digest" "$label"
      echo "  lõi nâng cấp (upgrade): $((SECONDS - t))s"
    fi
    stamp_write "core-$core_digest" "$label"
  fi
}

# web: cài đúng lockfile, typecheck, lint, test — độc lập với Python ⇒ release.sh chạy SONG SONG với check_core / check_packs
check_web() {
  (cd "$1/web" && pnpm install --frozen-lockfile --reporter=silent && pnpm -s typecheck && pnpm -s lint && pnpm -s test)
}

# Cài MỌI gói của bản cài vào cùng môi trường — đúng bộ ảnh api sẽ chạy — TRƯỚC khi test gói nào: test của một gói có thể là kịch bản xuyên gói
# ([tool.barista] test-requires — deploy/packs.py đã kiểm bản cài ghim đủ). Rồi chạy lại các test CẤU TRÚC của lõi (tests/pack_guards.txt): chúng quét
# cả mã của gói ngoài đang cài (0191), rồi test từng gói. Có digest: dùng lại dấu guards + từng gói đã đạt.
check_packs() {
  local work="$1" packs="$2" digest="${3:-}" label="${4:-}" key
  [ -n "$packs" ] || return 0
  local need_guards=1 todo=""
  if [ -n "$digest" ] && stamp_ok "guards-$digest" "pack_guards"; then
    need_guards=0
  fi
  for key in $packs; do
    if [ -n "$digest" ] && stamp_ok "pack-$key-$digest" "gói $key"; then
      continue
    fi
    todo="$todo $key"
  done
  if [ "$need_guards" = 0 ] && [ -z "$todo" ]; then
    return 0
  fi
  prepare_env "$work"
  for key in $packs; do
    (cd "$work/backend" && uv pip install -q --no-deps -e "../deploy/packs/$key")
  done
  if [ "$need_guards" = 1 ]; then
    # shellcheck disable=SC2046
    (cd "$work/backend" && uv run --no-sync python -m pytest -q $(grep -v '^#' tests/pack_guards.txt))
    [ -z "$digest" ] || stamp_write "guards-$digest" "$label"
  fi
  [ -n "$todo" ] || return 0
  # từng gói: HAI LUỒNG song song (máy 8 GB — không hơn), mỗi gói một mã DB test riêng (cùng tiền tố ⇒ drop_test_dbs vẫn xoá);
  # nhật ký từng gói in theo thứ tự sau khi xong. Gói đạt ghi dấu ngay ⇒ lỗi một gói không mất công chạy lại các gói đã đạt.
  local logs="$work/.pack-logs" lane0="" lane1="" i=0 failed="" rc
  mkdir -p "$logs"
  for key in $todo; do
    if [ $((i % 2)) = 0 ]; then lane0="$lane0 $key"; else lane1="$lane1 $key"; fi
    i=$((i + 1))
  done
  _lane() {
    local k
    for k in $1; do
      if (cd "$work/backend" && BARISTA_TEST_RUN_ID="${BARISTA_TEST_RUN_ID:-}$k" && export BARISTA_TEST_RUN_ID &&
          uv run --no-sync ruff check "../deploy/packs/$k" && uv run --no-sync pyright "../deploy/packs/$k/src" &&
          uv run --no-sync python -m pytest -q "../deploy/packs/$k/tests") > "$logs/$k.log" 2>&1
      then
        echo ok > "$logs/$k.rc"
        [ -z "$digest" ] || stamp_write "pack-$k-$digest" "$label"
      else
        echo fail > "$logs/$k.rc"
      fi
    done
  }
  _lane "$lane0" & local p0=$!
  _lane "$lane1" & local p1=$!
  wait "$p0" || true
  wait "$p1" || true
  for key in $todo; do
    echo "-- gói $key"
    tail -n 25 "$logs/$key.log"
    rc="$(cat "$logs/$key.rc" 2>/dev/null || echo fail)"
    [ "$rc" = ok ] || failed="$failed $key"
  done
  if [ -n "$failed" ]; then echo "Gói KHÔNG ĐẠT:$failed" >&2; return 1; fi
}
