#!/usr/bin/env bash
# 每天 NEWSLETTER_RUN_AT 跑一次 run_daily.sh；停機錯過的不補跑
set -uo pipefail
cd "$(dirname "$0")/.."
AT="${NEWSLETTER_RUN_AT:-08:00}"
if ! [[ "$AT" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "[scheduler] NEWSLETTER_RUN_AT 要是 HH:MM，收到「$AT」" >&2
  exit 2
fi

while true; do
  now=$(date +%s)
  next=$(date -d "today $AT" +%s)
  [ "$next" -le "$now" ] && next=$(date -d "tomorrow $AT" +%s)
  echo "[scheduler] 下次執行：$(date -d "@$next" '+%F %T %Z')"
  sleep $((next - now))
  ./run_daily.sh
  code=$?
  echo "[scheduler] run_daily.sh exit $code"
  # 輸出只進 logs/，失敗時印出來給 docker compose logs 看
  [ "$code" -ne 0 ] && tail -n 20 "logs/$(date +%Y-%m).log"
  sleep 60   # 跨過這一分鐘，避免重複觸發
done
