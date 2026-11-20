#!/usr/bin/env bash
# Bật hook pre-push của repo này (.githooks/pre-push). Chỉ đặt core.hooksPath cho repo hiện tại; gỡ: git config --unset core.hooksPath
set -euo pipefail
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
echo "đã đặt core.hooksPath=.githooks"
