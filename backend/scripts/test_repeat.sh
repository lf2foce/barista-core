#!/usr/bin/env bash
# Chạy TOÀN BỘ bộ test N lần (mặc định 3) để bắt test chập chờn (flaky). ~8 phút / 3 lần trên máy 8 GB.
#   bash backend/scripts/test_repeat.sh [N] [-- thêm tham số pytest]
# Mỗi lần chạy: `-n 2` (KHÔNG tăng — máy 8 GB dùng chung), mã lần chạy riêng ⇒ DB `<db>_<mã>_gw0…` riêng, tự xoá khi xong (tests/conftest.py).
# In: test lỗi MỘT SỐ lần (chập chờn), lỗi MỌI lần (lỗi thật), thời gian từng lần. Thoát 1 nếu có test lỗi ở bất kỳ lần nào.
# Chạy tay trước phát hành có đụng lõi / quyền (runbook 11) — không nằm trong pre-push.
set -uo pipefail
cd "$(dirname "$0")/.."

N=3
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then N="$1"; shift; fi
[[ "${1:-}" == "--" ]] && shift
OUT="$(mktemp -d "${TMPDIR:-/tmp}/test-repeat.XXXXXX")"
echo "test_repeat: $N lần, -n 2, kết quả ở $OUT"

for i in $(seq 1 "$N"); do
  start=$SECONDS
  # BARISTA_TEST_RUN_ID bỏ trống ⇒ conftest tự sinh mã mới cho từng lần (DB riêng, không đụng nhau)
  env -u BARISTA_TEST_RUN_ID uv run pytest -q -n 2 --dist loadfile -p no:cacheprovider \
      --junitxml="$OUT/run$i.xml" "$@" > "$OUT/run$i.log" 2>&1
  rc=$?
  echo "$((SECONDS - start))" > "$OUT/run$i.secs"
  echo "  lần $i: exit $rc, $((SECONDS - start))s — $(tail -1 "$OUT/run$i.log")"
done

uv run python -I - "$OUT" "$N" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

out, n = Path(sys.argv[1]), int(sys.argv[2])
failed: dict[str, list[int]] = {}
total = []
for i in range(1, n + 1):
    xml = out / f"run{i}.xml"
    secs = int((out / f"run{i}.secs").read_text())
    if not xml.exists():   # pytest chết trước khi ghi báo cáo (collect lỗi nặng, bị kill)
        failed.setdefault("<pytest không ghi được báo cáo — xem run%d.log>" % i, []).append(i)
        total.append((i, 0, secs))
        continue
    root = ET.parse(xml).getroot()
    cases = list(root.iter("testcase"))
    total.append((i, len(cases), secs))
    for c in cases:
        if c.find("failure") is not None or c.find("error") is not None:
            failed.setdefault(f"{c.get('classname')}::{c.get('name')}", []).append(i)

print()
for i, count, secs in total:
    print(f"lần {i}: {count} test, {secs}s")
print(f"tổng: {sum(s for _, _, s in total)}s")
real = {k: v for k, v in failed.items() if len(v) == n}
flaky = {k: v for k, v in failed.items() if len(v) < n}
print(f"\nCHẬP CHỜN (lỗi một số lần, không phải mọi lần): {len(flaky)}")
for k, v in sorted(flaky.items()):
    print(f"  {k}  — lỗi ở lần {v}")
print(f"LỖI THẬT (lỗi cả {n} lần): {len(real)}")
for k in sorted(real):
    print(f"  {k}")
if failed:
    print(f"\nChi tiết lỗi: {out}/run<N>.log")
sys.exit(1 if failed else 0)
PY
