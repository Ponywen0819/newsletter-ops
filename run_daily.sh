#!/usr/bin/env bash
# 排程入口：補 PATH、載入 env 檔（權限須為 600）、輸出導向 logs/，再交給 newsletter-run
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
LOG="logs/$(date +%Y-%m).log"
stamp() { echo "=== $(date '+%F %T') === $*"; }
# cron／systemd 的 PATH 很精簡
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

ENV_FILE="${NEWSLETTER_ENV_FILE:-$HOME/.config/newsletter-ops/env}"
if [ -e "$ENV_FILE" ]; then
  # 不用 stat：macOS 與 Linux 參數不同
  if [ "$(ls -ld "$ENV_FILE" | cut -c5-10)" != "------" ]; then
    stamp "$ENV_FILE 權限太鬆，拒絕載入（chmod 600）" | tee -a "$LOG" >&2
    exit 78
  fi
  set -a
  . "$ENV_FILE"
  set +a
fi

exec >> "$LOG" 2>&1

exec uv run --locked newsletter-run "$@"
