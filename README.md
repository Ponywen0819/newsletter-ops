# newsletter-ops

每日新聞自動蒐集 → 整理 → 報告的 pipeline。主題：AI / 研究論文動態 + 科技產業與市場。

## 架構

```
ISSUES.md                  已知問題與限制（先看這裡）
config/interests.md        關注範圍（自然語言），報告判讀的依據
config/config.json         執行參數：時間窗、關鍵字權重、收錄門檻、HTTP 設定
config/sources.d/*.json    來源清單，一個主題一個檔
src/sources.py             來源載入層：掃 sources.d、驗證欄位、去重、處理停用
src/fetch.py               抓取層：RSS 2.0 / Atom / arXiv API（零第三方依賴）
src/curate.py              整理層：時間窗 → 跨日去重 → 近似標題合併 → 關鍵字+新鮮度評分
src/report.py              輸出層：模板版 Markdown（無 LLM 保底）
src/metrics.py             量測層：debug 開啟時記錄各階段耗時與 Claude token 用量
src/render_email.py        email 層：條列版報告 Markdown → inline-CSS HTML（reports/<date>.html）
src/send_email.py          寄信層：Gmail SMTP 寄出 email HTML（不依賴 Claude 的 Gmail connector）
src/agent_run.py           無人值守層：Claude Agent SDK 跑 news-digest skill，記錄用量、驗收產出（唯一的第三方依賴）
src/auth_store.py          認證層：OAuth token 的儲存與來源解析，agent_run.py 與 web.py 共用（stdlib）
src/feedback.py            回饋層：從報告收集人工標記
src/report_data.py         報告資料層：報告 Markdown → 結構化 JSON（與 render_email.py 認同一套格式），給網頁用
src/web.py                 Web 後端：JSON API（/api/*）＋提供 web/dist；👍／👎 寫進 feedback.jsonl；/auth 貼 OAuth token（stdlib，無登入）
web/                       Web 前端：Vite + React + TypeScript（晨報、歷史列表、/auth）；建置產物 web/dist 不進版控
src/run.py                 入口 CLI
run_daily.sh               cron 包裝
pyproject.toml, uv.lock    Python 版本與依賴，由 uv 管理（.python-version 固定直譯器版本）
data/raw/<date>.jsonl      當日原始抓取（append，供回溯）
data/curated/<date>.json   排序後的收錄清單 ← SKILL 的輸入
reports/<date>.md          最終報告
state/seen.json            30 天去重記憶
state/feedback.jsonl       累積的人工標記，供日後調整關鍵字
state/oauth_token.json     /auth 頁面存的 OAuth token（權限 600，不進版控；見「無人值守」）
logs/<YYYY-MM>.log         cron 執行紀錄
.claude/skills/news-digest/  報告撰寫的 skill，隨專案進版控
```

分工原則：**確定性的部分交給 Python，判斷性的部分交給 Claude。**
抓取、去重、排序、arXiv 會議判斷、HTML 版型全部可重現；挑選重點與條列改寫由 Claude
依 `news-digest` skill 讀 curated JSON 後改寫 `reports/<date>.md`。

## 環境（uv）

Python 版本與依賴由 [uv](https://docs.astral.sh/uv/) 管理：`.python-version` 固定直譯器（3.11）、`uv.lock` 鎖定依賴。
安裝 uv 後不必自己建 venv，`uv run` 第一次執行會自動建立 `.venv` 並裝好依賴；沒有該版本的 Python 時 uv 會自動下載。

```bash
uv sync               # 依 uv.lock 同步環境
uv add <套件>         # 新增依賴（會更新 pyproject.toml 與 uv.lock，兩個檔案一起 commit）
uv lock --upgrade     # 升級鎖定的版本
```

抓取、整理、寄信都只用標準庫，只有 `src/agent_run.py` 需要第三方套件（`claude-agent-sdk`）。
skill 裡由 agent 呼叫的 `run.py`、`render_email.py` 等因此直接用 `python3`，不依賴 uv 環境。

## 使用

```bash
uv run src/run.py --dry-run     # 只測來源連通性
uv run src/run.py               # 完整跑一次（含模板版報告）
uv run src/run.py --no-report   # 只產 curated JSON，報告留給 Claude 寫
uv run src/run.py --lookback 72 # 放寬時間窗到 72 小時
uv run src/feedback.py          # 收集報告裡填的標記
(cd web && npm install && npm run build)   # 第一次（以及改了前端之後）：建置網頁前端，見「Web 前端」
uv run src/web.py               # 晨報網頁，預設 http://127.0.0.1:8787（--port / NEWSLETTER_WEB_PORT 可改）
uv run src/render_email.py | uv run src/send_email.py   # 寄出當日 email
```

`send_email.py` 讀環境變數 `GMAIL_USER`、`GMAIL_APP_PASSWORD`（Google 帳號的應用程式密碼，需先開兩步驟驗證）、
`NEWSLETTER_MAIL_TO`（逗號分隔，沒設就寄給自己）。加 `--dry-run` 只印標頭不寄。

## 讓它跟著你的研究重心走

興趣會變，但系統不會自己察覺——報告每天照常產出，你只是慢慢覺得「最近好像沒什麼有趣的」。
所以分兩條路處理：

**突變**（換題目、接新案子）→ 改 `config/interests.md`，用自然語言寫「核心 / 關注 /
背景 / 不要」四級，並更新檔頂的 `updated:` 日期。`news-digest` skill 每次寫報告前都讀它，
skill 本身不存任何興趣清單。超過 90 天沒更新時，`run.py` 每次執行都會在 stderr 提醒一行。

**漸變**（慢慢偏移，自己察覺不到）→ 靠標記累積資料。報告每則末尾有一行：

```markdown
<!-- mark:    uid=3f9a1c2b0d4e5678 -->
```

看完隨手填 `+`（有用）、`-`（沒用）、`++` / `--`（強烈），Markdown 預覽時不會顯示。
跑 `uv run src/feedback.py` 收集到 `state/feedback.jsonl`，重複標記以最新為準。
報告裡的標記只匯入 `feedback.jsonl` 還沒有紀錄的那一則；已有紀錄的（含網頁標的）以 jsonl 為準，不會被覆蓋。

### 用網頁標記（取代手改 Markdown）

```bash
(cd web && npm install && npm run build)   # 前端還沒建置過才需要；沒建置時頁面回 503 並提示這行
uv run src/web.py               # 開 http://127.0.0.1:8787/
uv run src/web.py --selftest    # API、靜態檔、寫入／覆蓋／取消的讀回、/auth 的本機限制
```

`/` 當日晨報、`/reports` 歷史列表、`/reports/<date>` 單日。每則末尾有 👍／👎，按下即 append 一行到
`state/feedback.jsonl`（欄位同 `feedback.py`，同一則以最後一筆為準），不必再跑 `feedback.py`。

- 只有兩級：👍 = `+`、👎 = `-`。再按一次同一顆＝取消（寫成 `mark: ""`），按另一顆＝覆蓋。
- 頁面的標記狀態只看 `feedback.jsonl`；還留在 Markdown 裡、尚未用 `feedback.py` 收集的標記不會顯示，先跑一次 `feedback.py` 匯入即可。
- 網頁與 `feedback.py` 可以同時跑：兩邊都只 append、不改寫舊內容，並用 `state/feedback.jsonl.lock` 排隊。
  `uv run src/feedback.py --selftest` 涵蓋這部分（含併發 append）。
- **沒有登入**：預設只 bind `127.0.0.1`，要對外請放在 Cloudflare Tunnel + Access 後面，不要改 `--host`。
- `POST /api/feedback` 只收 `Content-Type: application/json`，body 是 `{"uid": "...", "mark": "+" | "-" | ""}`。
  （改版前是 `POST /feedback`；若有外部腳本或 Cloudflare Access 規則寫死舊路徑，要跟著改。）
- `/auth`（貼 OAuth token）**只服務本機**，經 Tunnel 進來的一律 404，見下一節。

### Web 前端（`web/`）

Vite + React + TypeScript。後端 `src/web.py` 只出 JSON，頁面全由前端畫；晨報不再是後端組好的 HTML，
而是 `report_data.py` 解析出的結構（標題、段落、巢狀清單、每則的 mark、資料來源），前端依結構排版。
版面沿用 email 版型（灰底、640px 白卡片），email 本身仍由 `render_email.py` 產生、不受影響。

```bash
cd web
npm install          # 第一次；需要 Node ^20.19 或 >=22.12
npm run build        # 型別檢查 + 建置到 web/dist，src/web.py 直接提供
npm run dev          # 開發：Vite 在 :5173，/api 代理到 src/web.py（需另外用 uv run src/web.py 開後端）
npm test             # Vitest + Testing Library：元件與路由
npm run typecheck
```

- 路由：`/` 當日、`/reports` 歷史、`/reports/<date>` 單日、`/auth` 授權（只限本機）。後端對這幾條回 `index.html`，
  其他不認得的路徑回 404 的 `index.html`（前端畫「找不到頁面」）。新增前端路由時，`src/web.py` 的 `SPA_ROUTES` 要同步。
- API（細節見 `src/web.py` 開頭的說明、型別見 `web/src/types.ts`）：`GET /api/session`、`/api/today`、`/api/reports`、
  `/api/reports/<date>`、`/api/auth`；`POST /api/feedback`、`/api/auth/token|test|revoke`。
- 開發時 Vite 的代理不改 `Host`、不加 `X-Forwarded-*`，所以後端仍把它當本機，`/auth` 可以正常測。後端埠號不是 8787 時，
  前端用同一個環境變數：`NEWSLETTER_WEB_PORT=8790 npm run dev`。
- HTML 回應帶 `Content-Security-Policy`（只許同源的腳本與樣式），所以前端不能有行內 `<script>`／`style="…"`。
- 報告格式（`SKILL.md` 規定的 Markdown 子集）有改動時，`render_email.py`（email）與 `report_data.py`（網頁）兩邊要一起改；
  `python3 src/report_data.py --selftest` 會拿同一份 Markdown 對照兩邊的解析結果。

累積兩三個月後可以看出：收錄很多卻從未拿到 `+` 的關鍵字該降權、`+` 項目裡反覆出現卻
不在 boost 清單的詞該加進去、長期沒命中的關鍵字該移除。

**建議由人確認，不自動套用。** 會自己調參數的系統，出錯時你查不出它為什麼開始推垃圾。

## cron

```cron
0 8 * * * /Users/pony/project/newssletter-ops/run_daily.sh --no-report
```

`run_daily.sh` 內部用 `uv run --locked` 執行，並替 cron 精簡的 PATH 補上 uv 常見的安裝位置（`~/.local/bin`、`~/.cargo/bin`、Homebrew）。`--locked` 讓鎖檔與 `pyproject.toml` 對不上時直接失敗，不會在排程裡自己改鎖檔。

macOS 的 cron 需要「完整磁碟取用權」，或改用 launchd。抓完之後在 Claude 對話中
執行 `/news-digest`，讀當日 curated JSON 寫出條列版晨間簡報，並用 `render_email.py` 產出 email HTML。

arXiv 論文的會議／期刊接受資訊從 API 的 Comments / Journal-Ref 解析，清單在 `config.json` 的 `arxiv_venues`（conference / journal / minor_tracks）；主會議或期刊 +2.0、workshop 等次級 track +0.8、投稿中 +0.4，結果連同中文 `label` 寫進 curated JSON 的 `venue`，自我檢查：`uv run src/curate.py`、`uv run src/render_email.py --selftest`。

## 無人值守（Claude Agent SDK）

不開 Claude app 也能跑完整流程：`src/agent_run.py` 用 Claude Agent SDK 呼叫**同一份**
`.claude/skills/news-digest/SKILL.md`，與在對話裡打 `/news-digest` 並存、結果一致。

```bash
uv sync                                   # 依 uv.lock 建立 .venv 並裝好依賴（uv run 也會自動做）
uv run src/web.py                        # 第一次：開 http://127.0.0.1:8787/auth 貼上 OAuth token（見下方「認證」）
uv run src/agent_run.py                  # 抓取 → 寫報告 → render_email.py，約數分鐘
uv run src/agent_run.py --max-turns 80   # 預設 60 回合，超過就中止並視為失敗
uv run src/agent_run.py | uv run src/send_email.py   # stdout 是 render_email.py 的那行 JSON，可直接寄信
uv run src/agent_run.py --auth-check     # 只驗證 token（一次最小的呼叫），不跑晨報
```

### 認證：只用 OAuth（訂閱額度），不使用 API key

**刻意不支援 `ANTHROPIC_API_KEY`**：這個專案走 Claude 訂閱額度，不想額外付費。環境裡就算有 key 也不會被使用。

1. 在**自己的電腦**執行 `claude setup-token`，在瀏覽器完成授權；它會印出效期一年的 token（CLI 不會幫你存）。
   需要 Pro / Max / Team / Enterprise 方案。
2. 開 `http://127.0.0.1:8787/auth`（`uv run src/web.py`），把 token 貼上送出。伺服器會先用它實際呼叫一次 Claude
   （極小的請求）驗證，**通過才儲存**到 `state/oauth_token.json`（權限 600、不進版控）；貼錯的 token 不會蓋掉原本可用的。
   頁面也顯示授權狀態與預計到期日（以一年效期推算，剩 30 天內會提醒），並可「測試連線」或「刪除已存的 token」
   （刪除只移除本機檔案，token 在 Anthropic 端仍然有效）。
3. 不想經過網頁（例如遠端 host 沒開網頁）：直接在執行 `agent_run.py` 的環境設 `CLAUDE_CODE_OAUTH_TOKEN`。

- **token 來源的優先序**：`state/oauth_token.json`（頁面存的；`NEWSLETTER_TOKEN_FILE` 可改路徑）→ 環境變數
  `CLAUDE_CODE_OAUTH_TOKEN`。**都沒有就直接 exit 2**，不會退回本機 `claude` 的登入，也不會用 API key。
  stderr 會印這次用的來源（`stored-token` / `env-token`）。
- **為什麼要移除環境變數**：claude CLI 自己的優先順序是 `ANTHROPIC_API_KEY` 高於 OAuth token，非互動模式只要有 key
  就用 key。所以 `agent_run.py` 啟動時會把排在 OAuth 前面的來源（`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、
  `CLAUDE_CODE_USE_BEDROCK` / `_VERTEX` / `_FOUNDRY`）從環境中移除，並在 stderr 說明。
- **額度用完不會備援**：訂閱有使用額度，用完時晨報以 exit 6 失敗，不會自動換成 API key。
  若帳號開了「額外用量」（[extra usage](https://support.claude.com/en/articles/12429409-extra-usage-for-paid-claude-plans)）
  之類的自動付費設定，額度用完後仍可能產生費用，請到帳號設定確認。
- **token 保護**：token 傳給驗證子程序時走環境變數、不上命令列，不回傳給瀏覽器（最多顯示尾 4 碼）、不寫進 log。
  注意 token 在 agent（claude CLI）的環境裡，agent 用 Bash 跑的指令讀得到它（以前的 API key 也一樣）；
  Claude Code 有 `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` 可以擋，但它要求系統有 bubblewrap，沒有就整個 CLI 啟動失敗，
  所以預設沒開；要用請先裝 bubblewrap 再自己在環境設這個變數。
- **`/auth` 只能從本機開**：能寫入憑證，而 `web.py` 沒有登入。`cloudflared` 跑在同一台機器、以 `127.0.0.1` 連進來，
  所以光看來源位址擋不住 Tunnel；要同時符合「來源是 loopback」「`Host` 是 `127.0.0.1` / `localhost` / `[::1]`」
  「沒有 `Cf-*` / `X-Forwarded-*` 等代理標頭」「`Origin`（若有）是本機」，否則回 404。
  遠端 host 上要貼 token：`ssh -L 8787:127.0.0.1:8787 <host>`，再用自己電腦的瀏覽器開 `http://localhost:8787/auth`。
- 驗證 token 需要 SDK，請用 `uv run src/web.py` 啟動（用 `python3 src/web.py` 時其他頁面照常，只有貼 token 會提示缺 SDK）。
- **範圍**：只載入專案層級的 skill（`setting_sources=["project"]`），不吃使用者層級的同名 skill；預先允許
  `Skill / Bash / Read / Write / Edit / WebFetch / WebSearch`，其餘工具一律拒絕（不會卡在沒人回答的提示）。
- **失敗會以非 0 結束**，cron 看得到：

  | exit | 意思 |
  | --- | --- |
  | 0 | 成功 |
  | 1 | agent 失敗：API 錯誤、超過 `--max-turns`、SDK 例外 |
  | 2 | 環境問題：沒裝 `claude-agent-sdk`，或沒有可用的 OAuth token |
  | 3 | 報告沒產出：`reports/<date>.md` 沒在這次執行更新，或當天 curated 沒有收錄項目（抓取全失敗） |
  | 4 | `render_email.py` 失敗（報告格式不符） |
  | 5 | **授權失敗**：token 無效或已過期，到 `/auth` 重新貼上新的 token |
  | 6 | **額度用完**：訂閱的使用額度或帳務問題；不會改用別的認證，等額度恢復再跑 |

- **用量與成本**：結束時從 SDK 的結果取 token 與 `total_cost_usd`，寫成 `stage: claude`、`runner: sdk` 的紀錄
  （label 沿用 `NEWSLETTER_RUN_LABEL`）。和其他 stage 一樣，**要開 debug 才會寫**：`NEWSLETTER_DEBUG=1`。
  stderr 的 `[agent]` 摘要行（含估算費用 `est_usd`）不受 debug 影響，一定會進 log。`summary` 的 `via` 欄分辨路線（`sdk` / `chat`），
  `usd` 欄只有 `sdk` 路線有值。**走訂閱額度時 `total_cost_usd` 只是 SDK 依牌價估的 API 等價費用，不是實際扣款**
  （[官方說明](https://code.claude.com/docs/en/agent-sdk/cost-tracking)：client-side estimate），拿來比較每次執行的相對用量即可。
  跑幾天後用它調整 `--max-turns`，並看看一次晨報吃掉多少訂閱額度：

  ```bash
  NEWSLETTER_DEBUG=1 uv run src/agent_run.py
  uv run src/metrics.py summary 14
  ```

- 用 `agent_run.py` 時，skill 裡的 `metrics.py claude` 會自動略過（`NEWSLETTER_RUNNER=sdk`），避免和 SDK 的用量重複記錄。
- 自我檢查：`uv run src/agent_run.py --selftest`、`python3 src/auth_store.py --selftest`、`uv run src/web.py --selftest`、
  `python3 src/report_data.py --selftest`；前端 `cd web && npm test`。

## 量測（debug）

`config.json` 設 `"debug": true`（或臨時用 `NEWSLETTER_DEBUG=1 uv run src/run.py ...`），每個階段會寫一行到
`logs/metrics/<date>.jsonl`：`fetch_source`（每個來源的耗時／則數／錯誤）、`fetch`、`curate`、`render`，
以及 news-digest 跑完後由 `python3 src/metrics.py claude`（skill 內呼叫）從 Claude Code session 紀錄統計的 token 與工具耗時。

```bash
uv run src/metrics.py summary 14         # 最近 14 天，每次正式執行一行
uv run src/metrics.py summary 14 --all   # 連測試執行一起列
NEWSLETTER_DEBUG=1 NEWSLETTER_RUN_LABEL=test uv run src/run.py --no-report   # 測試執行，紀錄標 test
```

- 紀錄一律保留，不要刪；測試用 `NEWSLETTER_RUN_LABEL=test` 區分，預設是 `prod`。
- 每遇到一筆 `fetch` 就算新的一次執行，之後的 curate／render／claude 歸到這一次。
- token 只含主 session 的 API 回合；WebFetch 內部用的小模型不在紀錄裡，所以不計。
- `non_tool_seconds` 是總時間扣掉工具時間，含模型生成、串流與排隊。

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

```bash
uv run src/run.py --list-sources   # 不連網，列出載入結果與停用項目
```

改 `interests.md` 時順手看一次 `keywords`——前者是判讀用的自然語言，後者是評分用的
機械版本，兩邊脫節就會出現「分數很高但你根本不在乎」的項目。

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

```bash
uv run src/run.py --list-sources   # 不連網，列出載入結果與停用項目
```

改 `interests.md` 時順手看一次 `keywords`——前者是判讀用的自然語言，後者是評分用的
機械版本，兩邊脫節就會出現「分數很高但你根本不在乎」的項目。

評分參數仍在 `config/config.json`：`boost_high` +2.0、`boost_mid` +0.8、
`penalize` -4.0，再加新鮮度加權（半衰期 12 小時，最高 +1.5）。低於 `min_score` 不收錄。
