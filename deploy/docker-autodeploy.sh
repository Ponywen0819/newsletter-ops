#!/usr/bin/env bash
# 更新 repo（compose 檔）、拉最新映像並重建有變的容器；pipeline 正在跑就跳過這一輪
# 包成函式：git pull 可能改到這個檔，bash 要先讀完整個函式才執行
set -euo pipefail

main() {
  cd "$(dirname "$0")/.."

  # 只快轉；本機有修改或分岔就停，不硬蓋
  if ! git pull --quiet --ff-only; then
    echo "[autodeploy] git pull 失敗（本機有修改或分岔？），這輪不更新" >&2
    exit 1
  fi

  docker compose pull --quiet web worker

  if [ -n "$(docker compose ps --status running -q worker)" ] \
     && ! docker compose exec -T worker python -c \
       'import fcntl; fcntl.flock(open("state/pipeline.lock", "a"), fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null; then
    echo "[autodeploy] pipeline 正在跑，這輪不更新"
    exit 0
  fi

  docker compose up -d --remove-orphans
  docker image prune -f >/dev/null
}

main "$@"
exit
