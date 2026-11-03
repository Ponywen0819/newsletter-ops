#!/usr/bin/env bash
# 拉最新映像並重建有變的容器；pipeline 正在跑就跳過這一輪
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose pull --quiet web scheduler

if [ -n "$(docker compose ps --status running -q scheduler)" ] \
   && ! docker compose exec -T scheduler python -c \
     'import fcntl; fcntl.flock(open("state/pipeline.lock", "a"), fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null; then
  echo "[autodeploy] pipeline 正在跑，這輪不更新"
  exit 0
fi

docker compose up -d --remove-orphans
docker image prune -f >/dev/null
