#!/usr/bin/env bash
# Actions 不能跑時，在自己的電腦建 web、worker 映像推到 GHCR（結果同 release.yml）。
# 用法：deploy/push-images.sh [v1.2.0]   不給版本＝origin/main，打 latest；給 v* tag 就建那個 tag，打同名版本號。
# 一律從 git archive 的乾淨副本建，不受工作目錄的修改影響。需要先 docker login ghcr.io（write:packages）。
set -euo pipefail

IMAGE=ghcr.io/ponywen0819/newsletter-ops
ref=${1:-origin/main}
case "$ref" in
  origin/main) label=latest ;;
  v*) label=$ref ;;
  *) echo "只接受 v* tag（或不給＝origin/main）：$ref" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/.."
git fetch --quiet --tags origin
sha=$(git rev-parse "${ref}^{commit}")
src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT
git archive "$sha" | tar -x -C "$src"

for target in web worker; do
  echo "[push-images] ${IMAGE}-${target}:${label}（sha-${sha:0:7}）" >&2
  docker buildx build --platform linux/arm64 --target "$target" --push \
    -t "${IMAGE}-${target}:${label}" -t "${IMAGE}-${target}:sha-${sha}" "$src"
done
