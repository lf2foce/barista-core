#!/usr/bin/env bash
# Kiểm TRƯỚC KHI ĐẨY / GỘP main của lõi (DIEU-PHOI luật 2) — cùng các pha test của deploy/release.sh (deploy/checks.sh), chạy trên CÂY LÀM VIỆC hiện tại
# (file đã theo dõi + file mới chưa bị .gitignore, kể cả sửa chưa commit), để gói vỡ vì thay đổi của lõi lộ ra TRƯỚC khi gộp, không phải lúc phát hành
# (15/11/2026: một lượt phát hành dừng 4 lần ở test; một lần do lõi đổi chữ hướng dẫn MCP + phạm vi token làm vỡ 2 test của real_estate).
#   bash scripts/premerge.sh <bản cài>               # BẮT BUỘC nêu bản cài: ruff + pyright + TOÀN BỘ test lõi (~5 phút máy 8 GB) + gói ghim của bản cài
#   bash scripts/premerge.sh --quick <bản cài>       # test lõi chỉ: test_meta / pack_surface / dummy_pack / upgrade + file test trùng tên module đã đổi
#   bash scripts/premerge.sh --packs-main <bản cài>  # gói của bản cài lấy ở origin/main của barista-packs thay cho tag ghim (bắt gói vỡ trước khi tag)
# Pha: (1) chụp cây làm việc ⇒ tar, gói của bản cài ở tag + commit ghim (deploy/packs.py — như phát hành) ⇒ thư mục tạm; (2) lõi MỘT MÌNH trong môi
# trường uv RIÊNG của thư mục tạm: ruff + pyright + pytest; (3) cài gói vào môi trường tạm đó: test cấu trúc của lõi (tests/pack_guards.txt) + ruff /
# pyright / pytest từng gói. Không đụng .venv của repo / worktree nào, DB test mang mã lần chạy; bẫy EXIT xoá thư mục tạm + DB. Không chạy web.
# Khoá phát hành (DIEU-PHOI) có dòng ⇒ từ chối chạy (in người giữ). Thoát != 0 ⇒ KHÔNG đẩy main.
set -euo pipefail
QUICK=0
PACKS_MAIN=0
INSTANCE=
while [ $# -gt 0 ]; do
  case "$1" in
    --quick) QUICK=1 ;;
    --packs-main) PACKS_MAIN=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    -*) echo "Tuỳ chọn lạ: $1 (xem --help)" >&2; exit 2 ;;
    *) INSTANCE="$1" ;;
  esac
  shift
done
[ -n "$INSTANCE" ] || { echo "Thiếu bản cài. Dùng: bash scripts/premerge.sh [--quick] [--packs-main] <bản cài> (tên = barista-ops/instances/<tên>.json; không có mặc định)" >&2; exit 2; }
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
bash scripts/release_lock.sh || exit 3
INSTANCE_FILE=$(python3 deploy/packs.py instance "$INSTANCE")
# test chạy ở thư mục TẠM không có barista-ops bên cạnh ⇒ không thấy bản cài thật nào (17/11/2026: stock khai "gate": false làm hỏng test so render cũ
# mà premerge vẫn xanh) — chỉ đường tới repo barista-ops đã dùng cho bản cài này
export BARISTA_OPS_DIR="$(cd "$(dirname "$INSTANCE_FILE")/.." && pwd)"
. deploy/checks.sh

PACKS_REF=""
if [ "$PACKS_MAIN" = 1 ]; then
  COMMON="$(git rev-parse --path-format=absolute --git-common-dir)"
  PACKS_REPO="${BARISTA_PACKS_DIR:-$(dirname "$(dirname "$COMMON")")/barista-packs}"
  git -C "$PACKS_REPO" fetch -q origin main || echo "không fetch được origin của barista-packs — dùng origin/main có sẵn ở máy" >&2
  PACKS_REF=origin/main
  echo "Gói ở $PACKS_REF của $PACKS_REPO = $(git -C "$PACKS_REPO" rev-parse --short "$PACKS_REF")"
fi

WORK="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/premerge.XXXXXX")" && pwd -P)"
SHIP="$(mktemp -d "${TMPDIR:-/tmp}/premerge-ship.XXXXXX")"
export BARISTA_TEST_RUN_ID="pm$$${RANDOM}"
PHASE="chuẩn bị"
T0=$SECONDS
TIMES=""
lap() { TIMES="$TIMES  $PHASE: $((SECONDS - T_PHASE))s"$'\n'; }
finish() {
  rc=$?
  rm -rf "$WORK" "$SHIP"
  drop_test_dbs
  echo
  [ -z "$TIMES" ] || printf '%s' "$TIMES"
  if [ "$rc" = 0 ]; then echo "premerge: ĐẠT ($((SECONDS - T0))s) — đẩy main được"
  else echo "premerge: KHÔNG ĐẠT ở pha \"$PHASE\" (exit $rc, $((SECONDS - T0))s) — đừng đẩy main" >&2; fi
}
trap finish EXIT

PHASE="chụp cây làm việc + gói"; T_PHASE=$SECONDS
python3 - "$ROOT" "$SHIP/core.tar" <<'PY'
import os, subprocess, sys, tarfile
root, out = sys.argv[1], sys.argv[2]
names = subprocess.check_output(["git", "-C", root, "ls-files", "-z", "-c", "-o", "--exclude-standard"]).decode().split("\0")
with tarfile.open(out, "w") as t:
    for n in sorted(set(filter(None, names))):
        p = os.path.join(root, n)
        if os.path.lexists(p) and not os.path.isdir(p):   # file đã xoá ở cây làm việc ⇒ không đưa vào
            t.add(p, arcname=n, recursive=False)
PY
stage_tree "$SHIP/core.tar" "$INSTANCE_FILE" "$SHIP" "$WORK" "$PACKS_REF"
CORE_DIGEST="$(tree_digest "$WORK" core)"   # dấu lõi riêng: release.sh bản cài KHÁC cùng lõi chỉ còn test gói của nó
DIGEST="$(tree_digest "$WORK")"   # trước khi test (test tạo tệp tạm) — dấu cho release.sh khỏi chạy lại khi cùng nội dung
echo "Kiểm cây làm việc $(git rev-parse --short HEAD)$([ -z "$(git status --porcelain)" ] || echo ' + thay đổi chưa commit') trong $WORK"
lap

PYTEST_ARGS=()
if [ "$QUICK" = 1 ]; then
  # file đổi so với điểm rẽ khỏi origin/main (kể cả chưa commit / chưa theo dõi) ⇒ file test trùng tên module
  BASE="$(git merge-base HEAD origin/main 2>/dev/null || git rev-parse HEAD)"
  SEL="tests/test_meta.py tests/test_pack_surface.py tests/test_dummy_pack.py tests/test_upgrade.py"
  shopt -s nullglob
  for f in $( { git diff --name-only "$BASE"; git ls-files -o --exclude-standard; } | sort -u); do
    stem="$(basename "$f" .py)"
    [ "$stem" = __init__ ] && stem="$(basename "$(dirname "$f")")"
    cands=()
    case "$f" in
      backend/tests/*test_*.py) cands=("$WORK/$f") ;;
      backend/src/*.py) cands=("$WORK"/backend/tests/test_"$stem".py "$WORK"/backend/tests/test_"$stem"_*.py "$WORK"/backend/tests/standin/test_"$stem"*.py
                              "$WORK"/backend/tests/test_"${stem%%_*}".py) ;;   # mcp_server.py ⇒ cả test_mcp.py
      deploy/*.py) cands=("$WORK"/backend/tests/test_deploy_"$stem"*.py) ;;
    esac
    for t in ${cands[@]+"${cands[@]}"}; do
      [ -f "$t" ] || continue
      t="${t#"$WORK"/backend/}"
      case " $SEL " in *" $t "*) ;; *) SEL="$SEL $t" ;; esac
    done
  done
  shopt -u nullglob
  echo "--quick: $SEL"
  # shellcheck disable=SC2206
  PYTEST_ARGS=(-n 2 $SEL)
fi

PHASE="lõi: ruff + pyright + pytest"; [ "$QUICK" = 0 ] || PHASE="$PHASE (--quick)"; T_PHASE=$SECONDS
check_core "$WORK" ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}
[ "$QUICK" = 1 ] || stamp_write "core-$CORE_DIGEST" "premerge $INSTANCE $(git rev-parse --short HEAD)"
lap
PHASE="gói ${PACKS_REF:-ghim} của $INSTANCE: pack_guards + test từng gói"; T_PHASE=$SECONDS
check_packs "$WORK" "$PACKS"
lap
# dấu "đã kiểm đủ" chỉ khi kiểm ĐỦ (không --quick) và gói ở tag ghim (không --packs-main) — đúng điều release.sh sẽ kiểm
if [ "$QUICK" = 0 ] && [ -z "$PACKS_REF" ]; then stamp_write "$DIGEST" "premerge $INSTANCE $(git rev-parse --short HEAD)"; fi
