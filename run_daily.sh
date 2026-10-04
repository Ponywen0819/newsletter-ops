#!/usr/bin/env bash
# 排程入口（systemd timer / cron 都呼叫這支）。每日依序：
#   1. newsletter-fetch --no-report      抓取 → data/curated/<date>.json
#   2. newsletter-agent        Claude Agent SDK 依 news-digest skill 寫 reports/<date>.md，並驗收（有更新、格式正確）
#   3. render_email.py         reports/<date>.md → reports/<date>.html；stdout 是一行 JSON
#   4. send_email.py           讀上一步的 JSON，寄出
# 任一步失敗就停下並以該步的 exit code 結束（systemd 會標成 failed），過程全進 logs/<YYYY-MM>.log。
# 機密從 env 檔載入，預設 ~/.config/newsletter-ops/env（NEWSLETTER_ENV_FILE 可改），權限必須是 600。
# 額外參數（如 --lookback 72）會轉給 newsletter-fetch。
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

# 之後的輸出全進 log。不能用「{ ...; } >> log || ...」：bash 會在 || 左邊的群組裡停用 set -e，
# 失敗的步驟會被吞掉、整支腳本照樣 exit 0。改用 exec 導向 + ERR trap，set -e 才會生效。
exec >> "$LOG" 2>&1
trap 'status=$?; stamp "失敗，exit $status：$BASH_COMMAND"; exit $status' ERR

# --locked：uv.lock 跟 pyproject.toml 對不上就直接失敗，不要在排程裡自己改鎖檔
stamp "uv run newsletter-fetch --no-report $*"
uv run --locked newsletter-fetch --no-report "$@"
stamp "uv run newsletter-agent"
uv run --locked newsletter-agent
stamp "uv run src/render_email.py"
# 不用 render_email | send_email：pipefail 下 render 失敗時 send_email 也會因讀不到 JSON 而崩，
# 蓋掉 render 的 exit code 並多印一段無關的 traceback。先收進變數，失敗就停在這裡。
meta=$(uv run --locked src/render_email.py)
stamp "uv run src/send_email.py"
printf '%s\n' "$meta" | uv run --locked src/send_email.py
stamp "完成"
