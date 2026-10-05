# agent

核心：抓取 → 整理 → Claude 寫報告。唯一的第三方依賴 `claude-agent-sdk` 在這裡；依賴 [shared](../shared/README.md)，**不認得 email**（email 與寄信在 [notify](../notify/README.md)）。
專案全貌見[根目錄 README](../README.md)，已知問題見 [ISSUES.md](ISSUES.md)。

```
src/newsletter_agent/
  sources.py             來源載入層：掃 sources.d、驗證欄位、去重、處理停用
  fetch.py               抓取層：RSS 2.0 / Atom / arXiv API（零第三方依賴）
  curate.py              整理層：時間窗 → 跨日去重 → 近似標題合併 → 關鍵字+新鮮度評分
  report.py              輸出層：模板版 Markdown（無 LLM 保底）
  run.py                 入口 CLI（newsletter-fetch）
  agent_run.py           無人值守層：Claude Agent SDK 跑 news-digest skill，記錄用量、驗收產出（newsletter-agent）
```

設定在 repo 根的 `config/`（`interests.md`、`config.json`、`sources.d/*.json`），報告撰寫的 skill 在 `.claude/skills/news-digest/`。

分工原則：**確定性的部分交給 Python，判斷性的部分交給 Claude。**
抓取、去重、排序、arXiv 會議判斷全部可重現；挑選重點與條列改寫由 Claude
依 `news-digest` skill 讀 `data/curated/<date>.json` 後改寫 `reports/<date>.md`。

## 使用

```bash
uv run newsletter-fetch --dry-run     # 只測來源連通性
uv run newsletter-fetch               # 完整跑一次（含模板版報告）
uv run newsletter-fetch --no-report   # 只產 curated JSON，報告留給 Claude 寫
uv run newsletter-fetch --lookback 72 # 放寬時間窗到 72 小時
uv run newsletter-fetch --list-sources   # 不連網，列出來源載入結果與停用項目
uv run newsletter-agent               # 依 skill 寫報告並驗收，見下方「無人值守」
```

agent（`agent_run.py`，或在對話裡手動跑 skill）只負責寫 `reports/<date>.md` 並驗證格式，**不產 email HTML、也不寄信**；
要 email 就用 notify（`uv run newsletter-render | uv run newsletter-send`，排程由根目錄的 `run_daily.sh` 代勞）。

## 調整來源

來源不寫在程式碼裡，全部在 `config/sources.d/`，一個主題一個 JSON 檔。
新增主題＝新增一個檔，不必改任何程式碼或主設定。

```json
{
  "topic": "ai-research",
  "label": "AI / 研究論文動態",
  "enabled": true,
  "defaults": { "weight": 1.0 },
  "feeds": [
    { "name": "arXiv cs.CV", "type": "arxiv", "query": "cat:cs.CV" },
    { "name": "Hugging Face Blog", "type": "rss", "url": "https://huggingface.co/blog/feed.xml" },
    { "name": "先關起來的來源", "type": "rss", "url": "https://...", "enabled": false, "weight": 0.6 }
  ]
}
```

- `type: "rss"` 需要 `url`；`type: "arxiv"` 需要 `query`（如 `cat:cs.CV`、`all:"knowledge graph"`）
- `defaults` 套用到本檔所有 feed，個別 feed 可覆寫
- `enabled: false` 放在檔案層＝整個主題停用，放在 feed 層＝單一來源停用
- 檔名以 `_` 開頭的會被當草稿略過
- 同一個 url / arxiv query 重複出現時只留第一個，並印出提示

設定寫錯不會等到抓一半才爆：`sources.py` 在連網前就檢查 name / type / url / query /
weight，錯誤訊息會指出是哪個檔的哪個來源，程式以 exit code 2 結束。

## 評分與配額

每則有兩個分數，這是整個 curate 層最重要的設計：

| 值 | 組成 | 用途 |
| --- | --- | --- |
| `relevance` | 來源保底分 ＋ 關鍵字命中 | **門檻**，低於 `min_relevance` 一律不收 |
| `rank` | `relevance` × 來源 `weight` ＋ 新鮮度 | **只決定排序**，不影響是否收錄 |

關鍵字權重：`boost_high` +2.0、`boost_mid` +0.8、`boost_low` +0.4、`penalize` −4.0。
新鮮度為指數衰減，半衰期 12 小時，最高 +1.5。

早期版本只有單一分數，結果來源權重加新鮮度就超過門檻，關鍵字形同虛設——只要夠新，
「iPhone 霧面貼」也會被收錄。**分數拆開之後，「夠新」永遠無法讓不相關的東西進來。**

英文關鍵字用詞界比對（`agent` 不會命中 `federal agents`），中文用子字串。

改 `interests.md` 時順手看一次 `keywords`——前者是判讀用的自然語言，後者是評分用的
機械版本，兩邊脫節就會出現「分數很高但你根本不在乎」的項目。

### 時間窗依來源而定

`lookback_hours` 可寫在主題檔的 `defaults` 或個別 feed。理由：arXiv 週末不公告，
最新論文往往是 36 小時前；Hugging Face Blog 一週才發幾篇。跟每天產 40 則的新聞站
共用 24 小時的窗，研究類來源永遠進不來。目前設定：

| 主題 | 窗 | 例外 |
| --- | --- | --- |
| ai-research | 96h | HF Blog / Google Research 240h |
| ai-industry | 240h | — |
| semiconductor | 48h | — |
| tech-industry | 24h | IEEE Spectrum 96h |
| taiwan-tech | 24h | — |

### 各主題配額

`config.json` 的 `quota` 決定每個主題最多收幾則，沒列到的用 `default_quota`。
高產量來源（TechNews 一天 40 則）會把研究類主題整個擠掉，不是因為更相關，只是因為量大。

### 來源保底分

`baseline_relevance` 寫在主題檔，給「則則值得看但標題常沒有技術關鍵字」的來源。
官方公告叫〈Introducing X〉，一個關鍵字都沒有，但不該漏掉。目前 ai-industry 給 2.0，
Hugging Face / Google Research 給 1.2。數量由配額控制。

### arXiv 會議／期刊判斷

論文的會議／期刊接受資訊從 API 的 Comments / Journal-Ref 解析，清單在 `config.json` 的 `arxiv_venues`（conference / journal / minor_tracks）；
主會議或期刊 +2.0、workshop 等次級 track +0.8、投稿中 +0.4，結果連同中文 `label` 寫進 curated JSON 的 `venue`。
自我檢查：`uv run python -m newsletter_agent.curate`。

## 無人值守（Claude Agent SDK）

不開 Claude app 也能跑完整流程：`agent_run`（`uv run newsletter-agent`）用 Claude Agent SDK 呼叫**同一份**
`.claude/skills/news-digest/SKILL.md`，與在對話裡打 `/news-digest` 並存、結果一致。

```bash
uv sync                                   # 依 uv.lock 建立 .venv 並裝好依賴（uv run 也會自動做）
uv run newsletter-web                        # 第一次：開 http://127.0.0.1:8787/auth 貼上 OAuth token（見下方「認證」）
uv run newsletter-agent                  # 依 skill 寫報告並驗收（有更新、格式正確），約數分鐘
uv run newsletter-agent --max-turns 80   # 預設 60 回合，超過就中止並視為失敗
uv run newsletter-render | uv run newsletter-send   # 報告寫好之後：轉成 email 並寄出（notify）
uv run newsletter-agent --auth-check     # 只驗證 token（一次最小的呼叫），不跑晨報
```

### 認證：只用 OAuth（訂閱額度），不使用 API key

**刻意不支援 `ANTHROPIC_API_KEY`**：這個專案走 Claude 訂閱額度，不想額外付費。環境裡就算有 key 也不會被使用。

1. 在**自己的電腦**執行 `claude setup-token`，在瀏覽器完成授權；它會印出效期一年的 token（CLI 不會幫你存）。
   需要 Pro / Max / Team / Enterprise 方案。
2. 開 `http://127.0.0.1:8787/auth`（`uv run newsletter-web`），把 token 貼上送出。伺服器會先用它實際呼叫一次 Claude
   （極小的請求）驗證，**通過才儲存**到 `state/oauth_token.json`（權限 600、不進版控）；貼錯的 token 不會蓋掉原本可用的。
   頁面也顯示授權狀態與預計到期日（以一年效期推算，剩 30 天內會提醒），並可「測試連線」或「刪除已存的 token」
   （刪除只移除本機檔案，token 在 Anthropic 端仍然有效）。`/auth` 的使用限制見 [web/README.md](../web/README.md)。
3. 不想經過網頁（例如遠端 host 沒開網頁）：直接在執行 `agent_run.py` 的環境設 `CLAUDE_CODE_OAUTH_TOKEN`。

- **token 來源的優先序**：`state/oauth_token.json`（頁面存的；`NEWSLETTER_TOKEN_FILE` 可改路徑）→ 環境變數
  `CLAUDE_CODE_OAUTH_TOKEN`。**都沒有就直接 exit 2**，不會退回本機 `claude` 的登入，也不會用 API key。
  stderr 會印這次用的來源（`stored-token` / `env-token`）。儲存與解析由 [shared](../shared/README.md) 的 `auth_store.py` 負責。
- **為什麼要移除環境變數**：claude CLI 自己的優先順序是 `ANTHROPIC_API_KEY` 高於 OAuth token，非互動模式只要有 key
  就用 key。所以 `agent_run.py` 啟動時會把排在 OAuth 前面的來源（`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、
  `CLAUDE_CODE_USE_BEDROCK` / `_VERTEX` / `_FOUNDRY`）從環境中移除，並在 stderr 說明。
- **額度用完不會備援**：訂閱有使用額度，用完時晨報以 exit 6 失敗，不會自動換成 API key。
  若帳號開了「額外用量」（[extra usage](https://support.claude.com/en/articles/12429409-extra-usage-for-paid-claude-plans)）
  之類的自動付費設定，額度用完後仍可能產生費用，請到帳號設定確認。
- **token 保護**：token 傳給驗證子程序時走環境變數、不上命令列，不回傳給瀏覽器（最多顯示尾 4 碼）、不寫進 log。
  注意 token 在 agent（claude CLI）的環境裡，agent 用 Bash 跑的指令讀得到它；
  Claude Code 有 `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` 可以擋，但它要求系統有 bubblewrap，沒有就整個 CLI 啟動失敗，
  所以預設沒開；要用請先裝 bubblewrap 再自己在環境設這個變數。
- **範圍**：只載入專案層級的 skill（`setting_sources=["project"]`），不吃使用者層級的同名 skill；預先允許
  `Skill / Bash / Read / Write / Edit / WebFetch / WebSearch`，其餘工具一律拒絕（不會卡在沒人回答的提示）。

### exit code

失敗會以非 0 結束，cron 看得到：

| exit | 意思 |
| --- | --- |
| 0 | 成功 |
| 1 | agent 失敗：API 錯誤、超過 `--max-turns`、SDK 例外 |
| 2 | 環境問題：沒裝 `claude-agent-sdk`，或沒有可用的 OAuth token |
| 3 | 報告沒產出：`reports/<date>.md` 沒在這次執行更新，或當天 curated 沒有收錄項目（抓取全失敗） |
| 4 | 報告格式不符：shared 的 `parse_report` 拒絕（缺 `subject` 註解或今日頭條），原因在 stderr |
| 5 | **授權失敗**：token 無效或已過期，到 `/auth` 重新貼上新的 token |
| 6 | **額度用完**：訂閱的使用額度或帳務問題；不會改用別的認證，等額度恢復再跑 |

### 用量與成本

結束時從 SDK 的結果取 token 與 `total_cost_usd`，寫成 `stage: claude`、`runner: sdk` 的紀錄
（label 沿用 `NEWSLETTER_RUN_LABEL`）。和其他 stage 一樣，**要開 debug 才會寫**：`NEWSLETTER_DEBUG=1`。
stderr 的 `[agent]` 摘要行（含估算費用 `est_usd`）不受 debug 影響，一定會進 log。`summary` 的 `via` 欄分辨路線（`sdk` / `chat`），
`usd` 欄只有 `sdk` 路線有值。**走訂閱額度時 `total_cost_usd` 只是 SDK 依牌價估的 API 等價費用，不是實際扣款**
（[官方說明](https://code.claude.com/docs/en/agent-sdk/cost-tracking)：client-side estimate），拿來比較每次執行的相對用量即可。
跑幾天後用它調整 `--max-turns`，並看看一次晨報吃掉多少訂閱額度：

```bash
NEWSLETTER_DEBUG=1 uv run newsletter-agent
uv run newsletter-metrics summary 14
```

用 `agent_run.py` 時，skill 裡的 `newsletter-metrics claude` 會自動略過（`NEWSLETTER_RUNNER=sdk`），避免和 SDK 的用量重複記錄。
量測的細節見 [shared/README.md](../shared/README.md)「量測」。

## 自我檢查

```bash
uv run python -m newsletter_agent.curate
uv run python -m newsletter_agent.fetch
uv run python -m newsletter_agent.agent_run --selftest   # 需要裝 SDK，CI 不跑
```
