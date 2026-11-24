#!/usr/bin/env bash
# Phát hành bản demo từ Mac (thay GitHub Actions — tài khoản quá hạn mức, runbook 11). Quy trình:
#   commit đã push → checkout RIÊNG đúng commit → cài đúng lockfile → test → gửi đúng mã lên VPS → dựng → migration → kiểm phần mặc định → đổi bản → kiểm → quay lui.
# Kiểm phần mặc định (deploy.py, sau migration, CHỈ ĐỌC): in vai / quyền / chuỗi / luật duyệt của gói đang bật mà công ty chưa có — không cài gì;
# file bản cài "defaults_check": "block" ⇒ thiếu thứ quan trọng thì dừng trước khi đổi bản (mặc định "warn": chỉ in). Runbook 11.
# Test chạy trên bản `git archive` của commit, KHÔNG trên thư mục đang làm: file chưa được Git theo dõi hay sửa dở không làm test qua oan,
# và sửa trong lúc test không lọt vào bản gửi. Không có đường bỏ qua test: chỉ KHÔNG CHẠY LẠI khi đúng nội dung đó vừa qua kiểm đủ (deploy/checks.sh stamp_ok).
#   INSTANCE=<tên> bash deploy/release.sh               # kiểm (~2 phút) + phát hành (~2–4 phút) — BẮT BUỘC nêu bản cài, không có mặc định
#   CHECK_ONLY=1 INSTANCE=<tên> bash deploy/release.sh  # chỉ kiểm trên checkout riêng, KHÔNG gửi lên VPS
# Mọi bản cài dùng chung ảnh của commit (+ bộ gói) — đã có thì không dựng lại. Ảnh lõi do GitHub Actions dựng (images.yml); VPS kéo + ghép gói.
# File bản cài: ../barista-ops/instances/<tên>.json (hoặc BARISTA_OPS_DIR) — nguồn duy nhất, lõi không mang file bản cài. Gói ngoài lõi bản cài khai ("packs": {"<khoá>":
# "v<X.Y.Z>"}) lấy đúng tag <khoá>/v<X.Y.Z> từ ../barista-packs (git archive — deploy/packs.py), kiểm requires-core, test CÙNG lõi, gửi kèm mã
# lõi trong cùng một tar.gz (0155). Thiếu tag / không khớp lõi ⇒ dừng trước khi test.
set -euo pipefail
INSTANCE="${INSTANCE:-}"
[ -n "$INSTANCE" ] || { echo "Thiếu bản cài. Dùng: INSTANCE=<tên> bash deploy/release.sh (tên = barista-ops/instances/<tên>.json; không có mặc định)" >&2; exit 2; }
[[ "$INSTANCE" =~ ^[a-z][a-z0-9]{0,15}$ ]] || { echo "INSTANCE không hợp lệ: $INSTANCE"; exit 1; }
# luôn nêu --instance (deploy.py trên VPS — cả bản cũ — đều nhận); script vận hành mới được chép lên TRƯỚC lệnh phát hành (dưới)
INSTANCE_ARG="--instance $INSTANCE "
case "${RELEASE_MODE:-rolling}" in
  rolling) RELEASE_COMMAND=manual-v2 ;;
  maintenance) RELEASE_COMMAND=manual-maintenance ;;
  *) echo "RELEASE_MODE chỉ nhận rolling hoặc maintenance"; exit 1 ;;
esac
cd "$(git rev-parse --show-toplevel)"
INSTANCE_FILE=$(python3 deploy/packs.py instance "$INSTANCE")
# test chạy ở thư mục TẠM không có barista-ops bên cạnh ⇒ không thấy bản cài thật nào (17/11/2026: stock khai "gate": false làm hỏng test so render cũ
# mà premerge vẫn xanh) — chỉ đường tới repo barista-ops đã dùng cho bản cài này
export BARISTA_OPS_DIR="$(cd "$(dirname "$INSTANCE_FILE")/.." && pwd)"
echo "Bản cài $INSTANCE: $INSTANCE_FILE"
git fetch -q origin main
SHA=$(git rev-parse HEAD)
[ "$(git rev-parse origin/main)" = "$SHA" ] || { echo "HEAD ($SHA) chưa trùng origin/main — commit + push trước"; exit 1; }
WORK=$(mktemp -d "${TMPDIR:-/tmp}/barista-release.XXXXXX")
SHIP=$(mktemp -d "${TMPDIR:-/tmp}/barista-ship.XXXXXX")   # đúng tar.gz sẽ gửi: mã lõi của commit + gói của bản cài
# CHỤP file bản cài MỘT LẦN ở đầu lượt: kiểm (gói ghim) và gửi lên VPS dùng đúng bản chụp này — phiên khác sửa barista-ops giữa lượt không làm lệch
# giữa gói đã kiểm và file gửi đi (rà 21/11/2026). Tên file giữ nguyên (<bản cài>.json); BARISTA_OPS_DIR vẫn trỏ repo thật (đã đặt ở trên).
SNAP=$(mktemp -d "${TMPDIR:-/tmp}/barista-instance.XXXXXX")
cp "$INSTANCE_FILE" "$SNAP/" && INSTANCE_FILE="$SNAP/$(basename "$INSTANCE_FILE")"
# Pha kiểm dùng chung với scripts/premerge.sh (deploy/checks.sh): DB test RIÊNG của lần kiểm này (mã lần chạy ⇒ barista_erp_test_<mã>(_gwN) …; pytest
# tự xoá khi xong, bẫy EXIT xoá nốt phần còn sót) — phiên khác đang chạy test trên cùng máy không đụng DB của lần kiểm này và ngược lại.
. deploy/checks.sh
export BARISTA_TEST_RUN_ID="rel${SHA:0:8}$$"
cleanup() {
  rm -rf "$WORK" "$SHIP" "$SNAP"
  drop_test_dbs
}
trap cleanup EXIT
git archive --format=tar "$SHA" > "$SHIP/core.tar"
stage_tree "$SHIP/core.tar" "$INSTANCE_FILE" "$SHIP" "$WORK"
echo "Kiểm commit $SHA trong $WORK (chỉ gồm file đã commit + gói ở đúng tag)"
# lõi MỘT MÌNH trước (bộ test của lõi là hợp đồng của lõi, không phụ thuộc gói nào cài), rồi mọi gói của bản cài: test cấu trúc của lõi + test từng gói
# CÙNG NỘI DUNG (lõi của commit + gói ở tag ghim) vừa qua kiểm đủ ở premerge / CHECK_ONLY ⇒ không chạy lại hai pha dài (FORCE_TESTS=1 ⇒ luôn chạy).
# Không phải đường bỏ test: dấu chỉ có sau khi ĐÚNG các tệp này đã qua check_core TOÀN BỘ + check_packs (deploy/checks.sh — stamp_write).
# Dấu tách theo phần (deploy/checks.sh): lõi đã qua premerge (luật trước khi đẩy main) ⇒ bản cài nào cũng chỉ test GÓI của nó; web không đổi ⇒ bỏ.
# Web chạy SONG SONG với phần Python (khác tiến trình, khác DB — không đụng nhau); nhật ký web in sau. Ghi thời gian từng pha.
DIGEST="$(tree_digest "$WORK")"
CORE_DIGEST="$(tree_digest "$WORK" core)"
WEB_DIGEST="$(tree_digest "$WORK" web)"
T0=$SECONDS
WEB_PID=""
if ! stamp_ok "web-$WEB_DIGEST" "web"; then
  (T=$SECONDS; rc=0; check_web "$WORK" > "$SHIP/web.log" 2>&1 || rc=$?; echo "  web: typecheck + lint + test: $((SECONDS - T))s" >> "$SHIP/web.log"; exit $rc) &
  WEB_PID=$!
fi
if ! stamp_ok "$DIGEST" "lõi + gói đủ"; then
  T=$SECONDS
  check_core_tiered "$WORK" "$CORE_DIGEST" "release.sh $INSTANCE ${SHA:0:8}" 1
  echo "  lõi: $((SECONDS - T))s"
  T=$SECONDS
  check_packs "$WORK" "$PACKS" "$DIGEST" "release.sh $INSTANCE ${SHA:0:8}"
  echo "  gói của $INSTANCE: $((SECONDS - T))s"
  stamp_write "$DIGEST" "release.sh $INSTANCE ${SHA:0:8}"
fi
if [ -n "$WEB_PID" ]; then
  if wait "$WEB_PID"; then tail -n 3 "$SHIP/web.log"; stamp_write "web-$WEB_DIGEST" "release.sh $INSTANCE ${SHA:0:8}"
  else tail -n 40 "$SHIP/web.log"; echo "web KHÔNG ĐẠT" >&2; exit 1; fi
fi
echo "Kiểm xong: $((SECONDS - T0))s"
if [ "${CHECK_ONLY:-}" = 1 ]; then echo "Kiểm xong $SHA — không gửi (CHECK_ONLY=1)"; exit 0; fi
echo "Phát hành $SHA lên bản cài $INSTANCE …"
# SCRIPT VẬN HÀNH + FILE BẢN CÀI đi CÙNG lượt phát hành, lấy từ ĐÚNG bản vừa test (13/11/2026): trước đây chép tay ⇒ sửa backup.sh 12/11 nằm trong code
# mà VPS vẫn chạy bản 11/11 ⇒ đẩy sao lưu của bản cài đầu tiên lên R2 hỏng hai đêm, issuer OAuth vẫn localhost (runbook 11 mục ⚠️). Chép TRƯỚC khi gọi deploy.py ⇒
# deploy.py mới làm luôn lượt này. File khác thì giữ bản cũ cạnh nó (<file>.bak-<sha>) rồi thay; file giống thì để nguyên.
OPS_TAR="$SHIP/ops.tar"
COPYFILE_DISABLE=1 tar -cf "$OPS_TAR" --no-xattrs --exclude='__pycache__' -C "$WORK" deploy/ops deploy/stack.json deploy/compose.yaml
COPYFILE_DISABLE=1 tar -rf "$OPS_TAR" --no-xattrs -C "$(dirname "$INSTANCE_FILE")" "$(basename "$INSTANCE_FILE")"
ssh phenau-vps "set -euo pipefail; D=/root/$INSTANCE/ops; S=${SHA:0:8}; T=\$(mktemp -d); trap 'rm -rf \$T' EXIT; tar -xf - -C \$T
  [ -d \$D ] || { echo \"thiếu \$D — bản cài chưa thiết lập (runbook 11)\"; exit 1; }
  cd \$T; find . -type f | sort | while read -r f; do
    case \$f in ./deploy/ops/*) dst=\$D/\${f#./deploy/ops/} ;; ./deploy/*) dst=\$D/\${f#./deploy/} ;; *) dst=\$D/instances/\${f#./} ;; esac
    cmp -s \$f \$dst && continue
    mode=600; [ -e \$dst ] && { mode=\$(stat -c %a \$dst); cp -p \$dst \$dst.bak-\$S; }
    install -D -m \$mode \$f \$dst; echo \"  script vận hành: cập nhật \$dst\"
  done" < "$OPS_TAR"
# Ảnh do CI dựng sẵn (.github/workflows/images.yml) — VPS chỉ kéo. CI hỏng mà buộc phải phát hành: BARISTA_BUILD_ON_VPS=1 (dựng trên VPS, rủi ro sự cố 23/11)
BUILD_ON_VPS=""; [ "${BARISTA_BUILD_ON_VPS:-}" = 1 ] && BUILD_ON_VPS="BARISTA_BUILD_ON_VPS=1 "
ssh phenau-vps "${BUILD_ON_VPS}python3 /root/$INSTANCE/ops/deploy.py $INSTANCE_ARG$RELEASE_COMMAND $SHA" < "$SHIP/release.tar.gz"
