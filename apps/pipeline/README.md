# pipeline

每日流程的編排：抓取 → agent 寫報告 → render → 寄信，整條包在一把防重疊的鎖裡。專案全貌見[根目錄 README](../../README.md)。

站在依賴圖最上層：依賴 `shared`、`agent`、`notify`，**沒有任何成員可以依賴它**（放在 `apps/` 的模組都是這樣，`deploy/check_boundaries.py` 檢查）。
`agent` 不認得 email、`notify` 不認得 agent；把兩邊串起來是這個模組唯一的工作。純標準庫。

```
src/newsletter_pipeline/
  daily.py             run_pipeline()：鎖 → 紀錄 → 四步 → 失敗語意；newsletter-run 的入口與自我檢查
  service.py           newsletter-scheduler：每日時鐘、補跑、GET /status、POST /run（Docker 的 scheduler 容器跑它）
  service_selftest.py  newsletter-scheduler --selftest
```

## 用法

```bash
uv run --project apps/pipeline newsletter-run                    # 完整跑一遍。會呼叫 Claude、會寄信，和排程跑的是同一件事
uv run --project apps/pipeline newsletter-run --lookback 72      # 額外參數轉給 newsletter-fetch
uv run --project apps/pipeline newsletter-run --selftest         # 用假步驟驗證，不連網、不寄信
```

平常不必直接跑它：Docker 版由 `newsletter-scheduler` 呼叫，systemd／cron 經根目錄的 `run_daily.sh`（補 PATH、載入機密、導向 log，再 exec 它）。
直接跑時輸出印在終端機，不進 `logs/`。

## 行為

- 四步各自是獨立的程序（`python -m newsletter_agent.run` 等），由這裡依序啟動；任一步失敗就停下，exit code 是那一步的，被訊號殺掉的算 128+訊號。
- render 的 stdout（一行 JSON）原封不動餵給 send 的 stdin；其餘 stdout／stderr 和這裡的進度行（`=== 時間 === 步驟`）一起進 log。
- **鎖**：整條流程握著 `state/pipeline.lock`（`shared` 的 `lock.locked(timeout=0)`）。搶不到就**不排隊**、exit **75**（`EX_TEMPFAIL`），log 留一行說明。
  程序死掉鎖就放掉，不會留下過期的鎖。這把鎖只防「兩條 pipeline 同時跑」；各個檔案自己的讀寫排隊見 [shared/README.md](../../packages/shared/README.md)「檔案鎖」。

- **執行紀錄**：拿到鎖才寫 `state/last_run.json`（整檔取代）：開始時 `{date, trigger, started_at, finished_at: null, exit_code: null}`，結束補上後兩個。
  `date` 是 `config/config.json` 時區的日期；`trigger` 是 `schedule`／`catchup`／`manual`（`/run`）／`cli`。搶不到鎖不寫。
  有 `started_at` 沒 `finished_at`：鎖被握著＝還在跑，鎖空著＝被打斷（程序被 SIGKILL、斷電）。

| exit code | 意義 |
| --- | --- |
| 0 | 全部完成 |
| 1～6 | `newsletter-agent` 的（見 [agent/README.md](../../packages/agent/README.md)「exit code」） |
| 2、3 | `newsletter-fetch` 的（來源設定有誤、一則都沒抓到） |
| 75 | 另一條 pipeline 正在跑，這次沒執行 |
| 143 | 被要求停止（`newsletter-scheduler` 收到 SIGTERM，轉給執行中的那一步） |
| 78 | 由 `run_daily.sh` 回傳：env 檔權限太鬆 |

數字會重疊（agent 和 fetch 都用 2），要知道是哪一步，看 log 最後一行 `失敗，exit N：<步驟>`。

## 排程服務（newsletter-scheduler）

```bash
uv run --project apps/pipeline newsletter-scheduler              # 0.0.0.0:8790（--port／NEWSLETTER_SCHEDULER_PORT）；時間 NEWSLETTER_RUN_AT，預設 08:00
uv run --project apps/pipeline newsletter-scheduler --selftest   # 時鐘、補跑判斷、HTTP、SIGTERM，不連網
```

每 30 秒醒來看一次牆上時鐘（主機睡眠、校時都不怕），依 `state/last_run.json` 和鎖決定要不要跑：

| 現在 | 今天的紀錄 | 鎖 | 動作 |
| --- | --- | --- | --- |
| 還沒到 HH:MM | 任何 | — | 等 |
| 已過 | 沒有（或是昨天的） | 空 | 跑；晚了超過 5 分鐘記 `catchup`，否則 `schedule` |
| 已過 | 跑完了（不論成敗） | — | 等明天；失敗要重跑用 `POST /run` |
| 已過 | 開始了沒結束 | 空 | 上次被打斷，重跑 |
| 已過 | 任何 | 被握著 | 別條正在跑，下次再看 |

| 介面 | 回應 |
| --- | --- |
| `GET /status` | `{state: idle\|running, next_run_at, current: {trigger, started_at} \| null, last: last_run.json \| null}` |
| `POST /run` | 202 `{started_at}`；已經在跑（這裡或別的程序握著鎖）回 409。不收 body |

沒有登入，靠網路隔離：compose 不 publish 這個 port，`scheduler` 只在自己的 `be` 網路（見 [deploy/README.md](../../deploy/README.md)）。
輸出進 `logs/<YYYY-MM>.log`（月份以開始時間為準），服務自己的訊息印 stdout；失敗時附上這次 log 的最後 20 行。
SIGTERM：轉給執行中的那一步、等它結束、紀錄記成 143，然後離開；被打斷的那天下次啟動會補跑。

## 加一個步驟

在 `daily.py` 的 `MODULES` 加一行模組名，在 `_run` 依序加一次 `go(...)`，並在 `selftest` 的假步驟裡涵蓋它。
