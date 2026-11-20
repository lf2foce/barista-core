#!/bin/sh
# Sao lưu DB của MỘT bản cài (pg_dump dạng custom, nén) vào <thư mục bản cài>/backups, giữ 14 bản mới nhất. Thư mục bản cài = cha của thư mục
# chứa script này (/root/<tên>/ops/backup.sh ⇒ /root/<tên>); tên tệp mang tên bản cài (<tên>-….dump).
# Cron: cron_file của file bản cài instances/<tên>.json (vd /etc/cron.d/<tên>-backup) — bootstrap.py ghi.
# Khôi phục thử: docker exec -i <container> pg_restore -U postgres -d barista_restore_test < bản.dump  (runbook 11).
# Kho tệp (0122): pg_dump KHÔNG chứa thân tệp. Kho thư mục ⇒ chụp thư mục cùng lượt (giữ ít bản trên máy); có bucket sao lưu R2
# (BARISTA_R2_BACKUP_BUCKET trong runtime.env) ⇒ đẩy dump (+ ảnh chụp kho thư mục) lên đó = bản ngoài máy, khoá bucket R2 chống xoá.
set -eu
APP="$1"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NAME=$(basename "$ROOT")
ENV=$ROOT/runtime.env
C=$(docker ps -q --filter "label=com.docker.compose.project=$APP" --filter "label=com.docker.compose.service=postgres" | head -1)
[ -n "$C" ] || { echo "$(date -Is) không thấy container Postgres của $APP"; exit 1; }
# tên DB một nguồn với deploy.py: operations.json "database_name" (đổi DB khi dựng lại từ bản gốc 0085 — runbook 11)
DB=$(python3 -c 'import json; print(json.load(open("'"$ROOT"'/operations.json")).get("database_name", "barista"))')
STAMP=$(date +%Y%m%d-%H%M)
OUT=$ROOT/backups/$NAME-$STAMP.dump
docker exec "$C" pg_dump -U postgres -Fc "$DB" > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
chmod 600 "$OUT"
ls -1t "$ROOT"/backups/"$NAME"-*.dump | tail -n +15 | xargs -r rm -f
UPLOAD="$OUT"
if ! grep -q '^BARISTA_FILES_STORE=r2' "$ENV" && [ -d "$ROOT/files" ]; then
  FOUT=$ROOT/backups/$NAME-files-$STAMP.tar.gz
  # đối tượng ghi một lần; bản đang ghi dở (*.part-*) bỏ qua; tar trả 1 = "tệp đổi trong lúc đọc" (không phải lỗi hỏng bản chụp)
  tar -czf "$FOUT.tmp" --exclude='*.part-*' -C "$ROOT" files || [ $? -eq 1 ]
  mv "$FOUT.tmp" "$FOUT"
  chmod 600 "$FOUT"
  ls -1t "$ROOT"/backups/"$NAME"-files-*.tar.gz | tail -n +4 | xargs -r rm -f   # 3 bản trên máy; bản ngoài máy ở bucket sao lưu
  UPLOAD="$UPLOAD $FOUT"
fi
if grep -q '^BARISTA_R2_BACKUP_BUCKET=.' "$ENV"; then
  # ảnh api ĐANG CHẠY theo sổ phát hành (bản cài có gói ngoài lõi: <sha>-p<băm gói> — không có ảnh api "<sha>" trần; review Codex F4, 12/11/2026); sổ chưa có "images" (bản ghi trước 12/11)
  # ⇒ ảnh đang chạy của dịch vụ api trong Swarm (tên ảnh không viết cứng — đổi tên ảnh 16/11/2026)
  IMAGE=$(python3 -c 'import json; d=json.load(open("'"$ROOT"'/release.json")); print((d.get("images") or {}).get("api") or "")')
  if [ -z "$IMAGE" ]; then
    PREFIX=$(python3 -c 'import json; print(json.load(open("'"$ROOT"'/operations.json"))["prefix"])')
    IMAGE=$(docker service inspect "${PREFIX}_api" --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}' | cut -d@ -f1)
  fi
  # shellcheck disable=SC2086
  docker run --rm --env-file "$ENV" -v "$ROOT/backups:/backups:ro" --label traefik.enable=false --entrypoint python "$IMAGE" \
    -m barista.kernel.files backup-upload $(for f in $UPLOAD; do printf '/backups/%s ' "$(basename "$f")"; done)
fi
echo "$(date -Is) ok $OUT $(du -h "$OUT" | cut -f1)"
