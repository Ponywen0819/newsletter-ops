# llm

用 Claude Agent SDK 跑 agent 的共用執行器：OAuth 認證、只開指定工具的執行、失敗分類、用量紀錄。
唯一的第三方依賴 `claude-agent-sdk` 在這裡；只依賴 [shared](../shared/README.md)。
使用者是 [collect](../collect/README.md) 的搜尋與 [writer](../writer/README.md)；[web-server](../../apps/web-server/README.md) 用它驗證 token。
專案全貌見[根目錄 README](../../README.md)。

```
src/newsletter_llm/
  runner.py              pick_auth（選認證、清環境）、run（跑一次 agent）、failure（失敗分類）、log_usage、--auth-check
```

```bash
uv run --project packages/llm newsletter-llm --auth-check     # 只驗證儲存的 token（一次最小的呼叫）
uv run --project packages/llm newsletter-llm --selftest
```

`--auth-check` 的 exit code：`0` 通過、`1` 其他失敗（含用了 API key）、`2` 沒有 token 或沒裝 SDK、`5` 授權失敗、`6` 額度用完。
呼叫端用 `failure()` 拿到一樣的分類，所以 writer 的 5／6 和這裡同義。

## 認證：只用 OAuth（訂閱額度），不使用 API key

**刻意不支援 `ANTHROPIC_API_KEY`**：這個專案走 Claude 訂閱額度，不想額外付費。環境裡就算有 key 也不會被使用。

1. 在**自己的電腦**執行 `claude setup-token`，在瀏覽器完成授權；它會印出效期一年的 token（CLI 不會幫你存）。
   需要 Pro / Max / Team / Enterprise 方案。
2. 本機直接跑或 systemd 部署：開 `http://127.0.0.1:8787/auth`（`uv run --project apps/web-server newsletter-web`），把 token 貼上送出。伺服器會先用它實際呼叫一次 Claude
   （極小的請求）驗證，**通過才儲存**到 `state/oauth_token.json`（權限 600、不進版控）；貼錯的 token 不會蓋掉原本可用的。
   頁面也顯示授權狀態與預計到期日（以一年效期推算，剩 30 天內會提醒），並可「測試連線」或「刪除已存的 token」
   （刪除只移除本機檔案，token 在 Anthropic 端仍然有效）。`/auth` 的使用限制見 [web-server/README.md](../../apps/web-server/README.md)。
3. Docker 部署：在 host 本機的瀏覽器開管理 port `http://127.0.0.1:8788/auth`（主 port 上的 `/auth` 開不了），見 [deploy/README.md](../../deploy/README.md)。

- **token 的來源只有 `state/oauth_token.json`**（頁面存的；`NEWSLETTER_TOKEN_FILE` 可改路徑）。**沒有就直接 exit 2**，
  不會退回本機 `claude` 的登入，也不會用 API key。儲存與讀取由 [shared](../shared/README.md) 的 `auth_store.py` 負責。
- **不讀環境變數 `CLAUDE_CODE_OAUTH_TOKEN`**：token 效期一年，換新時環境變數得重啟程序（Docker 要重新部署）才生效，
  而且 web 與排程的環境可能不同、`/auth` 的狀態會對不起來；存成檔案，換新立刻生效，兩邊看到同一份。環境裡還留著它時，執行器會在 stderr 提示已不再讀取。
- **為什麼要移除環境變數**：claude CLI 自己的優先順序是 `ANTHROPIC_API_KEY` 高於 OAuth token，非互動模式只要有 key
  就用 key。所以執行器啟動時會把排在 OAuth 前面的來源（`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、
  `CLAUDE_CODE_USE_BEDROCK` / `_VERTEX` / `_FOUNDRY`）從環境中移除，並在 stderr 說明。
- **確認 SDK 實際用哪種認證**：`uv run --project packages/llm newsletter-llm --auth-check` 印一行 JSON，`source` 是 token 來源（驗證儲存的 token 是 `stored-token`；`--token-from-env` 驗證候選 token 時是 `env-token`，web 貼上時用），
  `api_key_source` 是 claude CLI 在 init 訊息裡回報的 `apiKeySource`——`none` 表示這次呼叫沒有用 API key（走的是 OAuth token）。
  不是 `none`（例如 `ANTHROPIC_API_KEY`）就算失敗（`kind: api_key`，exit 1），即使呼叫本身成功也一樣：成功的呼叫分不出是 OAuth 還是悄悄走了 API key 計費。
  結果範例：`{"ok": true, "kind": "ok", "message": "驗證通過", "api_key_source": "none", "source": "stored-token"}`。
- **額度用完不會備援**：訂閱有使用額度，用完時寫報告以 exit 6 失敗（搜尋只警告），不會自動換成 API key。
  若帳號開了「額外用量」（[extra usage](https://support.claude.com/en/articles/12429409-extra-usage-for-paid-claude-plans)）
  之類的自動付費設定，額度用完後仍可能產生費用，請到帳號設定確認。
- **token 保護**：token 傳給驗證子程序時走環境變數、不上命令列，不回傳給瀏覽器（最多顯示尾 4 碼）、不寫進 log。
  注意 token 在 agent（claude CLI）的環境裡，agent 用 Bash 跑的指令讀得到它；
  Claude Code 有 `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` 可以擋，但它要求系統有 bubblewrap，沒有就整個 CLI 啟動失敗，
  所以預設沒開；要用請先裝 bubblewrap 再自己在環境設這個變數。
- **工具範圍**：`run()` 只開呼叫端指定的工具（`tools` 與 `allowed_tools` 同一份），其他工具 agent 看不到也用不了，
  沒預先允許的一律拒絕（`permission_mode="dontAsk"`，不會卡在沒人回答的提示）。有 Skill 才載入專案設定
  （`setting_sources=["project"]`，不吃使用者層級的同名 skill），否則什麼設定都不載入。

## 用量與成本

每次執行結束時從 SDK 的結果取 token 與 `total_cost_usd`，寫成 `runner: sdk` 的紀錄（stage 由呼叫端決定：writer 是 `claude`、搜尋是 `search`）
（label 沿用 `NEWSLETTER_RUN_LABEL`）。和其他 stage 一樣，**要開 debug 才會寫**：`NEWSLETTER_DEBUG=1`。
stderr 的 `[writer]`／`[search]` 摘要行（含估算費用 `est_usd`）不受 debug 影響，一定會進 log。`summary` 的 `via` 欄分辨路線（`sdk` / `chat`），
`usd` 欄只有 `sdk` 路線有值。**走訂閱額度時 `total_cost_usd` 只是 SDK 依牌價估的 API 等價費用，不是實際扣款**
（[官方說明](https://code.claude.com/docs/en/agent-sdk/cost-tracking)：client-side estimate），拿來比較每次執行的相對用量即可。
跑幾天後用它調整 `--max-turns`，並看看一次晨報吃掉多少訂閱額度：

```bash
NEWSLETTER_DEBUG=1 uv run --project packages/writer newsletter-write
uv run --project packages/shared newsletter-metrics summary 14
```

writer 跑 skill 時，skill 裡的 `newsletter-metrics claude` 會自動略過（`NEWSLETTER_RUNNER=sdk`），避免和 SDK 的用量重複記錄。
量測的細節見 [shared/README.md](../shared/README.md)「量測」。

