# newsletter-ops

每日新聞自動蒐集 → 整理 → 報告的 pipeline。主題：AI / 研究論文動態 + 科技產業與市場。

## 架構

monorepo，用 [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) 組成：每個模組是獨立的 Python 套件，
`uv sync` 把它們以 editable 裝進同一個環境。依賴方向由 `deploy/check_boundaries.py` 檢查（CI 也跑），違規的 import 會失敗：

```
shared ◄── agent ◄── web
   ▲
   └────── notify
```

`agent` 是核心（抓取 → 整理 → Claude 寫報告），**不認得 email**；`notify` 是外圍（報告 → email → 寄出），之後加別的通知管道放這裡；
`web` 依賴 `agent` 只為了驗證 OAuth token（以子程序呼叫 `newsletter-agent --auth-check`）。

```
shared/                    共用底層（標準庫）
  src/newsletter_shared/
    paths.py               repo 根目錄 ROOT 的唯一來源；其他模組一律從這裡取，不自己用 __file__ 推算
    report_data.py         報告 Markdown → 結構化資料：報告格式（SKILL.md 規定的子集）唯一的解析與驗證者，網頁與 email 共用
                           （newsletter-report-check 用它驗證 reports/<date>.md）
    feedback.py            回饋標記格式、跨程序檔案鎖、從報告收集人工標記（newsletter-feedback）
    metrics.py             量測層：debug 開啟時記錄各階段耗時與 Claude token 用量（newsletter-metrics）
    auth_store.py          OAuth token 的儲存與來源解析，agent 與 web 共用
agent/                     核心：抓取 → 整理 → Claude 寫報告（唯一的第三方依賴 claude-agent-sdk 在這裡）
  src/newsletter_agent/
    sources.py             來源載入層：掃 sources.d、驗證欄位、去重、處理停用
    fetch.py               抓取層：RSS 2.0 / Atom / arXiv API（零第三方依賴）
    curate.py              整理層：時間窗 → 跨日去重 → 近似標題合併 → 關鍵字+新鮮度評分
    report.py              輸出層：模板版 Markdown（無 LLM 保底）
    run.py                 入口 CLI（newsletter-fetch）
    agent_run.py           無人值守層：Claude Agent SDK 跑 news-digest skill，記錄用量、驗收產出（newsletter-agent）
notify/                    通知（標準庫）
  src/newsletter_notify/
    render_email.py        email 層：報告結構 → inline-CSS HTML（reports/<date>.html）（newsletter-render）
    send_email.py          寄信層：Gmail SMTP 寄出 email HTML，不依賴 Claude 的 Gmail connector（newsletter-send）
web/
  server/src/newsletter_web/web.py   Web 後端：JSON API（/api/*）＋提供 web/ui/dist；有用／沒用 寫進 feedback.jsonl；
                                     /auth 貼 OAuth token（標準庫，無登入）（newsletter-web）
  ui/                      Web 前端：Vite + React + TypeScript（晨報、歷史列表、/auth），可安裝成 PWA；建置產物 web/ui/dist 不進版控
config/interests.md        關注範圍（自然語言），報告判讀的依據
config/config.json         執行參數：時間窗、關鍵字權重、收錄門檻、HTTP 設定
config/sources.d/*.json    來源清單，一個主題一個檔
.claude/skills/news-digest/  報告撰寫的 skill，隨專案進版控
run_daily.sh               排程入口：載入 env 檔 → 抓取 → agent → render → send，失敗留 log、exit 非 0
Dockerfile, docker-compose.yml, docker/   容器部署：web + scheduler + cloudflared，生成物放 volume（見「部署到家用 host」）
deploy/                    不用 Docker 時的範本（systemd 單元、cloudflared 設定、env 範本）；check_boundaries.py 依賴方向檢查
pyproject.toml, uv.lock    workspace 的根（只列成員）與整個 workspace 的鎖檔；.python-version 固定直譯器版本
ISSUES.md                  已知問題與限制（先看這裡）

# 執行期生成物（都不進版控；位置都在 repo 根，Docker 裡是 volume）
data/raw/<date>.jsonl      當日原始抓取（append，供回溯）
data/curated/<date>.json   排序後的收錄清單 ← SKILL 的輸入
reports/<date>.md          最終報告
reports/<date>.html        email 版（newsletter-render 產生）
state/seen.json            30 天去重記憶
state/feedback.jsonl       累積的人工標記，供日後調整關鍵字與 interests.md
state/oauth_token.json     /auth 頁面存的 OAuth token（權限 600；見「無人值守」）
logs/<YYYY-MM>.log         排程執行紀錄
```

分工原則：**確定性的部分交給 Python，判斷性的部分交給 Claude。**
抓取、去重、排序、arXiv 會議判斷、HTML 版型全部可重現；挑選重點與條列改寫由 Claude
依 `news-digest` skill 讀 curated JSON 後改寫 `reports/<date>.md`。

## 環境（uv）

Python 版本與依賴由 [uv](https://docs.astral.sh/uv/) 管理：`.python-version` 固定直譯器（3.11）、`uv.lock` 鎖定依賴。
安裝 uv 後不必自己建 venv，`uv run` 第一次執行會自動建立 `.venv` 並裝好依賴；沒有該版本的 Python 時 uv 會自動下載。

```bash
uv sync               # 依 uv.lock 同步環境（所有成員一起裝）
uv add --package newsletter-agent <套件>   # 替某個成員新增依賴（改該成員的 pyproject.toml 與 uv.lock，一起 commit）
uv lock --upgrade     # 升級鎖定的版本
```

抓取、整理、寄信都只用標準庫，只有 `agent` 成員（`agent_run`）需要第三方套件（`claude-agent-sdk`）。
skill 裡由 agent 呼叫的指令都是 `uv run --locked …`，要在 repo 根目錄、有 uv 的環境下執行。

## 使用

```bash
uv run newsletter-fetch --dry-run     # 只測來源連通性
uv run newsletter-fetch               # 完整跑一次（含模板版報告）
uv run newsletter-fetch --no-report   # 只產 curated JSON，報告留給 Claude 寫
uv run newsletter-fetch --lookback 72 # 放寬時間窗到 72 小時
uv run newsletter-feedback      # 收集報告裡填的標記
(cd web/ui && npm install && npm run build)   # 第一次（以及改了前端之後）：建置網頁前端，見「Web 前端」
uv run newsletter-web               # 晨報網頁，預設 http://127.0.0.1:8787（--port / NEWSLETTER_WEB_PORT 可改）
uv run newsletter-render | uv run newsletter-send   # 寄出當日 email
```

agent（`agent_run.py`，或在對話裡手動跑 skill）只負責寫 `reports/<date>.md` 並驗證格式，**不產 email HTML、也不寄信**；
要 email 就跑上面那行（排程由 `run_daily.sh` 代勞）。

`send_email.py` 讀環境變數 `GMAIL_USER`、`GMAIL_APP_PASSWORD`（Google 帳號的應用程式密碼，需先開兩步驟驗證）、
`NEWSLETTER_MAIL_TO`（逗號分隔，沒設就寄給自己）。加 `--dry-run` 只印標頭不寄。

## 讓它跟著你的研究重心走

興趣會變，但系統不會自己察覺——報告每天照常產出，你只是慢慢覺得「最近好像沒什麼有趣的」。
所以分兩條路處理：

**突變**（換題目、接新案子）→ 改 `config/interests.md`，用自然語言寫「核心 / 關注 /
背景 / 不要」四級，並更新檔頂的 `updated:` 日期。`news-digest` skill 每次寫報告前都讀它，
skill 本身不存任何興趣清單。超過 90 天沒更新時，`newsletter-fetch` 每次執行都會在 stderr 提醒一行。

**漸變**（慢慢偏移，自己察覺不到）→ 靠標記累積資料。報告每則末尾有一行：

```markdown
<!-- mark:    uid=3f9a1c2b0d4e5678 -->
```

看完隨手填 `+`（有用）、`-`（沒用）、`++` / `--`（強烈），Markdown 預覽時不會顯示。
跑 `uv run newsletter-feedback` 收集到 `state/feedback.jsonl`，重複標記以最新為準。
報告裡的標記只匯入 `feedback.jsonl` 還沒有紀錄的那一則；已有紀錄的（含網頁標的）以 jsonl 為準，不會被覆蓋。

### 用網頁標記（取代手改 Markdown）

```bash
(cd web/ui && npm install && npm run build)   # 前端還沒建置過才需要；沒建置時頁面回 503 並提示這行
uv run newsletter-web               # 開 http://127.0.0.1:8787/
uv run newsletter-web --selftest    # API、靜態檔、寫入／覆蓋／取消的讀回、/auth 的本機限制
```

`/` 當日晨報、`/reports` 歷史列表、`/reports/<date>` 單日。每則主要新聞末尾有「有用／沒用」兩顆按鈕（上／下箭頭圖示），按下即 append 一行到
`state/feedback.jsonl`（欄位同 `feedback.py`，同一則以最後一筆為準），不必再跑 `feedback.py`。

- 只有兩級：有用 = `+`、沒用 = `-`。再按一次同一顆＝取消（寫成 `mark: ""`），按另一顆＝覆蓋。
- 只有主要新聞（標題段落＋清單）有按鈕，「其餘收錄」那種整張單行清單沒有（由 `report_data.py` 的 `votable` 決定，網頁與 email 一致）。
  併了多篇文章的新聞（報告裡連著好幾行 mark，每篇一個 uid）只有一組按鈕，按下去對每篇各記一筆。
- 頁面的標記狀態只看 `feedback.jsonl`；還留在 Markdown 裡、尚未用 `feedback.py` 收集的標記不會顯示，先跑一次 `feedback.py` 匯入即可。
- 網頁與 `feedback.py` 可以同時跑：兩邊都只 append、不改寫舊內容，並用 `state/feedback.jsonl.lock` 排隊。
  `uv run newsletter-feedback --selftest` 涵蓋這部分（含併發 append）。
- **沒有登入**：預設只 bind `127.0.0.1`，要對外請放在 Cloudflare Tunnel + Access 後面，不要改 `--host`（Docker 部署例外：容器內綁 `0.0.0.0`，但不 publish 任何 port，見「部署到家用 host」）。
- `POST /api/feedback` 只收 `Content-Type: application/json`，body 是 `{"uid": "...", "mark": "+" | "-" | ""}`。
- `/auth`（貼 OAuth token）**只服務本機**，經 Tunnel 進來的一律 404，見下一節。
- **email 裡的「👍 有用／👎 沒用」連結**（email 的連結文字仍帶 emoji；網頁上的按鈕是箭頭圖示）：設環境變數 `NEWSLETTER_BASE_URL`（對外網址，如 `https://news.example.com`，要 `http(s)://` 開頭）後，
  `render_email.py` 會在每則**主要新聞**底下加兩個連結，指向 `<base>/feedback/<uid>?v=%2B`（有用）／`?v=-`（沒用），手機看信也能回饋。
  與網頁一致：「其餘收錄」那種沒有標題段落的整張單行清單不放連結；併了多篇文章的新聞（報告裡連著好幾行 mark，每篇一個 uid）只放一組，
  uid 用逗號接起來 `<base>/feedback/<uid>,<uid>?v=…`，確認頁一次對每個 uid 各投一票（單一 uid 的舊連結照常可用）。
  **連結不會一點就寫入**：信箱的安全掃描會自動開連結，所以 GET 只顯示「確認標為 有用」的頁面（`web/ui/src/pages/FeedbackPage.tsx`，
  資料來自 `GET /api/feedback/<uid>`，純讀取），按了確認才 `POST /api/feedback`。已經是同一個標記就只顯示「已記下」；
  標記不同則說明會覆蓋。沒設 `NEWSLETTER_BASE_URL`（本機測試）就不加按鈕；格式不對會在 stderr 警告並不加。
  網址要是 Tunnel + Access 保護的那個網域：點連結時 Access 會先要求登入，掃描器看到的只是登入頁。
  Docker 部署在 `.env` 設；systemd／cron 部署在 `~/.config/newsletter-ops/env` 設。

### Web 前端（`web/ui/`）

Vite + React + TypeScript。後端 `newsletter-web`（`web/server`）只出 JSON，頁面全由前端畫；晨報是 `report_data.py` 解析出的結構（標題、段落、巢狀清單、每則的 mark、資料來源），前端依結構排版。
版面是自適應的（手機單欄、筆電左側目錄＋內文，樣式在 `web/ui/src/styles.css`）；email 吃同一份結構，由 `render_email.py` 排成 inline-CSS HTML，兩邊版型各自維護。

```bash
cd web/ui
npm install          # 第一次；需要 Node ^20.19 或 >=22.12
npm run build        # 型別檢查 + 建置到 web/ui/dist，newsletter-web 直接提供
npm run dev          # 開發：Vite 在 :5173，/api 代理到 newsletter-web（需另外用 uv run newsletter-web 開後端）
npm test             # Vitest + Testing Library：元件與路由
npm run typecheck
```

- 路由：`/` 當日、`/reports` 歷史、`/reports/<date>` 單日、`/feedback/<uid>?v=…` email 連結的確認頁、`/auth` 授權（只限本機）。後端對這幾條回 `index.html`，
  其他不認得的路徑回 404 的 `index.html`（前端畫「找不到頁面」）。新增前端路由時，`web/server/src/newsletter_web/web.py` 的 `SPA_ROUTES` 要同步。
- API（細節見 `web/server/src/newsletter_web/web.py` 開頭的說明、型別見 `web/ui/src/types.ts`）：`GET /api/session`、`/api/today`、`/api/reports`、
  `/api/reports/<date>`、`/api/feedback/<uid>`、`/api/auth`；`POST /api/feedback`、`/api/auth/token|test|revoke`。
- 開發時 Vite 的代理不改 `Host`、不加 `X-Forwarded-*`，所以後端仍把它當本機，`/auth` 可以正常測。後端埠號不是 8787 時，
  前端用同一個環境變數：`NEWSLETTER_WEB_PORT=8790 npm run dev`。
- HTML 回應帶 `Content-Security-Policy`（只許同源的腳本與樣式），所以前端不能有行內 `<script>`／`style="…"`。
- 深色模式：預設跟系統（`prefers-color-scheme`），頁首「配色」可改成淺色／深色，選擇存在瀏覽器的 localStorage（key `theme`，只收 `light`／`dark`，沒有＝自動），後端不知道。
  `<html data-theme>` 是唯一真相；顏色在 `styles.css` 的 `:root` 用 `light-dark(淺, 深)` 定義（需要 2024 年中以後的瀏覽器）。
  `web/ui/public/theme-init.js` 在 `<head>` 同步套用儲存的主題，避免先閃一下錯的顏色；CSP 不許行內腳本，所以它必須是外部檔。
  放進 `web/ui/public/` 的檔案要同時放行 `.gitignore`（白名單）、`.dockerignore`，並在 `Dockerfile` 的前端階段 COPY，否則不會進版控或映像。
- 報告格式（`SKILL.md` 規定的 Markdown 子集）有改動時**只改 `report_data.py`**：網頁與 email 共用同一個解析器。哪些條目可以投票
  （`list`／`mark` 區塊的 `votable`：主要新聞才有、「其餘收錄」沒有）也在那裡決定，兩邊版型只負責照畫。
  `uv run python -m newsletter_shared.report_data --selftest` 驗證解析與 `votable`，`uv run newsletter-render --selftest` 驗證 email 輸出。

累積兩三個月後可以看出：收錄很多卻從未拿到 `+` 的關鍵字該降權、`+` 項目裡反覆出現卻
不在 boost 清單的詞該加進去、長期沒命中的關鍵字該移除。

**建議由人確認，不自動套用。** 會自己調參數的系統，出錯時你查不出它為什麼開始推垃圾。

### PWA（可安裝，離線讀開過的晨報）

網頁可以裝成 App：Android／桌機 Chrome 選「安裝應用程式」（桌機在網址列右側），iOS Safari 用「分享 → 加入主畫面」。裝好後以獨立視窗開啟，圖示是藍底的「報」。
需要 HTTPS 或 `localhost`（Tunnel 的網域本來就是 HTTPS）。**只有 `npm run build` 的產物有 service worker**，`npm run dev` 不註冊，免得開發時被舊快取干擾。

- **離線**：開過的今日晨報、歷史列表、單日晨報，沒網路時仍能打開，標頭下方出現「離線中」；沒開過的日期顯示「讀取失敗」。有網路時永遠先拿最新的（標記狀態、新產出的晨報不會被舊快取蓋住）。離線時按「有用／沒用」會顯示「儲存失敗」，不會排隊補送。
- **更新**：新版 service worker 直接接手（`skipWaiting`＋`clientsClaim`），不提示重新整理；舊版的 precache 自動清掉。
- **檔案**：`web/ui/vite.config.ts`（`vite-plugin-pwa`：manifest、註冊）、`web/ui/src/sw.ts`（service worker 的路由）、`web/ui/public/icons/`（`icon.svg` 是來源，另有 192、512、maskable 512、apple-touch-icon 180 的 PNG；換圖示就重新輸出這四張）。
  `public/` 的檔案原樣複製到 `dist/` 根目錄；它被 `.gitignore`、`.dockerignore` 放行，`Dockerfile` 也有 `COPY web/ui/public`，新增這類目錄時三處都要加。

**`sw.ts` 的原則**（改它之前先讀）

- **只快取** `GET /api/today`、`/api/reports`、`/api/reports/<date>`，且只存 200。`POST`、`/api/session`（`local` 因請求而異）、`/api/auth*`（能寫入憑證）、`/api/feedback/*` **不註冊路由、不經過 service worker**；新增路由前先確認不會碰到它們。
- **導覽一律先走網路**，只有網路錯誤才回 precache 的 `index.html`。Access 逾時時回的 302 要原樣交給瀏覽器去登入，不能拿快取的畫面蓋掉；導覽的回應不進任何快取，登入頁不可能被存起來。
  導覽路由必須排在 precache 的路由**前面**（路由先註冊的先贏；否則 `/` 會直接吃快取），所以用 `precache()`＋`addRoute()`，不要改回 `precacheAndRoute()`。
- manifest 的 `<link>` 要帶 `crossorigin="use-credentials"`（設定裡的 `useCredentials: true`）：瀏覽器抓 manifest 預設不帶 cookie，會被 Access 擋成登入頁，在正式站就裝不起來。這在本機測不出來。
- 註冊用外部的 `registerSW.js`（`injectRegister: 'script'`）；不能改成行內，CSP 不允許行內 `<script>`。
- PWA 的檔案（`manifest.webmanifest`、`sw.js`、圖示）也在 Access 後面，不要為了安裝方便而對它們開 bypass。

**已知限制**

- Access 登入逾時後，已經開著的頁面背景請求會被跨來源 redirect 擋掉、看起來像離線而顯示快取內容；畫面上有日期可以辨識。重新開啟 app 時導覽會被導去登入，登入後回到晨報。
- 「離線中」橫幅看的是 `navigator.onLine`，只反映有沒有網路（飛航模式、斷線），伺服器掛了但網路還在時不會出現。
- 訊號極差時，讀取要等瀏覽器自己放棄才回退快取（`NetworkFirst` 沒設逾時）。

**測試**：`npm test` 的 `pwa.test.ts` 會建置到暫存目錄（不動 `dist/`），檢查 manifest 欄位、`crossorigin`、沒有行內 script、service worker 的路由範圍與順序；
`uv run newsletter-web --selftest` 檢查 manifest／`sw.js`／圖示的 Content-Type 與 `no-cache`。

**正式站驗收**（Docker 與 systemd 兩種部署共用；Access 的行為只有在真實網域上測得到）

| 檢查 | 預期 |
| --- | --- |
| **未登入**：`curl -sI https://news.example.com/manifest.webmanifest`、`curl -sI https://news.example.com/sw.js` | 都是 `302` 到 `cloudflareaccess.com`（或 `403`），**絕不能是 200**（PWA 的檔案也要在 Access 後面） |
| Android Chrome 登入後開首頁 | 選單出現「安裝應用程式」；裝好後以獨立視窗開到今日晨報 |
| iOS Safari 登入後「分享 → 加入主畫面」 | 圖示是藍底「報」（不是預設的字母）；以獨立視窗開啟 |
| 手機開飛航模式，開已安裝的 app | 看得到最近開過的晨報，標頭下方有「離線中」 |
| 清掉 Access 登入（或等逾時）後開 app | 被帶到 Access 登入頁，登入後回到晨報（不是白畫面，也不是舊的畫面停住） |

iOS 主畫面的圖示若是預設字母，很可能是 iOS 抓圖示時沒帶 Access 登入；可以在 Access 對 `/icons/*` 加一條 bypass policy。圖示不含機密，但這是改安全設定，範圍請只限 `/icons/*`。

## 排程

正式排程見「部署到家用 host」。`run_daily.sh` 是唯一的排程入口，Docker 的 scheduler 容器、systemd timer 與 cron 都呼叫它：

```cron
0 8 * * * $HOME/newsletter-ops/run_daily.sh
```

它依序跑 `newsletter-fetch --no-report` → `agent_run.py` → `render_email.py` → `send_email.py`（`render_email.py` 的 stdout 是一行 JSON，
先收進變數再餵給 `send_email.py`）。任一步失敗就停下、以該步的 exit code 結束（`agent_run.py` 的 1～6 見下節），
並在 `logs/<YYYY-MM>.log` 留一行失敗紀錄。額外參數（如 `--lookback 72`）轉給 `newsletter-fetch`。
先由 `newsletter-fetch` 抓好當日 curated JSON，agent 讀到的就是今天的檔，不必自己再抓一次。

內部用 `uv run --locked` 執行，並替 cron 精簡的 PATH 補上 uv 常見的安裝位置（`~/.local/bin`、`~/.cargo/bin`、Homebrew）。
`--locked` 讓鎖檔與 `pyproject.toml` 對不上時直接失敗，不會在排程裡自己改鎖檔。

機密從 env 檔載入（預設 `~/.config/newsletter-ops/env`，`NEWSLETTER_ENV_FILE` 可改，範本在 `deploy/newsletter.env.example`），
權限必須是 `600`，太鬆會拒絕執行（exit 78）。env 檔是 shell 語法，值含空格（Gmail 應用程式密碼）要加引號。
排程需要 OAuth token（環境變數 `CLAUDE_CODE_OAUTH_TOKEN`，或 `/auth` 頁面存的檔，見「無人值守」的「認證」）；兩者都沒有時 `agent_run.py` 直接 exit 2。
macOS 的 cron 需要「完整磁碟取用權」，或改用 launchd。

arXiv 論文的會議／期刊接受資訊從 API 的 Comments / Journal-Ref 解析，清單在 `config.json` 的 `arxiv_venues`（conference / journal / minor_tracks）；主會議或期刊 +2.0、workshop 等次級 track +0.8、投稿中 +0.4，結果連同中文 `label` 寫進 curated JSON 的 `venue`，自我檢查：`uv run python -m newsletter_agent.curate`、`uv run newsletter-render --selftest`。

## 無人值守（Claude Agent SDK）

不開 Claude app 也能跑完整流程：`agent_run`（`uv run newsletter-agent`）用 Claude Agent SDK 呼叫**同一份**
`.claude/skills/news-digest/SKILL.md`，與在對話裡打 `/news-digest` 並存、結果一致。

```bash
uv sync                                   # 依 uv.lock 建立 .venv 並裝好依賴（uv run 也會自動做）
uv run newsletter-web                        # 第一次：開 http://127.0.0.1:8787/auth 貼上 OAuth token（見下方「認證」）
uv run newsletter-agent                  # 依 skill 寫報告並驗收（有更新、格式正確），約數分鐘
uv run newsletter-agent --max-turns 80   # 預設 60 回合，超過就中止並視為失敗
uv run newsletter-render | uv run newsletter-send   # 報告寫好之後：轉成 email 並寄出
uv run newsletter-agent --auth-check     # 只驗證 token（一次最小的呼叫），不跑晨報
```

### 認證：只用 OAuth（訂閱額度），不使用 API key

**刻意不支援 `ANTHROPIC_API_KEY`**：這個專案走 Claude 訂閱額度，不想額外付費。環境裡就算有 key 也不會被使用。

1. 在**自己的電腦**執行 `claude setup-token`，在瀏覽器完成授權；它會印出效期一年的 token（CLI 不會幫你存）。
   需要 Pro / Max / Team / Enterprise 方案。
2. 開 `http://127.0.0.1:8787/auth`（`uv run newsletter-web`），把 token 貼上送出。伺服器會先用它實際呼叫一次 Claude
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
  注意 token 在 agent（claude CLI）的環境裡，agent 用 Bash 跑的指令讀得到它；
  Claude Code 有 `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` 可以擋，但它要求系統有 bubblewrap，沒有就整個 CLI 啟動失敗，
  所以預設沒開；要用請先裝 bubblewrap 再自己在環境設這個變數。
- **`/auth` 只能從本機開**：能寫入憑證，而 `web.py` 沒有登入。`cloudflared` 跑在同一台機器、以 `127.0.0.1` 連進來，
  所以光看來源位址擋不住 Tunnel；要同時符合「來源是 loopback」「`Host` 是 `127.0.0.1` / `localhost` / `[::1]`」
  「沒有 `Cf-*` / `X-Forwarded-*` 等代理標頭」「`Origin`（若有）是本機」，否則回 404。
  遠端 host 上要貼 token：`ssh -L 8787:127.0.0.1:8787 <host>`，再用自己電腦的瀏覽器開 `http://localhost:8787/auth`。
- 驗證 token 需要 SDK：`web` 成員依賴 `agent`（它帶著 SDK），用 `uv run newsletter-web` 啟動就有。
- **範圍**：只載入專案層級的 skill（`setting_sources=["project"]`），不吃使用者層級的同名 skill；預先允許
  `Skill / Bash / Read / Write / Edit / WebFetch / WebSearch`，其餘工具一律拒絕（不會卡在沒人回答的提示）。
- **失敗會以非 0 結束**，cron 看得到：

  | exit | 意思 |
  | --- | --- |
  | 0 | 成功 |
  | 1 | agent 失敗：API 錯誤、超過 `--max-turns`、SDK 例外 |
  | 2 | 環境問題：沒裝 `claude-agent-sdk`，或沒有可用的 OAuth token |
  | 3 | 報告沒產出：`reports/<date>.md` 沒在這次執行更新，或當天 curated 沒有收錄項目（抓取全失敗） |
  | 4 | 報告格式不符：shared 的 `parse_report` 拒絕（缺 `subject` 註解或今日頭條），原因在 stderr |
  | 5 | **授權失敗**：token 無效或已過期，到 `/auth` 重新貼上新的 token |
  | 6 | **額度用完**：訂閱的使用額度或帳務問題；不會改用別的認證，等額度恢復再跑 |

- **用量與成本**：結束時從 SDK 的結果取 token 與 `total_cost_usd`，寫成 `stage: claude`、`runner: sdk` 的紀錄
  （label 沿用 `NEWSLETTER_RUN_LABEL`）。和其他 stage 一樣，**要開 debug 才會寫**：`NEWSLETTER_DEBUG=1`。
  stderr 的 `[agent]` 摘要行（含估算費用 `est_usd`）不受 debug 影響，一定會進 log。`summary` 的 `via` 欄分辨路線（`sdk` / `chat`），
  `usd` 欄只有 `sdk` 路線有值。**走訂閱額度時 `total_cost_usd` 只是 SDK 依牌價估的 API 等價費用，不是實際扣款**
  （[官方說明](https://code.claude.com/docs/en/agent-sdk/cost-tracking)：client-side estimate），拿來比較每次執行的相對用量即可。
  跑幾天後用它調整 `--max-turns`，並看看一次晨報吃掉多少訂閱額度：

  ```bash
  NEWSLETTER_DEBUG=1 uv run newsletter-agent
  uv run newsletter-metrics summary 14
  ```

- 用 `agent_run.py` 時，skill 裡的 `newsletter-metrics claude` 會自動略過（`NEWSLETTER_RUNNER=sdk`），避免和 SDK 的用量重複記錄。
- 自我檢查：`uv run python -m newsletter_agent.agent_run --selftest`、`uv run python -m newsletter_shared.auth_store --selftest`、`uv run newsletter-web --selftest`、
  `uv run python -m newsletter_shared.report_data --selftest`；前端 `cd web/ui && npm test`。
  push 時 GitHub Actions 會跑除了 `agent_run`（要裝 SDK）和前端以外的全部自我檢查，另外跑 `deploy/check_boundaries.py`（依賴方向），
  設定在 `.github/workflows/selftest.yml`；新增模組的自我檢查記得加進去。

## 部署到家用 host（Docker + Cloudflare Tunnel + Access）

目標：pipeline 與 web 跑在家裡一台 host（VM 也行，只要有 Docker），經 Cloudflare 在外也能看晨報、按有用／沒用。本機只當測試區。
host 上不用裝 Python、uv、cloudflared，也不用開任何對外 port。

```
手機／筆電 ──https──▶ Cloudflare（Access：只放行你的 email）──Tunnel──▶ cloudflared 容器 ──http://web:8787──▶ web 容器
                                                                    scheduler 容器：每天 08:00 跑 run_daily.sh
                                    三個容器共用一個 volume：newsletter-data（reports／data／state／logs）
```

- `Dockerfile`：web 與排程共用同一個映像（Python 3.11 + uv 鎖定的依賴，各成員以 editable 裝進同一個環境，非 root 執行）。多階段建置：先用 Node 建置網頁前端（`web/ui/`），
  只把 `web/ui/dist` 帶進最終映像，所以 host 與映像裡都不需要 Node；`.dockerignore` 是白名單，前端原始碼與 `web/ui/public/`（圖示）要放行才進得了 build context。
- `docker-compose.yml`：`web`、`scheduler`、`cloudflared` 三個服務與 volume。`cloudflared` 用 Tunnel token 執行，
  不需要 `cert.pem`、憑證檔或 `config.yml`；對外的主機名稱在 Cloudflare 後台設定。
- `web.py` 沒有登入、而且能寫入 `state/feedback.jsonl`，**唯一的防線是 Access**。compose 沒有 `ports:`，host 不會開任何 port；
  請不要為了方便從 host 直接開而加上 `ports:`，那會繞過 Access（host 本機仍連得到容器的內部 IP，所以這台 host 本身要信得過）。Tunnel 的網域一定要先掛上 Access 再對外使用（步驟 2、4）。
- 機密只放 `.env`：`web` 容器拿不到任何機密，`scheduler` 拿不到 `TUNNEL_TOKEN`。

**1. 取得程式與機密**

```bash
git clone https://github.com/Ponywen0819/newsletter-ops.git ~/newsletter-ops && cd ~/newsletter-ops
cp .env.example .env && chmod 600 .env
$EDITOR .env      # GMAIL_USER、GMAIL_APP_PASSWORD、CLAUDE_CODE_OAUTH_TOKEN；TUNNEL_TOKEN 在下一步取得
```

`CLAUDE_CODE_OAUTH_TOKEN` 的來源與用法見「無人值守」的「認證」：在**自己的電腦**執行 `claude setup-token` 取得，效期一年，只走訂閱額度。
Docker 部署**用不了 web 的 `/auth` 頁面**：它只服務「來源是 loopback、沒有代理標頭」的請求，容器網路裡的請求一律 404，
所以 token 放在 `.env`。token 到期（`agent_run.py` exit 5）時重新產生，改 `.env` 後 `docker compose up -d`（scheduler 會用新的環境變數重建）。

**2. Cloudflare：建立 Tunnel 與 Access（Access 一定要做）**

Zero Trust 後台（介面名稱依版本略有不同）：

1. Networks → Tunnels → Create a tunnel → 類型選 Cloudflared，取個名字。畫面上的安裝指令不用理它，
   只複製其中的 token（那串很長的字）貼到 `.env` 的 `TUNNEL_TOKEN`。
2. 該 Tunnel 的 Public Hostname：填你的網域 `news.example.com`，Service 類型選 `HTTP`、URL 填 `web:8787`（compose 服務名稱）。DNS 紀錄會自動建立。
3. Access → Applications → Add an application → Self-hosted：Application domain 填同一個 `news.example.com`；
   Policy 一條就好：Action = Allow，Include = Emails，只填你自己的 email。不要留 Everyone 之類的其他 policy。

**3. 啟動**

```bash
docker compose up -d --build
docker compose ps        # web 要是 healthy、cloudflared 是 Up，PORTS 欄全空
```

`.env` 缺 `GMAIL_USER`、`GMAIL_APP_PASSWORD`、`CLAUDE_CODE_OAUTH_TOKEN`、`TUNNEL_TOKEN` 任何一個，`docker compose` 會直接報錯並指出缺哪個。

**4. 驗收**

| 檢查 | 預期 |
| --- | --- |
| `docker compose ps` | `web` healthy、`cloudflared` Up、PORTS 欄是空的 |
| `docker compose logs cloudflared` | 出現 `Registered tunnel connection`（沒有就是 token 有誤或出站連不到 Cloudflare） |
| 另一個網路（手機關 Wi-Fi）開 `https://news.example.com` | 先到 Access 登入頁，用你的 email 登入後看到晨報（還沒有報告時是「還沒產出」頁） |
| **未登入**：`curl -sI https://news.example.com/reports` | `302` 到 `cloudflareaccess.com`（或 `403`），**絕不能是 200** |
| **未登入**：`curl -s -X POST -H 'Content-Type: application/json' -d '{}' https://news.example.com/api/feedback` | 同上，到不了 `web.py`（它自己會回 400，看到 400 代表 Access 沒擋住） |
| 登入後按一則的「有用」 | `docker compose exec web tail -n1 state/feedback.jsonl` 多一行 |
| 裝成 PWA、離線、Access 逾時 | 照「PWA」一節的「正式站驗收」表 |
| `.env` 設了 `NEWSLETTER_BASE_URL` 後寄一封信，在手機點信裡的「👍 有用」連結 | 先經 Access 登入，再看到「確認標為 有用」頁；**這時 `feedback.jsonl` 還沒變**，按了確認才多一行 |
| `docker compose run --rm scheduler uv run newsletter-agent --auth-check` | 只驗證 OAuth token（一次最小的呼叫，不跑晨報），通過才表示每天的排程跑得起來 |

頁面上要有晨報可看，`reports/<date>.md` 得先存在：還沒到 08:00 的話，先手動跑一次（見下，會呼叫 Claude、有費用）。
Access 登入逾時後按「有用」會顯示「儲存失敗」，重新整理頁面重新登入即可。

**5. 日常操作**

```bash
docker compose logs -f scheduler                  # 排程輸出；run_daily.sh 失敗時會附上 logs/ 的最後 20 行
docker compose exec web ls reports                # volume 裡的報告
docker compose run --rm scheduler ./run_daily.sh  # 手動跑一次完整流程（抓取 → agent → render → 寄信）
docker compose run --rm -e NEWSLETTER_DEBUG=1 -e NEWSLETTER_RUN_LABEL=test scheduler uv run newsletter-agent   # 只產報告、不寄信
git pull && docker compose up -d --build          # 更新（interests.md、config/、程式都在映像裡，要重 build）
```

- 每天 08:00（Asia/Taipei，`.env` 的 `NEWSLETTER_RUN_AT` 可改）。容器停機時錯過的那一次不會補跑，要補就手動跑。
- 每個**新**容器的第一次 `uv run` 會在 log 印出 `Building … Uninstalled 4 packages / Installed 4 packages`：uv 判定 editable 成員比安裝記錄新而重裝
  （約 10 毫秒、不需要網路），之後同一個容器就安靜了。這不是錯誤。
- `up -d --build` 只會重建有變動的容器；`web` 當掉或被重建時，`cloudflared` 靠服務名稱 `web` 重新連上，不必另外處理。

**6. 資料保存：不保存**

生成物——`reports/`、`data/`、`state/`（含 `state/feedback.jsonl`）、`logs/`——都在 volume `newsletter-data` 裡（容器內 `/var/lib/newsletter`），
換容器、換映像、`docker compose down` 都還在；**`docker compose down -v` 或 `docker volume rm` 才會刪掉**。
不備份、不進版控，volume 壞了就重來：抓取結果可以重跑，`seen.json` 沒了只會讓時間窗內已報過的新聞再出現一次，
`feedback.jsonl` 只是改 `config/interests.md` 時參考的中間產物（本 repo 是 public，標記含標題與來源，更不該推上來）。

版控裡只有程式、設定與 `interests.md`；這些是唯一需要「持續更新」的內容，`git pull` 後重 build 即可（見上）。
要新增被追蹤的檔案就加進 `.gitignore` 的白名單（`.dockerignore` 也是白名單，要進映像的話一併加）。
之後若想留報告歷史，另外決定備份方式（例如定期把 volume 打包），不要放進這個 repo。

### 不用 Docker：systemd + cloudflared

不想用 Docker 時，直接在 host 上跑。單元檔與設定範本在 `deploy/`，以下假設 repo 在 `~/newsletter-ops`，需要 [uv](https://docs.astral.sh/uv/)
（見「環境（uv）」）與 Node（^20.19 或 >=22.12，只在建置網頁前端時用到，之後執行不需要）。資料保存的決定同上：生成物留在 host 的 `reports/`、`data/`、`state/`、`logs/`，不備份、不進版控。

**1. 取得程式**

先裝 [uv](https://docs.astral.sh/uv/)（見「環境（uv）」）與 Node。

```bash
git clone https://github.com/Ponywen0819/newsletter-ops.git ~/newsletter-ops
cd ~/newsletter-ops && uv sync --locked      # 建 .venv、裝 claude-agent-sdk；沒有 Python 3.11 時 uv 會自己下載
(cd web/ui && npm ci && npm run build)          # 建置網頁前端到 web/ui/dist；沒做的話 web service 的頁面都回 503
uv run newsletter-fetch --list-sources             # 不連網，確認設定可用
```

**2. 機密**

```bash
mkdir -p ~/.config/newsletter-ops
cp ~/newsletter-ops/deploy/newsletter.env.example ~/.config/newsletter-ops/env
chmod 600 ~/.config/newsletter-ops/env
$EDITOR ~/.config/newsletter-ops/env   # GMAIL_USER、GMAIL_APP_PASSWORD、CLAUDE_CODE_OAUTH_TOKEN（或之後開 /auth 貼）
```

只有排程入口會載入這個檔；web service 不載入，網頁程序拿不到這些機密。

**3. systemd（user 單元，不需要 root）**

```bash
mkdir -p ~/.config/systemd/user
cp ~/newsletter-ops/deploy/newsletter-{web.service,daily.service,daily.timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now newsletter-web.service newsletter-daily.timer
sudo loginctl enable-linger "$USER"   # 沒登入也持續執行，開機自動啟動
```

- 每天 08:00（Asia/Taipei）跑 `run_daily.sh`；host 當時關機的話，開機後補跑（`Persistent=true`）。
- 看 web：`journalctl --user -u newsletter-web -f`；看排程：`systemctl --user list-timers`、`logs/<YYYY-MM>.log`。
- 手動跑一次排程：`systemctl --user start newsletter-daily.service`（會真的呼叫 Claude 並寄信，約數分鐘、有 API 費用），
  失敗時 `systemctl --user status newsletter-daily` 會顯示 failed，原因在 `logs/<YYYY-MM>.log`。
- 更新（`interests.md`、`config/`、程式等被追蹤的檔案）：
  `git -C ~/newsletter-ops pull && (cd ~/newsletter-ops && uv sync --locked && cd web/ui && npm ci && npm run build) && systemctl --user restart newsletter-web`。
  （前端沒變動時，`npm ci && npm run build` 可以省略。）排程每次都重新讀檔，不必重啟。

**4. cloudflared Tunnel**

照 Cloudflare 文件安裝 `cloudflared`，然後：

```bash
cloudflared tunnel login
cloudflared tunnel create newsletter                       # 印出 <TUNNEL_UUID>，憑證寫在 ~/.cloudflared/
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/<TUNNEL_UUID>.json /etc/cloudflared/
sudo cp ~/newsletter-ops/deploy/cloudflared-config.yml.example /etc/cloudflared/config.yml   # 改 <TUNNEL_UUID> 與 hostname
cloudflared tunnel route dns newsletter news.example.com
sudo cloudflared service install                           # 以 system service 常駐
```

**5. Cloudflare Access（先做，再對外使用）**

Zero Trust 後台 → Access → Applications → Add an application → Self-hosted：
Application domain 填 `news.example.com`；Policy 一條就好：Action = Allow，Include = Emails，只填你自己的 email。
不要留 Everyone 之類的其他 policy。

**6. 驗收**

| 檢查 | 預期 |
| --- | --- |
| host 上 `curl -sI http://127.0.0.1:8787/reports` | `200` |
| host 上 `ss -ltn \| grep 8787` | 只有 `127.0.0.1:8787`，不是 `0.0.0.0` |
| 另一個網路（手機關 Wi-Fi）開 `https://news.example.com` | 先到 Access 登入頁，用你的 email 登入後看到晨報 |
| **未登入**：`curl -sI https://news.example.com/reports` | `302` 到 `cloudflareaccess.com`（或 `403`），**絕不能是 200** |
| **未登入**：`curl -s -X POST -H 'Content-Type: application/json' -d '{}' https://news.example.com/api/feedback` | 同上，到不了 `web.py`（它自己會回 400，看到 400 代表 Access 沒擋住） |
| 登入後按一則的「有用」 | host 上 `tail -n1 ~/newsletter-ops/state/feedback.jsonl` 多一行 |
| 裝成 PWA、離線、Access 逾時 | 照「PWA」一節的「正式站驗收」表 |

頁面上要有晨報可看，`reports/<date>.md` 得先存在：還沒到 08:00 的話，在 host 上 `uv run newsletter-agent`
（或上面的手動排程）先產一份。
Access 登入逾時後按「有用」會顯示「儲存失敗」，重新整理頁面重新登入即可。

## 量測（debug）

`config.json` 設 `"debug": true`（或臨時用 `NEWSLETTER_DEBUG=1 uv run newsletter-fetch ...`），每個階段會寫一行到
`logs/metrics/<date>.jsonl`：`fetch_source`（每個來源的耗時／則數／錯誤）、`fetch`、`curate`、`render`，
以及 news-digest 跑完後由 `uv run newsletter-metrics claude`（skill 內呼叫）從 Claude Code session 紀錄統計的 token 與工具耗時。

```bash
uv run newsletter-metrics summary 14         # 最近 14 天，每次正式執行一行
uv run newsletter-metrics summary 14 --all   # 連測試執行一起列
NEWSLETTER_DEBUG=1 NEWSLETTER_RUN_LABEL=test uv run newsletter-fetch --no-report   # 測試執行，紀錄標 test
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
uv run newsletter-fetch --list-sources   # 不連網，列出載入結果與停用項目
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
uv run newsletter-fetch --list-sources   # 不連網，列出載入結果與停用項目
```

改 `interests.md` 時順手看一次 `keywords`——前者是判讀用的自然語言，後者是評分用的
機械版本，兩邊脫節就會出現「分數很高但你根本不在乎」的項目。

評分參數仍在 `config/config.json`：`boost_high` +2.0、`boost_mid` +0.8、
`penalize` -4.0，再加新鮮度加權（半衰期 12 小時，最高 +1.5）。低於 `min_score` 不收錄。
