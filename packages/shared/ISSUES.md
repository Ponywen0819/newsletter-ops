# shared 已知問題

最後更新：2026-09-28。每則格式：現象 → 原因 → 建議。解決後移到文末「已解決」並寫日期。編號沿用原本根目錄 `ISSUES.md` 的，不重排。

## 未解決

### 低：已知限制，先記錄

**12. Metrics 算不到 WebFetch 內部的 token**（`metrics.py`）
- WebFetch 用來摘要網頁的小模型不在 session 紀錄裡，拿不到。替代指標是 `tools.WebFetch` 的呼叫次數與秒數。

## 已解決（2026-09-28）

- **Metrics 測試紀錄被刪、同一天多次執行只剩最後一筆**（原第 12 點）：測試紀錄會和正式紀錄混在同一個檔、分不出來，所以之前測試完都被手動刪掉。現在每筆紀錄帶 `label`（`NEWSLETTER_RUN_LABEL`，預設 `prod`），`summary` 每次執行一行、預設只列 prod。`model_seconds` 改名 `non_tool_seconds`。
