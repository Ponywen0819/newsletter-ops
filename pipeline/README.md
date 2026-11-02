# pipeline

每日流程的編排：抓取 → agent 寫報告 → render → 寄信，整條包在一把防重疊的鎖裡。專案全貌見[根目錄 README](../README.md)。

站在依賴圖最上層：依賴 `shared`、`agent`、`notify`，**沒有任何成員可以依賴它**（`deploy/check_boundaries.py` 檢查）。
`agent` 不認得 email、`notify` 不認得 agent；把兩邊串起來是這個模組唯一的工作。純標準庫。

```
src/newsletter_pipeline/
  daily.py     run_pipeline()：鎖 → 四步 → 失敗語意；newsletter-run 的入口與自我檢查
```

## 用法

```bash
uv run newsletter-run                    # 完整跑一遍。會呼叫 Claude、會寄信，和排程跑的是同一件事
uv run newsletter-run --lookback 72      # 額外參數轉給 newsletter-fetch
uv run newsletter-run --selftest         # 用假步驟驗證，不連網、不寄信
```

平常不必直接跑它：排程經根目錄的 `run_daily.sh` 呼叫（補 PATH、載入機密、導向 log，再 exec 它）。
直接跑時輸出印在終端機，不進 `logs/`。

## 行為

- 四步各自是獨立的程序（`python -m newsletter_agent.run` 等），由這裡依序啟動；任一步失敗就停下，exit code 是那一步的，被訊號殺掉的算 128+訊號。
- render 的 stdout（一行 JSON）原封不動餵給 send 的 stdin；其餘 stdout／stderr 和這裡的進度行（`=== 時間 === 步驟`）一起進 log。
- **鎖**：整條流程握著 `state/pipeline.lock`（`shared` 的 `lock.locked(timeout=0)`）。搶不到就**不排隊**、exit **75**（`EX_TEMPFAIL`），log 留一行說明。
  程序死掉鎖就放掉，不會留下過期的鎖。這把鎖只防「兩條 pipeline 同時跑」；各個檔案自己的讀寫排隊見 [shared/README.md](../shared/README.md)「檔案鎖」。

| exit code | 意義 |
| --- | --- |
| 0 | 全部完成 |
| 1～6 | `newsletter-agent` 的（見 [agent/README.md](../agent/README.md)「exit code」） |
| 2、3 | `newsletter-fetch` 的（來源設定有誤、一則都沒抓到） |
| 75 | 另一條 pipeline 正在跑，這次沒執行 |
| 78 | 由 `run_daily.sh` 回傳：env 檔權限太鬆 |

數字會重疊（agent 和 fetch 都用 2），要知道是哪一步，看 log 最後一行 `失敗，exit N：<步驟>`。

## 加一個步驟

在 `daily.py` 的 `MODULES` 加一行模組名，在 `_run` 依序加一次 `go(...)`，並在 `selftest` 的假步驟裡涵蓋它。
