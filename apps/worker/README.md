# worker

網頁以外的常駐服務：每日排程與補跑，以及每日流程的編排（蒐集 → 寫報告 → render → 寄信，整條包在一把防重疊的鎖裡）。專案全貌見[根目錄 README](../../README.md)。

站在依賴圖最上層：依賴 `shared`、`collect`、`writer`、`notify`，**沒有任何成員可以依賴它**（放在 `apps/` 的模組都是這樣，`deploy/check_boundaries.py` 檢查）。
`collect`、`writer`、`notify` 彼此不認得，只透過檔案交接（curated JSON → 報告 → email）；把它們依序串起來是這裡的事。純標準庫。

```
src/newsletter_worker/
  daily.py             run_pipeline()：鎖 → 紀錄 → 四步 → 失敗語意，與它的自我檢查
  service.py           入口 worker：每日時鐘、補跑、GET /status、POST /run，以及 --once（Docker 的 worker 容器、systemd 的 newsletter-worker.service 跑它）
  service_selftest.py  服務的自我檢查
```

## 用法

```bash
npx nx run worker:once                   # 完整跑一遍。會呼叫 Claude、會寄信，和排程跑的是同一件事
npx nx run worker:once --lookback 72     # 額外參數轉給 newsletter-collect
npx nx run worker:start                  # 常駐排程服務，見下面「排程服務」
npx nx run worker:selftest               # 用假步驟驗證，不連網、不寄信
```

Nx 只是包一層：`once` 就是 `uv run --project apps/worker worker --once`，其餘同理。`--once` 的輸出印在終端機，不進 `logs/`。

## 行為

- 四步各自是獨立的程序（`python -m newsletter_collect.run` 等），由這裡依序啟動；任一步失敗就停下，exit code 是那一步的，被訊號殺掉的算 128+訊號。
- render 的 stdout（一行 JSON）原封不動餵給 send 的 stdin；其餘 stdout／stderr 和這裡的進度行（`=== 時間 === 步驟`）一起進 log。
- **鎖**：整條流程握著 `state/pipeline.lock`（`shared` 的 `lock.locked(timeout=0)`）。搶不到就**不排隊**、exit **75**（`EX_TEMPFAIL`），log 留一行說明。
  程序死掉鎖就放掉，不會留下過期的鎖。這把鎖只防「兩條 pipeline 同時跑」；各個檔案自己的讀寫排隊見 [shared/README.md](../../packages/shared/README.md)「檔案鎖」。

- **執行紀錄**：拿到鎖才寫 `state/last_run.json`（整檔取代）：開始時 `{date, trigger, started_at, finished_at: null, exit_code: null}`，結束補上後兩個。
  `date` 是 `config/config.json` 時區的日期；`trigger` 是 `schedule`／`catchup`／`manual`（`/run`）／`cli`。搶不到鎖不寫。
  有 `started_at` 沒 `finished_at`：鎖被握著＝還在跑，鎖空著＝被打斷（程序被 SIGKILL、斷電）。

| exit code | 意義 |
| --- | --- |
| 0 | 全部完成 |
| 2、3 | `newsletter-collect` 的（來源設定有誤、一則都沒抓到）；搜尋與補原文失敗不算失敗 |
| 1～6 | `newsletter-write` 的（見 [writer/README.md](../../packages/writer/README.md)「exit code」） |
| 75 | 另一條 pipeline 正在跑，這次沒執行 |
| 143 | 被要求停止（`worker` 收到 SIGTERM，轉給執行中的那一步） |

數字會重疊（collect 和 write 都用 2、3），要知道是哪一步，看 log 最後一行 `失敗，exit N：<步驟>`。

## 排程服務

```bash
npx nx run worker:start                  # 0.0.0.0:8790（--port／NEWSLETTER_WORKER_PORT）；時間 NEWSLETTER_RUN_AT，預設 08:00
npx nx run worker:start --port 9000      # 參數直接接在後面
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

沒有登入，靠網路隔離：compose 不 publish 這個 port，`worker` 只在自己的 `be` 網路；systemd 版綁 `127.0.0.1`（見 [deploy/README.md](../../deploy/README.md)）。
輸出進 `logs/<YYYY-MM>.log`（月份以開始時間為準），服務自己的訊息印 stdout；失敗時附上這次 log 的最後 20 行。
SIGTERM：轉給執行中的那一步、等它結束、紀錄記成 143，然後離開；被打斷的那天下次啟動會補跑。

## 加一個步驟

在 `daily.py` 的 `MODULES` 加一行模組名，在 `_run` 依序加一次 `go(...)`，並在 `selftest` 的假步驟裡涵蓋它。
