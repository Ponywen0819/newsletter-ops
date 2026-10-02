#!/usr/bin/env bash
# 排程入口（systemd timer / cron 都呼叫這支）。每日依序：
#   1. run.py --no-report            抓取 → data/curated/<date>.json
#   2. agent_run.py                  Claude Agent SDK 寫 reports/<date>.md（#2；還沒有這支就略過 2、3）
#   3. render_email.py | send_email.py   轉 email HTML 並寄出
# 任一步失敗就停下並以該步的 exit code 結束（systemd 會標成 failed），過程全進 logs/<YYYY-MM>.log。
# 機密從 env 檔載入，預設 ~/.config/newsletter-ops/env（NEWSLETTER_ENV_FILE 可改），權限必須是 600。
# 額外參數（如 --lookback 72）會轉給 run.py。
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
LOG="logs/$(date +%Y-%m).log"
stamp() { echo "=== $(date '+%F %T') === $*"; }

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

# 之後的輸出全進 log。不能用「{ ...; } >> log || ...」：bash 會在 || 左邊的群組裡停用 set -e，
# 失敗的步驟會被吞掉、整支腳本照樣 exit 0。改用 exec 導向 + ERR trap，set -e 才會生效。
exec >> "$LOG" 2>&1
trap 'status=$?; stamp "失敗，exit $status：$BASH_COMMAND"; exit $status' ERR

stamp "python3 src/run.py --no-report $*"
python3 src/run.py --no-report "$@"
if [ -f src/agent_run.py ]; then
  stamp "python3 src/agent_run.py"
  python3 src/agent_run.py
  stamp "python3 src/render_email.py | python3 src/send_email.py"
  python3 src/render_email.py | python3 src/send_email.py
else
  stamp "略過寫報告與寄信：src/agent_run.py 還不存在（等 #2）"
fi
stamp "完成"
