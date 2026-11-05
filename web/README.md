# web

晨報網頁：看晨報、按「有用／沒用」回饋、貼 OAuth token。專案全貌見[根目錄 README](../README.md)。

```
server/src/newsletter_web/web.py   後端：JSON API（/api/*）＋提供 web/ui/dist；有用／沒用 寫進 feedback.jsonl；
                                   /auth 貼 OAuth token（標準庫，無登入）（newsletter-web）
ui/                                前端：Vite + React + TypeScript（晨報、歷史列表、/auth），可安裝成 PWA；建置產物 web/ui/dist 不進版控
```

後端 `newsletter-web` 只出 JSON，頁面全由前端畫，前端見 [ui/README.md](ui/README.md)。
後端依賴 [agent](../packages/agent/README.md) 只為了驗證 OAuth token（以子程序呼叫 `newsletter-agent --auth-check`，貼上時與背景定時驗證；`web.py` 本身不 import agent），用 `uv run --project web/server newsletter-web` 啟動就有 SDK。

## 啟動

```bash
(cd web/ui && npm install && npm run build)   # 前端還沒建置過才需要；沒建置時頁面回 503 並提示這行
uv run --project web/server newsletter-web               # 晨報網頁，預設 http://127.0.0.1:8787（--port / NEWSLETTER_WEB_PORT 可改）
uv run --project web/server newsletter-web --auth-check-interval 1800   # 背景驗證 Claude 認證的間隔（秒），預設 3600；0＝不驗證
uv run --project web/server newsletter-web --selftest    # API、靜態檔、寫入／覆蓋／取消的讀回、/auth 的本機限制、定時驗證
```

`/` 當日晨報、`/reports` 歷史列表、`/reports/<date>` 單日。每則主要新聞末尾有「有用／沒用」兩顆按鈕（上／下箭頭圖示），按下即 append 一行到
`state/feedback.jsonl`（欄位同 shared 的 `feedback.py`，同一則以最後一筆為準），不必再跑 `newsletter-feedback`。

- 只有兩級：有用 = `+`、沒用 = `-`。再按一次同一顆＝取消（寫成 `mark: ""`），按另一顆＝覆蓋。
- 只有主要新聞（標題段落＋清單）有按鈕，「其餘收錄」那種整張單行清單沒有（由 shared 的 `report_data.py` 的 `votable` 決定，網頁與 email 一致）。
  併了多篇文章的新聞（報告裡連著好幾行 mark，每篇一個 uid）只有一組按鈕，按下去對每篇各記一筆。
- 頁面的標記狀態只看 `feedback.jsonl`；還留在 Markdown 裡、尚未用 `newsletter-feedback` 收集的標記不會顯示，先跑一次 `newsletter-feedback` 匯入即可。
- 網頁與 `newsletter-feedback` 可以同時跑，見 [shared/README.md](../packages/shared/README.md)「回饋標記」。

## 安全邊界

- **沒有登入**：預設只 bind `127.0.0.1`，要對外請放在 Cloudflare Tunnel + Access 後面，不要改 `--host`（Docker 部署例外：容器內綁 `0.0.0.0`，主 port 不 publish，只有管理 port 以 `127.0.0.1:8788` publish，見 [deploy/README.md](../deploy/README.md)）。
- `POST /api/feedback` 只收 `Content-Type: application/json`，body 是 `{"uid": "...", "mark": "+" | "-" | ""}`。
- HTML 回應帶 `Content-Security-Policy`（只許同源的腳本與樣式），前端的限制見 [ui/README.md](ui/README.md)。
- **`/auth`（貼 OAuth token）只能從本機開**：要同時符合來源是 loopback、`Host` 是本機、沒有 `Cf-*`／`X-Forwarded-*` 等代理標頭、`Origin`（若有）是本機，否則 404。
  cloudflared 也是從 `127.0.0.1` 連進來，所以光看來源位址不夠。遠端 host 用 `ssh -L 8787:127.0.0.1:8787 <host>`。
  Docker 部署改用 `--admin-port` 另開的管理 port（來源只要求私有位址，其他檢查照舊），見 [deploy/README.md](../deploy/README.md)。
  token 只存在這一頁存的檔，不讀 `CLAUDE_CODE_OAUTH_TOKEN`，政策見 [agent/README.md](../packages/agent/README.md)「認證」。
- **定時驗證**：啟動時與之後每 `--auth-check-interval` 秒（`NEWSLETTER_AUTH_CHECK_INTERVAL`，預設 3600、0＝關閉）用儲存的 token 跑一次 `--auth-check`，
  每次都是真的呼叫、用掉極少額度，結果只放記憶體。`GET /api/auth-health` 的 `state`：
  `ok`、`expiring`（依一年效期推算快到期）、`missing`（沒有 token）、`invalid`（要重新授權）、`unknown`（額度用完、逾時等，**不代表失效**）。

## API 與路由

API 都是 JSON，型別見 `ui/src/types.ts`：

| 路由 | 回應 | 說明 |
| --- | --- | --- |
| `GET /api/session` | `{local}` | 是否從本機來（決定要不要顯示「Claude 授權」） |
| `GET /api/heartbeat` | `{ok}` | 連線偵測，不讀檔、不寫入（見 [ui/README.md](ui/README.md)「PWA」） |
| `GET /api/today` | `{date, latest, report, marks}` | 當日晨報；還沒產出時 `report` 是 null |
| `GET /api/reports` | `{reports: [{date, headline}]}` | 歷史列表，新到舊 |
| `GET /api/reports/<date>` | `{date, report, marks}` | `report` 是 `report_data.parse_report()` 的結構 |
| `GET /api/feedback/<uid>` | `{uid, date, title, mark}` | email 確認頁用，只讀 |
| `POST /api/feedback` | `{uid, mark}` | 有用／沒用 |
| `GET /api/auth-health` | `{state, kind, checked_at}` | 認證健康狀態，所有人可讀 |
| `GET /api/auth`、`POST /api/auth/token\|test\|revoke` | | OAuth token 狀態與管理，只限本機 |

- 頁面路由（清單見 [ui/README.md](ui/README.md)）回 `index.html`，不認得的路徑回 404 的 `index.html`；新增前端路由時，`web.py` 的 `SPA_ROUTES` 要同步。
- `/feedback/<uid>?v=…` 是 email 連結進來的確認頁：GET 只顯示確認頁（`GET /api/feedback/<uid>` 純讀取），按了確認才 `POST /api/feedback`。email 端見 [notify/README.md](../packages/notify/README.md)。

## 自我檢查

```bash
uv run --project web/server newsletter-web --selftest
```

涵蓋 API、靜態檔、寫入／覆蓋／取消的讀回、`/auth` 的本機限制、定時驗證與 `/api/auth-health`，以及 manifest／`sw.js`／圖示的 Content-Type 與 `no-cache`、heartbeat 的回應（`no-store`、不寫入任何檔案）。前端測試見 [ui/README.md](ui/README.md)。
