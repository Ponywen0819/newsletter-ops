#!/usr/bin/env bash
# 容器內的排程：每天 NEWSLETTER_RUN_AT（預設 08:00，時區看 TZ）跑一次 run_daily.sh。
# 不補跑：容器停機時錯過的那一次就算了，要補請手動 `docker compose run --rm scheduler ./run_daily.sh`。
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
  # run_daily.sh 的輸出只進 volume 裡的 logs/，失敗時順手印出來，docker compose logs 才看得到原因
  [ "$code" -ne 0 ] && tail -n 20 "logs/$(date +%Y-%m).log"
  sleep 60   # 跨過這一分鐘，避免同一個時間點重複觸發
done
