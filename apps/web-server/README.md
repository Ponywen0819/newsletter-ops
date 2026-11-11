# web-server

晨報網頁的後端（`newsletter-web`）：JSON API（`/api/*`）＋提供前端 [web-ui](../web-ui/README.md) 的建置結果 `apps/web-ui/dist`；有用／沒用寫進 `feedback.jsonl`；`/auth` 貼 OAuth token。標準庫，無登入。專案全貌見[根目錄 README](../../README.md)。

頁面全由前端畫，兩邊的合約是 API（型別在 `apps/web-ui/src/types.ts`）與 `server.py` 的 `SPA_ROUTES`，改了要兩邊同步。
依賴 [llm](../../packages/llm/README.md) 只為了驗證 OAuth token（貼上 token 與按「測試連線」時以子程序呼叫 `newsletter-llm --auth-check`；程式本身不 import llm），用 `uv run --project apps/web-server newsletter-web` 啟動就有 SDK。

```
src/newsletter_web_server/
  web.py       入口：參數、啟動伺服器
  server.py    HTTP：路由、本機判斷、靜態檔；Site 把 reports 與 auth 組起來給 Handler 用
  reports.py   晨報與回饋的讀寫
  auth.py      OAuth token 的驗證、儲存、定時檢查
  errors.py    NotFound、ApiError
  selftest.py  --selftest
```

## 啟動

```bash
(cd apps/web-ui && npm install && npm run build)   # 前端還沒建置過才需要；沒建置時頁面回 503 並提示這行
uv run --project apps/web-server newsletter-web               # 晨報網頁，預設 http://127.0.0.1:8787（--port / NEWSLETTER_WEB_PORT 可改）
uv run --project apps/web-server newsletter-web --selftest    # API、靜態檔、寫入／覆蓋／取消的讀回、/auth 的本機限制、驗證結果的讀寫
```

`/` 當日晨報、`/reports` 歷史列表、`/reports/<date>` 單日。每則主要新聞末尾有「有用／沒用」兩顆按鈕（上／下箭頭圖示），按下即 append 一行到
`state/feedback.jsonl`（欄位同 shared 的 `feedback.py`，同一則以最後一筆為準），不必再跑 `newsletter-feedback`。

- 只有兩級：有用 = `+`、沒用 = `-`。再按一次同一顆＝取消（寫成 `mark: ""`），按另一顆＝覆蓋。
- 只有主要新聞（標題段落＋清單）有按鈕，「其餘收錄」那種整張單行清單沒有（由 shared 的 `report_data.py` 的 `votable` 決定，網頁與 email 一致）。
  併了多篇文章的新聞（報告裡連著好幾行 mark，每篇一個 uid）只有一組按鈕，按下去對每篇各記一筆。
- 頁面的標記狀態只看 `feedback.jsonl`；還留在 Markdown 裡、尚未用 `newsletter-feedback` 收集的標記不會顯示，先跑一次 `newsletter-feedback` 匯入即可。
- 網頁與 `newsletter-feedback` 可以同時跑，見 [shared/README.md](../../packages/shared/README.md)「回饋標記」。
- 同一排還有「事實有誤」：append 一行到 `state/kg_flags.jsonl`（`{uid, flag: "wrong" | "", at}`），之後寫晨報時這則萃取出的事件不再當背景，
  它對別則的否認也不算（見 [writer/README.md](../../packages/writer/README.md)「知識圖譜」）。再按一次＝取消。不排隊：離線時直接告知做不到。

## 安全邊界

- **沒有登入**：預設只 bind `127.0.0.1`，要對外請放在 Cloudflare Tunnel + Access 後面，不要改 `--host`（Docker 部署例外：容器內綁 `0.0.0.0`，主 port 不 publish，只有管理 port 以 `127.0.0.1:8788` publish，見 [deploy/README.md](../../deploy/README.md)）。
- **Access JWT（選用）**：設了 `NEWSLETTER_ACCESS_TEAM`、`NEWSLETTER_ACCESS_AUD` 時，非本機的請求都要帶有效的 `Cf-Access-Jwt-Assertion`（RS256，驗 aud／iss／exp，公鑰從 `<team>.cloudflareaccess.com/cdn-cgi/access/certs` 取並快取），否則 403；取不到公鑰也拒絕。Access 政策被誤刪時不會整站公開（`access.py`）。
- `POST /api/feedback`、`POST /api/kg-flag` 只收 `Content-Type: application/json`，body 是 `{"uid": "...", "mark": "+" | "-" | ""}`／`{"uid": "...", "flag": "wrong" | ""}`。
- HTML 回應帶 `Content-Security-Policy`（只許同源的腳本與樣式），前端的限制見 [ui/README.md](../web-ui/README.md)。
- **`/auth`（貼 OAuth token）只能從本機開**：要同時符合來源是 loopback、`Host` 是本機、沒有 `Cf-*`／`X-Forwarded-*` 等代理標頭、`Origin`（若有）是本機，否則 404。
  cloudflared 也是從 `127.0.0.1` 連進來，所以光看來源位址不夠。遠端 host 用 `ssh -L 8787:127.0.0.1:8787 <host>`。
  Docker 部署改用 `--admin-port` 另開的管理 port（來源只要求私有位址，其他檢查照舊），見 [deploy/README.md](../../deploy/README.md)。
  token 只存在這一頁存的檔，不讀 `CLAUDE_CODE_OAUTH_TOKEN`，政策見 [llm/README.md](../../packages/llm/README.md)「認證」。
- **驗證結果**：定時驗證在 worker（每天 07:30，見 [worker/README.md](../worker/README.md)），這裡不開定時器。
  `/auth` 貼上 token（通過時）與「測試連線」的結果也寫進同一個 `state/auth_health.json`，刪除 token 時一起刪掉。
  `GET /api/auth-health` 讀這個檔：檔案不存在是 `unknown`／`unchecked`（不顯示橫幅），超過 26 小時沒更新是 `unknown`／`stale`（worker 可能停了，不再說正常）。`state`：
  `ok`、`expiring`（依一年效期推算快到期）、`missing`（沒有 token）、`invalid`（要重新授權）、`unknown`（額度用完、逾時等，**不代表失效**）。

## API 與路由

API 都是 JSON，型別見 `apps/web-ui/src/types.ts`：

| 路由 | 回應 | 說明 |
| --- | --- | --- |
| `GET /api/session` | `{local}` | 是否從本機來（決定要不要顯示「Claude 授權」） |
| `GET /api/heartbeat` | `{ok}` | 連線偵測，不讀檔、不寫入（見 [ui/README.md](../web-ui/README.md)「PWA」） |
| `GET /api/today` | `{date, latest, report, marks, flags, run}`（有晨報時另有 `prev, next`） | 當日晨報；還沒產出時 `report` 是 null，有舊晨報時另附 `fallback`（最新一份，格式同 `/api/reports/<date>`），首頁直接顯示它。`run` 是當天的 `state/last_run.json`（`trigger, started_at, finished_at, exit_code, step`），今天還沒跑是 null |
| `GET /api/reports` | `{reports: [{date, headline}]}` | 歷史列表，新到舊 |
| `GET /api/feedback-stats` | `{total, groups: {source, topic, keyword: [{name, pos, neg}]}}` | `newsletter-feedback --stats` 的網頁版；每則只算最後一筆、取消的不算，則數多的在前 |
| `GET /api/search?q=` | `{q, results: [{date, text}], truncated}` | 所有晨報裡含 `q` 的行（不分大小寫，去掉 Markdown 與 mark；空白分開的多個詞要都有），新到舊最多 50 筆；`q` 最多 100 字 |
| `GET /api/reports/<date>` | `{date, report, marks, flags, prev, next}` | `report` 是 `report_data.parse_report()` 的結構；`flags` 是標了事實有誤的 uid（`{uid: "wrong"}`）；`prev`／`next` 是前一份（更舊）／下一份（更新）的日期，沒有是 null |
| `GET /api/feedback/<uid>` | `{uid, date, title, mark}` | email 確認頁用，只讀 |
| `POST /api/feedback` | `{uid, mark}` | 有用／沒用 |
| `POST /api/kg-flag` | `{uid, flag}` | 事實有誤（`wrong`）或取消（`""`）；只收出現在某份晨報裡的 uid |
| `GET /api/kg/item/<uid>[,<uid>…]?depth=1\|2` | `{uids, items, depth, seeds, events, links, flags, truncated}` | 知識圖譜：以這幾則（最多 10 則）的事件為中心，沿關係往外 `depth` 層。`items` 是這幾則本身（`uid, date, title, url, source, report`），`seeds` 是它們的事件 id |
| `GET /api/kg/entity/<key>?days=` | `{entity: {key, name, type, total}, days, since, related, events, links, flags, truncated}` | 提到這個實體的事件，近 `days` 天（1～365，預設 90），新的優先；`key` 是正規化後的名稱（`kg_store.norm`，Unicode，網址要百分比編碼），不是正規化形式的一律 404。`related` 是同一批事件裡常一起出現的實體（`{key, name, type, count}`） |
| `GET /api/kg/entities?q=` | `{q, available, since, entities: [{key, name, type, count, last}]}` | `q` 空白＝近 30 天最常出現的 30 個；否則 key 含 `norm(q)` 的實體（不限日期，最多 50 個），事件多的在前。還沒建過 KG 時 `available` 是 false |
| `GET /api/auth-health` | `{state, kind, checked_at}` | 認證健康狀態，所有人可讀 |
| `GET /api/auth`、`POST /api/auth/token\|test\|revoke` | | OAuth token 狀態與管理，只限本機 |

- 知識圖譜的 `events` 舊到新，每個是 `{id, uid, date, summary, quote, title, url, source, status, sources, report, entities: [{key, name, type}]}`；`status` 是 `active`／`disputed`／`invalid`，與 writer 寫背景時同一套規則（shared 的 `kg_store.statuses`，套用 `kg_flags.jsonl`）；`report` 是收錄這則的最新一份晨報（沒有是 null）。`links` 只含兩端都在圖裡的（`{src, dst, kind, quote, date}`，src 是較新的那個）。一張圖最多 150 個事件，超過時 `truncated`。`state/kg.sqlite` 只用唯讀連線開（不建表、不寫入）；檔案不存在時 item／entity 回 404。摘要、引句、標題是外部網頁萃取來的文字，前端只當純文字顯示。這三條不回 ETag、不進離線副本。
- `GET /api/today`、`/api/reports`、`/api/reports/<date>` 回 `ETag`（對最後的 JSON 取雜湊，所以標記變了也會變）；`If-None-Match` 相符時回 `304`、沒有 body。
  比對時忽略 `W/`（Cloudflare 可能把強 ETag 改成弱的）。`Cache-Control` 照舊是 `no-store`：條件請求由前端的離線副本同步自己帶，不靠瀏覽器的 HTTP cache。
- 頁面路由（清單見 [ui/README.md](../web-ui/README.md)）回 `index.html`，不認得的路徑回 404 的 `index.html`；新增前端路由時，`server.py` 的 `SPA_ROUTES` 要同步。
- `/feedback/<uid>?v=…` 是 email 連結進來的確認頁：GET 只顯示確認頁（`GET /api/feedback/<uid>` 純讀取），按了確認才 `POST /api/feedback`。email 端見 [notify/README.md](../../packages/notify/README.md)。

## 自我檢查

```bash
uv run --project apps/web-server newsletter-web --selftest
```

涵蓋 API、靜態檔、寫入／覆蓋／取消的讀回、知識圖譜（狀態與 flags、範圍、Unicode key、錯誤、唯讀）、`/auth` 的本機限制、`auth_health.json` 的讀寫與 `/api/auth-health`，以及 manifest／`sw.js`／圖示的 Content-Type 與 `no-cache`、heartbeat 的回應（`no-store`、不寫入任何檔案）。前端測試見 [ui/README.md](../web-ui/README.md)。
