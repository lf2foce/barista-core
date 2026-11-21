#!/usr/bin/env bash
# Gói của đối tác / bên ngoài (ADR 0242): sinh khuôn gói và tự kiểm gói ở repo RIÊNG của họ — gói không vào repo gói chính thức.
#
#   bash scripts/pack.sh new <khoá> <thư mục> [--title "Tên gói"] [--connector]
#   bash scripts/pack.sh dev <thư mục gói> [--with <gói khác>] [--port 8300] [--web-port 3100] [--no-serve]
#   bash scripts/pack.sh check <thư mục gói> [--core-ref HEAD] [--worktree] [--with <gói khác>] [--keep]
#
# Chi tiết: scripts/packkit/packkit.py, runbook docs/runbooks/21-goi-cua-doi-tac.md.
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/packkit/packkit.py" "$@"
