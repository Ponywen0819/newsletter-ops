#!/usr/bin/env bash
# Cloudflare Tunnel 路由的 GitOps：deploy/cloudflare-tunnel.json 是唯一來源，網頁上改的會在下次 apply 被蓋掉。
#   tunnel-sync.sh export   把線上設定匯出成 cloudflare-tunnel.json（第一次導入時跑一次）
#   tunnel-sync.sh check    只驗檔案，不連網
#   tunnel-sync.sh diff     與線上設定比對（不改任何東西）
#   tunnel-sync.sh apply    check 通過後 PUT 整份設定，並補上缺的 DNS CNAME
# 需要的環境變數（check 不用）：CF_API_TOKEN（Tunnel: Edit、DNS: Edit）、CF_ACCOUNT_ID、CF_TUNNEL_ID；apply 另需 CF_ZONE_ID
set -euo pipefail

FILE="${TUNNEL_FILE:-$(dirname "$0")/cloudflare-tunnel.json}"
API=https://api.cloudflare.com/client/v4

fail() { echo "::error::$*" >&2; exit 1; }

for bin in jq curl; do
  command -v "$bin" >/dev/null || fail "找不到 $bin：Debian/Ubuntu 用 'sudo apt-get install -y $bin'，Windows 用 'winget install jqlang.jq'"
done

cf() {  # cf METHOD PATH [curl 參數...]
  local method=$1 path=$2; shift 2
  curl -fsS -X "$method" -H "Authorization: Bearer ${CF_API_TOKEN:?}" -H 'Content-Type: application/json' "$@" "$API$path"
}

tunnel_path() { echo "/accounts/${CF_ACCOUNT_ID:?}/cfd_tunnel/${CF_TUNNEL_ID:?}/configurations"; }

live() { cf GET "$(tunnel_path)" | jq -S '.result.config'; }

cmd_export() {
  cf GET "$(tunnel_path)" | jq -S '{config: .result.config}' > "$FILE"
  echo "wrote $FILE"
  jq -r '.config.ingress[] | "\(.hostname // "(catch-all)")\t\(.service)"' "$FILE"
}

cmd_check() {
  [ -f "$FILE" ] || fail "$FILE 不存在：先用 'deploy/tunnel-sync.sh export' 匯出線上設定"
  jq -e '.config.ingress | type == "array" and length > 0' "$FILE" >/dev/null \
    || fail "config.ingress 不能是空的"
  jq -e '.config.ingress[-1] | has("hostname") | not' "$FILE" >/dev/null \
    || fail "最後一條必須是不帶 hostname 的 catch-all（例如 {\"service\":\"http_status:404\"}）"
  jq -e '.config.ingress[:-1] | all(has("hostname") and has("service"))' "$FILE" >/dev/null \
    || fail "除了最後一條，每條都要有 hostname 與 service"

  local dup
  dup=$(jq -r '[.config.ingress[] | select(has("hostname")) | (.hostname + (.path // ""))] | group_by(.) | map(select(length > 1) | .[0]) | .[]' "$FILE")
  [ -z "$dup" ] || fail "重複的 hostname/path：$dup"

  # web 沒有登入，Access 是唯一防線；每個對外 hostname 都要在 cloudflared 這層驗 Access JWT（見 deploy/README.md 步驟 2）
  local open
  open=$(jq -r '.config.ingress[] | select(has("hostname")) | select((.originRequest.access.required // false) != true) | .hostname' "$FILE")
  [ -z "$open" ] || fail "這些 hostname 沒開 Enforce Access JWT validation（originRequest.access.required）：$open"

  echo "check ok: $(jq '[.config.ingress[] | select(has("hostname"))] | length' "$FILE") 個 hostname"
}

cmd_diff() {
  local out
  if out=$(diff -u --label live --label "$FILE" <(live) <(jq -S '.config' "$FILE")); then
    echo "no change"
  else
    echo "$out"
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
      printf '### Tunnel 設定差異\n\n```diff\n%s\n```\n' "$out" >> "$GITHUB_STEP_SUMMARY"
    fi
  fi
}

cmd_apply() {
  cmd_check
  local body
  body=$(jq -c '{config: .config}' "$FILE")
  cf PUT "$(tunnel_path)" --data "$body" | jq -e '.success' >/dev/null || fail "更新 Tunnel 設定失敗"
  echo "tunnel config applied"

  local target="${CF_TUNNEL_ID}.cfargotunnel.com" h rec
  for h in $(jq -r '.config.ingress[].hostname // empty' "$FILE" | sort -u); do
    rec=$(cf GET "/zones/${CF_ZONE_ID:?}/dns_records?name=$h" | jq -c '.result[0] // empty')
    if [ -z "$rec" ]; then
      cf POST "/zones/$CF_ZONE_ID/dns_records" \
        --data "$(jq -nc --arg n "$h" --arg c "$target" '{type: "CNAME", name: $n, content: $c, proxied: true}')" \
        | jq -e '.success' >/dev/null || fail "建立 DNS $h 失敗"
      echo "DNS created: $h -> $target"
    elif [ "$(jq -r '.content' <<<"$rec")" = "$target" ]; then
      echo "DNS ok: $h"
    else
      fail "DNS $h 已存在但指向 $(jq -r '"\(.type) \(.content)"' <<<"$rec")，不是這個 Tunnel；請手動處理"
    fi
  done
}

case "${1:-}" in
  export) cmd_export ;;
  check)  cmd_check ;;
  diff)   cmd_diff ;;
  apply)  cmd_apply ;;
  *) echo "usage: $0 export|check|diff|apply" >&2; exit 2 ;;
esac
