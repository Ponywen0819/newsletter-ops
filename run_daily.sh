#!/usr/bin/env bash
# cron 入口：每天抓取 + 產 curated JSON（+ 選用模板版報告）
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
{
  echo "=== $(date '+%F %T') ==="
  /usr/bin/env python3 src/run.py "$@"
} >> "logs/$(date +%Y-%m).log" 2>&1
