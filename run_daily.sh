#!/usr/bin/env bash
# 排程入口（systemd timer / cron / docker 的 scheduler 都呼叫這支）。它只負責讓環境對，然後交給 newsletter-run：
#   1. 補上 uv 常見的 PATH（cron／systemd 的 PATH 很精簡）
#   2. 載入機密：env 檔預設 ~/.config/newsletter-ops/env（NEWSLETTER_ENV_FILE 可改），權限必須是 600
#   3. 輸出全導向 logs/<YYYY-MM>.log，再 exec uv run newsletter-run
# 每日四步（fetch → agent → render → send）、失敗語意與防重疊的鎖都在 Python：pipeline/src/newsletter_pipeline/daily.py。
# 額外參數（如 --lookback 72）會轉給 newsletter-fetch。exit code 就是失敗那一步的；75 是另一條 pipeline 正在跑。
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
LOG="logs/$(date +%Y-%m).log"
stamp() { echo "=== $(date '+%F %T') === $*"; }
# cron／systemd 的 PATH 很精簡，找不到 uv 的常見安裝位置先補上
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

ENV_FILE="${NEWSLETTER_ENV_FILE:-$HOME/.config/newsletter-ops/env}"
if [ -e "$ENV_FILE" ]; then
  # ls -l 的權限欄 group／other 必須全空；不用 stat，因為 macOS 與 Linux 的參數不同
  if [ "$(ls -ld "$ENV_FILE" | cut -c5-10)" != "------" ]; then
    stamp "$ENV_FILE 權限太鬆，拒絕載入（chmod 600）" | tee -a "$LOG" >&2
    exit 78
  fi
  set -a
  . "$ENV_FILE"   # shell 語法：值含空格（如 Gmail 應用程式密碼）要加引號
  set +a
fi

# 之後的輸出全進 log：newsletter-run 的進度行與各步驟的 stdout／stderr 都繼承這個導向。
exec >> "$LOG" 2>&1

# --locked：uv.lock 跟 pyproject.toml 對不上就直接失敗，不要在排程裡自己改鎖檔
exec uv run --locked newsletter-run "$@"
