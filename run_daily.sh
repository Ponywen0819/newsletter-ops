#!/usr/bin/env bash
# cron 入口：每天抓取 + 產 curated JSON（+ 選用模板版報告）
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
# cron 的 PATH 很精簡，找不到 uv 的常見安裝位置先補上
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
{
  echo "=== $(date '+%F %T') ==="
  # --locked：uv.lock 跟 pyproject.toml 對不上就直接失敗，不要在排程裡自己改鎖檔
  uv run --locked src/run.py "$@"
} >> "logs/$(date +%Y-%m).log" 2>&1
