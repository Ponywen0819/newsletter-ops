# Spec: web

屬於 [SPEC.md](SPEC.md) 的 Capability Map。依賴：shared、agent（執行期，`--auth-check`）。**在 agent、notify 之後動工。**

## Objective

晨報網頁：JSON API（Python，純標準庫）＋ React 前端。後端與前端共用同一份 HTTP 契約（`web.py` 開頭的 API 清單 ↔ `web/ui/src/types.ts`），所以放同一個模組、一起改、一起出貨。

## 內容

| 原檔 | 新位置 |
|---|---|
| `src/web.py` | `web/server/src/newsletter_web/web.py`（921 行，不拆） |
| `web/{index.html, package.json, package-lock.json, tsconfig.json, vite.config.ts, src/}` | `web/ui/` 同名 |
| （新增） | `web/server/pyproject.toml`：依賴 `newsletter-shared`、`newsletter-agent`；console script `newsletter-web` → `web:main` |

## 改動（只列會壞的地方）

| 位置 | 現在 | 之後 |
|---|---|---|
| 靜態檔目錄 `self.dist`（約第 187 行）與 selftest 的假 dist | `root/"web"/"dist"` | `root/"web"/"ui"/"dist"` |
| `BUILD_COMMAND` 與 503 頁的說明字串 | `cd web && npm install && npm run build` | `cd web/ui && npm install && npm run build` |
| 驗證 token 的子程序（約第 147 行） | `[sys.executable, ROOT/"src"/"agent_run.py", "--auth-check", "--token-from-env"]` | `[sys.executable, "-m", "newsletter_agent.agent_run", "--auth-check", "--token-from-env"]`，`cwd=ROOT`、env 傳 token 的方式不變 |
| `web/ui/vite.config.ts` | `/api` 代理到 `127.0.0.1:${NEWSLETTER_WEB_PORT ?? 8787}` | 不變 |
| docstring 與 README 裡的 `cd web && …`、`src/web.py` | — | 同步更新 |

`SPA_ROUTES`、`is_local_request`（`/auth` 只服務本機）、CSP、API 路徑與 JSON 欄位**一律不動**。

## 要特別小心

- 反轉前提：`web.py` 不再 import `render_email`（`refactor/email-from-report-data` 已把 `plain` 移到 `report_data`）。若該分支沒先合進 main，這個模組不能動工。
- `/auth` 的 token 驗證要「真的呼叫一次 SDK」，所以 web 的 venv 必須有 `newsletter-agent`；Docker 映像同一個 venv，沒問題，但 `web/server/pyproject.toml` 必須如實宣告這條依賴。

## Success Criteria

1. `newsletter-web --selftest` 通過（含假 dist 路徑已改、auth-check 子程序路徑已改）。
2. `npm --prefix web/ui test`、`typecheck`、`build` 通過；`web/ui/dist` 產生後 `newsletter-web` 能提供 `/`、`/reports`、`/reports/<date>`。
3. 同一份報告，`GET /api/reports/<date>` 與 `GET /api/today` 的 JSON 搬家前後逐位元相同。
4. 本機開 `/auth`，貼測試 token → 觸發 `python -m newsletter_agent.agent_run --auth-check` 並得到「無效」回應（用假 token，不消耗額度）。
5. `grep -rn newsletter_notify web/server` 無結果；`check_boundaries.py` 通過。

## Boundaries

- Always：用 `git mv` 搬 `web/` 內容；`.gitignore`／`.dockerignore` 白名單在同一個 commit 更新。
- Ask first：改 API 路徑或 JSON 欄位；拆 `web.py`；改 CSP。
- Never：把 `/auth` 開放給非本機；動 `state/oauth_token.json` 的內容。
