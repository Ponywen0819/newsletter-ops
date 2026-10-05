# shared

共用底層，只用標準庫、不依賴其他模組；`agent`、`notify`、`web` 都依賴它。專案全貌見[根目錄 README](../README.md)，已知問題見 [ISSUES.md](ISSUES.md)。

```
src/newsletter_shared/
  paths.py               repo 根目錄 ROOT 的唯一來源；其他模組一律從這裡取，不自己用 __file__ 推算
  report_data.py         報告 Markdown → 結構化資料：報告格式（SKILL.md 規定的子集）唯一的解析與驗證者，網頁與 email 共用
                         （newsletter-report-check 用它驗證 reports/<date>.md）
  feedback.py            回饋標記格式、跨程序檔案鎖、從報告收集人工標記（newsletter-feedback）
  metrics.py             量測層：debug 開啟時記錄各階段耗時與 Claude token 用量（newsletter-metrics）
  auth_store.py          OAuth token 的儲存與來源解析，agent 與 web 共用
```

## 報告格式（`report_data.py`）

報告格式（`SKILL.md` 規定的 Markdown 子集）有改動時**只改 `report_data.py`**：網頁與 email 共用同一個解析器。哪些條目可以投票
（`list`／`mark` 區塊的 `votable`：主要新聞才有、「其餘收錄」沒有）也在那裡決定，兩邊版型只負責照畫。

```bash
uv run python -m newsletter_shared.report_data --selftest   # 驗證解析與 votable
```

`uv run newsletter-render --selftest` 驗證 email 輸出，見 [notify/README.md](../notify/README.md)。

## 回饋標記（`feedback.py`）

報告每則末尾有一行：

```markdown
<!-- mark:    uid=3f9a1c2b0d4e5678 -->
```

看完隨手填 `+`（有用）、`-`（沒用）、`++` / `--`（強烈），Markdown 預覽時不會顯示。
跑 `uv run newsletter-feedback` 收集到 `state/feedback.jsonl`，重複標記以最新為準。
報告裡的標記只匯入 `feedback.jsonl` 還沒有紀錄的那一則；已有紀錄的（含網頁標的）以 jsonl 為準，不會被覆蓋。

網頁（[web/README.md](../web/README.md)）與 `feedback.py` 可以同時跑：兩邊都只 append、不改寫舊內容，並用 `state/feedback.jsonl.lock` 排隊。
`uv run newsletter-feedback --selftest` 涵蓋這部分（含併發 append）。

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
- 用 `agent_run.py` 跑時的用量紀錄見 [agent/README.md](../agent/README.md)「用量與成本」。

## OAuth token（`auth_store.py`）

只負責 token 的儲存與來源解析，`agent`（`agent_run`）與 `web`（`/auth` 頁面）共用。使用政策（只走訂閱額度、不使用 API key）與取得方式見 [agent/README.md](../agent/README.md)「認證」。

- **來源的優先序**：`state/oauth_token.json`（頁面存的；`NEWSLETTER_TOKEN_FILE` 可改路徑，權限 600、不進版控）→ 環境變數 `CLAUDE_CODE_OAUTH_TOKEN`。

```bash
uv run python -m newsletter_shared.auth_store --selftest
```
