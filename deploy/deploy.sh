#!/usr/bin/env bash
# 把指定版本部署到這台 host：repo 切到那個 tag（compose 檔跟著版本走）、拉同一個版本的映像、重建有變的容器、等它們 healthy。
#   deploy/deploy.sh v1.4.0
# 平常由 GitHub Actions 的 deploy.yml 經 ssh 呼叫：authorized_keys 用 command= 把那把 key 限定成只能跑這支，
# 版本號從 SSH_ORIGINAL_COMMAND 來（見 deploy/README.md「部署」）。
# 每日流程、白天那輪或手動工作正在跑時，等它跑完（最多 DEPLOY_WAIT 秒，預設 1800）再部署，不打斷執行。
# 包成函式：git checkout 會換掉這個檔，bash 要先讀完整個函式才執行
set -euo pipefail

fail() { echo "[deploy] $*" >&2; exit 1; }

pipeline_busy() {  # worker 在跑、而且 state/pipeline.lock 被占用
  [ -n "$(docker compose ps --status running -q worker)" ] || return 1
  ! docker compose exec -T worker python -c \
    'import fcntl; fcntl.flock(open("state/pipeline.lock", "a"), fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null
}

health() { docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1"; }

main() {
  local tag=${1:-${SSH_ORIGINAL_COMMAND:-}}
  [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "版本號要是 vX.Y.Z：'$tag'"

  cd "$(dirname "$0")/.."
  git fetch --quiet --force --tags origin
  git rev-parse -q --verify "refs/tags/$tag^{commit}" >/dev/null || fail "找不到 tag $tag"
  # 這支腳本之前的版本沒有它；切過去之後下次就部署不了，退到那麼舊的版本要手動
  git cat-file -e "$tag:deploy/deploy.sh" 2>/dev/null || fail "$tag 沒有 deploy/deploy.sh，不能用這個方式部署"
  [ -z "$(git status --porcelain --untracked-files=no)" ] || fail "repo 有本機修改，不覆蓋（本機設定放 .env 或 docker-compose.override.yml）"

  git checkout --quiet --detach "$tag"
  echo "[deploy] repo 在 $tag（$(git rev-parse --short HEAD)）"

  [ -f .env ] || fail "沒有 .env（見 deploy/README.md 步驟 1）"
  export NEWSLETTER_TAG=$tag
  docker compose pull --quiet web worker

  local waited=0 limit=${DEPLOY_WAIT:-1800}
  while pipeline_busy; do
    [ "$waited" -lt "$limit" ] || fail "pipeline 跑了超過 ${limit} 秒還沒結束，這次不部署（repo 已切到 $tag，容器沒動）"
    [ "$waited" -gt 0 ] || echo "[deploy] pipeline 正在跑，等它結束"
    sleep 30; waited=$((waited + 30))
  done

  # 部署的版本寫進 .env：之後在 host 上手動下 docker compose 指令也是這一版（本機的 latest 可能是舊的映像）
  if grep -q '^NEWSLETTER_TAG=' .env; then
    sed -i "s/^NEWSLETTER_TAG=.*/NEWSLETTER_TAG=$tag/" .env
  else
    printf '\n# deploy/deploy.sh 維護：目前部署的版本\nNEWSLETTER_TAG=%s\n' "$tag" >> .env
  fi

  docker compose up -d --remove-orphans

  # podman 的 docker compose ps 不顯示 healthy，直接看每個容器
  local svc id state t
  for svc in web worker; do
    id=$(docker compose ps -q "$svc")
    [ -n "$id" ] || fail "$svc 沒有起來"
    for t in $(seq 1 30); do
      state=$(health "$id")
      [ "$state" = healthy ] && break
      [ "$t" -lt 30 ] || fail "$svc 3 分鐘內沒有 healthy（目前 $state），看 docker compose logs $svc"
      sleep 6
    done
    echo "[deploy] $svc healthy"
  done

  docker image prune -f >/dev/null
  echo "[deploy] $tag 部署完成"
}

main "$@"
exit
