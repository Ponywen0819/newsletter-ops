# web/ui

晨報網頁的前端：Vite + React + TypeScript。後端 `newsletter-web`（[web/README.md](../README.md)）只出 JSON，頁面全由前端畫；
晨報是 shared 的 `report_data.py` 解析出的結構（標題、段落、巢狀清單、每則的 mark、資料來源），前端依結構排版。
版面是自適應的（手機單欄、筆電左側欄＋內文，樣式在 `src/styles.css`）。筆電版面由路由決定、不看內容：`/`、`/reports`、`/reports/<date>` 是左側欄＋內文（頁面用 `ReaderShell`，載入中與出錯也一樣，站內切換外框才不會跳），
`/feedback`、`/auth`、找不到頁面維持單欄卡片（`App.tsx` 的兩組 layout route）；email 吃同一份結構，由 notify 的 `render_email.py` 排成 inline-CSS HTML，兩邊版型各自維護。

```bash
cd web/ui
npm install          # 第一次；需要 Node ^20.19 或 >=22.12
npm run build        # 型別檢查 + 建置到 web/ui/dist，newsletter-web 直接提供
npm run dev          # 開發：Vite 在 :5173，/api 代理到 newsletter-web（需另外用 uv run newsletter-web 開後端）
npm test             # Vitest + Testing Library：元件與路由
npm run typecheck
```

- 路由：`/` 當日、`/reports` 歷史、`/reports/<date>` 單日、`/feedback/<uid>?v=…` email 連結的確認頁、`/auth` 授權（只限本機）。後端對這幾條回 `index.html`，
  其他不認得的路徑回 404 的 `index.html`（前端畫「找不到頁面」）。新增前端路由時，`web/server/src/newsletter_web/web.py` 的 `SPA_ROUTES` 要同步。API 清單見 [web/README.md](../README.md)。
- 開發時 Vite 的代理不改 `Host`、不加 `X-Forwarded-*`，所以後端仍把它當本機，`/auth` 可以正常測。後端埠號不是 8787 時，
  前端用同一個環境變數：`NEWSLETTER_WEB_PORT=8790 npm run dev`。
- HTML 回應帶 `Content-Security-Policy`（只許同源的腳本與樣式），所以前端不能有行內 `<script>`／`style="…"`。
- 深色模式：預設跟系統（`prefers-color-scheme`），頁首「配色」可改成淺色／深色，選擇存在瀏覽器的 localStorage（key `theme`，只收 `light`／`dark`，沒有＝自動），後端不知道。
  `<html data-theme>` 是唯一真相；顏色在 `styles.css` 的 `:root` 用 `light-dark(淺, 深)` 定義（需要 2024 年中以後的瀏覽器）。
  `public/theme-init.js` 在 `<head>` 同步套用儲存的主題，避免先閃一下錯的顏色；CSP 不許行內腳本，所以它必須是外部檔。
  放進 `public/` 的檔案要同時放行 `.gitignore`（白名單）、`.dockerignore`，並在 `Dockerfile` 的前端階段 COPY，否則不會進版控或映像。

## PWA（可安裝，離線讀開過的晨報）

網頁可以裝成 App：Android／桌機 Chrome 選「安裝應用程式」（桌機在網址列右側），iOS Safari 用「分享 → 加入主畫面」。裝好後以獨立視窗開啟，圖示是藍底的「報」。
需要 HTTPS 或 `localhost`（Tunnel 的網域本來就是 HTTPS）。**只有 `npm run build` 的產物有 service worker**，`npm run dev` 不註冊，免得開發時被舊快取干擾。

- **離線**：開過的今日晨報、歷史列表、單日晨報，沒網路或連不上伺服器時仍能打開，標頭下方出現「離線中」或「連不上伺服器」；沒開過的日期顯示「讀取失敗」。有網路時永遠先拿最新的（標記狀態、新產出的晨報不會被舊快取蓋住）。離線時按「有用／沒用」會顯示「儲存失敗」，不會排隊補送。
- **連線偵測**（`GET /api/heartbeat`，`src/connectivity.ts`）：回 `200 {"ok": true}`、`Cache-Control: no-store`，不讀檔、不寫入。前端用它判斷四種狀態，橫幅與資料請求都看同一份結果：

  | 狀態 | 判定 | 畫面 |
  | --- | --- | --- |
  | 正常 | heartbeat 2xx | 橫幅是空的；資料網路優先，更新快取 |
  | 離線 | `navigator.onLine` 為 `false`（不探測） | 「離線中」；直接讀快取 |
  | 連不上伺服器 | 網路錯誤、**2 秒**沒回應、或任何非 2xx（Tunnel 在、origin 掛時 Cloudflare 回 502／530） | 「連不上伺服器」；直接讀快取，不等請求逾時 |
  | 登入逾時 | heartbeat 被 302 到別的來源（`redirect: 'manual'` 得到 `opaqueredirect`），或被 Access 擋成 401／403 | 「登入已逾時」＋「重新登入」按鈕；**不顯示快取**，資料請求顯示「讀取失敗」 |

  探測時機：App 啟動、`online`／`offline` 事件、回到前景、資料請求前；結果重用 **5 秒**（成功與失敗都重用，同時多個請求共用一次探測），不做常駐輪詢。狀態從異常回到正常時，今日、歷史列表、單日晨報會自動重抓。
  只有 `/api/today`、`/api/reports`、`/api/reports/<date>` 走這個判斷；`POST`、`/api/session`、`/api/auth*`、`/api/feedback/*` 照舊直接送出。
- **更新**：新版 service worker 直接接手（`skipWaiting`＋`clientsClaim`），不提示重新整理；舊版的 precache 自動清掉。
- **檔案**：`vite.config.ts`（`vite-plugin-pwa`：manifest、註冊）、`src/sw.ts`（service worker 的路由）、`public/icons/`（`icon.svg` 是來源，另有 192、512、maskable 512、apple-touch-icon 180 的 PNG；換圖示就重新輸出這四張）。
  `public/` 的檔案原樣複製到 `dist/` 根目錄；它被 `.gitignore`、`.dockerignore` 放行，`Dockerfile` 也有 `COPY web/ui/public`，新增這類目錄時三處都要加。

**`sw.ts` 的原則**（改它之前先讀）

- **只快取** `GET /api/today`、`/api/reports`、`/api/reports/<date>`，且只存 200。`POST`、`/api/session`（`local` 因請求而異）、`/api/auth*`（能寫入憑證）、`/api/feedback/*` **不註冊路由、不經過 service worker**；新增路由前先確認不會碰到它們。`/api/heartbeat` 也不註冊路由：它一定要打到網路，不能被快取（`pwa.test.ts` 斷言建置後的 `sw.js` 不含它）。
- **「連不連得上」由頁面判斷，不是 service worker**：頁面的 `request()`（`api.ts`）先問 heartbeat，連不上就自己讀 `API_CACHE`（`cacheName.ts`，`sw.ts` 寫入的同一個 cache），登入逾時則不讀。service worker 的 `NetworkFirst` 只多一個保險逾時（`networkTimeoutSeconds`，heartbeat 通過後、請求途中才斷線時用）。這樣橫幅與資料用同一份狀態、只需一次探測，也不會有 service worker 默默回快取而頁面不知道的情況。
- **導覽一律先走網路**，只有網路錯誤才回 precache 的 `index.html`。Access 逾時時回的 302 要原樣交給瀏覽器去登入，不能拿快取的畫面蓋掉；導覽的回應不進任何快取，登入頁不可能被存起來。
  導覽路由必須排在 precache 的路由**前面**（路由先註冊的先贏；否則 `/` 會直接吃快取），所以用 `precache()`＋`addRoute()`，不要改回 `precacheAndRoute()`。
- manifest 的 `<link>` 要帶 `crossorigin="use-credentials"`（設定裡的 `useCredentials: true`）：瀏覽器抓 manifest 預設不帶 cookie，會被 Access 擋成登入頁，在正式站就裝不起來。這在本機測不出來。
- 註冊用外部的 `registerSW.js`（`injectRegister: 'script'`）；不能改成行內，CSP 不允許行內 `<script>`。
- PWA 的檔案（`manifest.webmanifest`、`sw.js`、圖示）也在 Access 後面，不要為了安裝方便而對它們開 bypass。

**測試**：`npm test` 的 `pwa.test.ts` 會建置到暫存目錄（不動 `dist/`），檢查 manifest 欄位、`crossorigin`、沒有行內 script、service worker 的路由範圍與順序、cache 名稱與保險逾時；
`connectivity.test.ts`（判定、逾時、重用、事件、恢復計數）、`api.test.ts`（連不上讀快取、登入逾時不讀、不相關的請求不受影響）、`Layout.test.tsx`（橫幅）鎖住連線偵測；
後端 `uv run newsletter-web --selftest` 檢查 manifest／`sw.js`／圖示的 Content-Type 與 `no-cache`，以及 heartbeat 的回應、`no-store`、不寫入任何檔案。

**正式站驗收**（Docker 與 systemd 兩種部署共用，部署步驟見 [deploy/README.md](../../deploy/README.md)；Access 的行為只有在真實網域上測得到）

| 檢查 | 預期 |
| --- | --- |
| **未登入**：`curl -sI https://news.example.com/manifest.webmanifest`、`curl -sI https://news.example.com/sw.js` | 都是 `302` 到 `cloudflareaccess.com`（或 `403`），**絕不能是 200**（PWA 的檔案也要在 Access 後面） |
| Android Chrome 登入後開首頁 | 選單出現「安裝應用程式」；裝好後以獨立視窗開到今日晨報 |
| iOS Safari 登入後「分享 → 加入主畫面」 | 圖示是藍底「報」（不是預設的字母）；以獨立視窗開啟 |
| **未登入**：`curl -sI https://news.example.com/api/heartbeat` | `302` 到 `cloudflareaccess.com`（或 `403`），**絕不能是 200**；前端把 `opaqueredirect`、401、403 都當成登入逾時，若看到別的狀態碼要回頭檢查 `connectivity.ts` 的判定 |
| 手機開飛航模式，開已安裝的 app | 看得到最近開過的晨報，標頭下方有「離線中」 |
| 停掉 origin（`docker compose stop` 或 `systemctl stop`，Tunnel 還在）後開已安裝的 app | 約 2 秒內看到快取的晨報，標頭下方是「連不上伺服器」（不是「離線中」）；把 origin 開回來、回到前景，橫幅消失、資料變最新 |
| 清掉 Access 登入（或等逾時）後開 app | 被帶到 Access 登入頁，登入後回到晨報（不是白畫面，也不是舊的畫面停住） |
| 開著 app 時讓 Access 登入逾時，再點「歷史晨報」 | **不顯示快取**：標頭下方是「登入已逾時」與「重新登入」，頁面是「讀取失敗」；按「重新登入」被帶到登入頁，登入後回到晨報 |

iOS 主畫面的圖示若是預設字母，很可能是 iOS 抓圖示時沒帶 Access 登入；可以在 Access 對 `/icons/*` 加一條 bypass policy。圖示不含機密，但這是改安全設定，範圍請只限 `/icons/*`。
