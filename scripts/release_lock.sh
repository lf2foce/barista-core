#!/usr/bin/env bash
# Khoá phát hành (barista-docs/DIEU-PHOI.md, mục "Khoá phát hành"): có dòng ⇒ in người đang giữ, thoát 1; trống ⇒ thoát 0. Không thấy file ⇒ cảnh báo, thoát 0.
# Dùng bởi scripts/premerge.sh và hook pre-push (đẩy main). File ở BARISTA_DOCS_DIR/DIEU-PHOI.md, không thì ../barista-docs cạnh repo lõi CHÍNH.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
common="$(git -C "$here" rev-parse --path-format=absolute --git-common-dir)"   # worktree ⇒ thư mục .git của repo chính
file="${BARISTA_DOCS_DIR:-$(dirname "$(dirname "$common")")/barista-docs}/DIEU-PHOI.md"
if [ ! -f "$file" ]; then
  echo "khoá phát hành: không thấy $file (BARISTA_DOCS_DIR) — bỏ qua kiểm khoá" >&2
  exit 0
fi
rows="$(awk '/^## Khoá phát hành/ {on = 1; next} /^## / {on = 0} on && /^\|/ && !/^\| *Phiên *\|/ && !/^\|[-:| ]+$/' "$file")"
[ -z "$rows" ] && exit 0
echo "KHOÁ PHÁT HÀNH đang có người giữ ($file):" >&2
echo "$rows" >&2
echo "⇒ chờ họ xoá dòng (DIEU-PHOI luật 2–3: trong lúc có khoá không đẩy main, không chạy bộ pytest đầy đủ)." >&2
exit 1
