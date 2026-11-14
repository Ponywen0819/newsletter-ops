# worker

網頁以外的常駐服務：每日排程與補跑，以及每日流程的編排（蒐集 → 寫報告 → render → 寄信，整條包在一把防重疊的鎖裡）。專案全貌見[根目錄 README](../../README.md)。

站在依賴圖最上層：依賴 `shared`、`collect`、`writer`、`notify`，**沒有任何成員可以依賴它**（放在 `apps/` 的模組都是這樣，`deploy/check_boundaries.py` 檢查）。
`collect`、`writer`、`notify` 彼此不認得，只透過檔案交接（curated JSON → 報告 → email）；把它們依序串起來是這裡的事。純標準庫。

```
src/newsletter_worker/
  daily.py             run_pipeline()：鎖 → 紀錄 → 四步 → 失敗語意
  service.py           入口 worker：每日時鐘、補跑、GET /status、POST /run、POST /job，以及 --once（Docker 的 worker 容器、systemd 的 newsletter-worker.service 跑它）
tests/                 pytest；helpers.py 的假步驟取代四步，不連網、不寄信
```

## 用法

```bash
npx nx run worker:once                   # 完整跑一遍。會呼叫 Claude、會寄信，和排程跑的是同一件事
npx nx run worker:once --lookback 72     # 額外參數轉給 newsletter-collect
npx nx run worker:start                  # 常駐排程服務，見下面「排程服務」
npx nx run worker:test                   # 用假步驟驗證，不連網、不寄信
```

Nx 只是包一層：`once` 就是 `uv run --project apps/worker worker --once`，其餘同理。`--once` 的輸出印在終端機，不進 `logs/`。

## 行為

- 四步各自是獨立的程序（`python -m newsletter_collect.run` 等），由這裡依序啟動；任一步失敗就停下，exit code 是那一步的，被訊號殺掉的算 128+訊號。
- render 的 stdout（一行 JSON）原封不動餵給 send 的 stdin；其餘 stdout／stderr 和這裡的進度行（`=== 時間 === 步驟`）一起進 log。
- **鎖**：整條流程握著 `state/pipeline.lock`（`shared` 的 `lock.locked(timeout=0)`）。搶不到就**不排隊**、exit **75**（`EX_TEMPFAIL`），log 留一行說明。
  程序死掉鎖就放掉，不會留下過期的鎖。這把鎖只防「兩條 pipeline 同時跑」；各個檔案自己的讀寫排隊見 [shared/README.md](../../packages/shared/README.md)「檔案鎖」。

- **執行紀錄**：拿到鎖才寫 `state/last_run.json`（整檔取代）：開始時 `{date, trigger, started_at, finished_at: null, exit_code: null, step: null}`，結束補上後三個（`step` 是失敗的那一步，成功是 null）；web 的今日頁讀它顯示失敗。
  `date` 是 `config/config.json` 時區的日期；`trigger` 是 `schedule`／`catchup`／`retry`／`manual`（`/run`）／`cli`。搶不到鎖不寫。
  有 `started_at` 沒 `finished_at`：鎖被握著＝還在跑，鎖空著＝被打斷（程序被 SIGKILL、斷電）。

| exit code | 意義 |
| --- | --- |
| 0 | 全部完成 |
| 2、3 | `newsletter-collect` 的（來源設定有誤、一則都沒抓到）；搜尋與補原文失敗不算失敗 |
| 1～6 | `newsletter-write` 的（見 [writer/README.md](../../packages/writer/README.md)「exit code」） |
| 75 | 另一條 pipeline 正在跑，這次沒執行 |
| 124 | 某一步超過時限（collect 30 分、write 60 分、render／send 5 分，`daily.TIMEOUTS`）被停掉；會自動重跑一次 |
| 143 | 被要求停止（`worker` 收到 SIGTERM，轉給執行中的那一步） |

數字會重疊（collect 和 write 都用 2、3），要知道是哪一步，看 log 最後一行 `失敗，exit N：<步驟>`。

## 排程服務

```bash
npx nx run worker:start                  # 0.0.0.0:8790（--port／NEWSLETTER_WORKER_PORT）；時間 NEWSLETTER_RUN_AT，預設 08:00；認證驗證 NEWSLETTER_AUTH_CHECK_AT，預設 07:30
npx nx run worker:start --port 9000      # 參數直接接在後面
```

每 30 秒醒來看一次牆上時鐘（主機睡眠、校時都不怕），依 `state/last_run.json` 和鎖決定要不要跑：

| 現在 | 今天的紀錄 | 鎖 | 動作 |
| --- | --- | --- | --- |
| 還沒到 HH:MM | 任何 | — | 等 |
| 已過 | 沒有（或是昨天的） | 空 | 跑；晚了超過 5 分鐘記 `catchup`，否則 `schedule` |
| 已過 | 成功，或失敗是 exit 5／6（認證、額度）／143（被停止） | — | 等明天；修好後用 `POST /run` 補 |
| 已過 | 其他失敗、結束超過 30 分鐘、trigger 不是 `retry` | 空 | 從失敗的那步重跑一次（寄信失敗從 render 起，前面的沿用今天的產出），記 `retry`；再失敗就等明天 |
| 已過 | 開始了沒結束 | 空 | 上次被打斷，重跑 |
| 已過 | 任何 | 被握著 | 別條正在跑，下次再看 |

**失敗通知**：今天不會再自動重跑時（重跑也失敗，或是 exit 5／6），寄一封純文字信給 `NEWSLETTER_MAIL_TO`：哪一步、exit code、該怎麼處理、log 最後 20 行。
用 `newsletter-send --notice` 寄（同一組 Gmail 設定）；寄信本身壞掉時通知多半也寄不出去，只會留在 worker 的 log。`worker --once` 不寄（人就在終端機前）。

**heartbeat**：設了 `NEWSLETTER_HEARTBEAT_URL`（例如 [healthchecks.io](https://healthchecks.io) 免費方案的 ping 網址）時，每次成功跑完 GET 這個網址，
寄失敗通知時 GET `<url>/fail`。樹莓派斷電、斷網、worker 卡死或寄信壞掉，失敗通知都寄不出來；這時外部服務等不到 ping，會自己寄信。
在 healthchecks.io 把週期設 1 天、寬限 1～2 小時。沒設就不做事；ping 不到只記 log。

**回饋備份**：每週日 `NEWSLETTER_AUTH_CHECK_AT` 之後，把 `state/feedback.jsonl`（有的話連同 `state/kg_flags.jsonl`）在鎖內複製一份，當附件寄到 `NEWSLETTER_MAIL_TO`。
寄出的 ISO 週記在 `state/backup_week`，重啟後同一週不重寄；寄不出去等下週。還原就是把附件放回 `state/feedback.jsonl`。

**token 到期提醒**：`/auth` 存的 token 依儲存時間推算剩 30、14、7、3、1、0 天，以及過期後第一天，在 `NEWSLETTER_AUTH_CHECK_AT` 之後寄一封提醒，說明怎麼換新。

**認證驗證**：同一個時鐘，每天 `NEWSLETTER_AUTH_CHECK_AT`（預設 07:30）跑一次 `newsletter-llm --auth-check`（一次極小的 Claude 呼叫），
結果 `{ok, kind, at}` 原子寫入 `state/auth_health.json`，web 的 `/api/auth-health` 讀它（[web-server/README.md](../web-server/README.md)）。
要不要驗只看這個檔的 `at`：今天排定時間之後驗過就不再驗（web 的 `/auth` 測試、貼上 token 也會寫它，算數）；啟動時已過時間又還沒驗過就補一次。
和 pipeline 同一輪到期時先驗再跑。寫報告以 exit 5（授權失敗）／6（額度用完）結束時也順手寫進這個檔，不另外驗。不握 pipeline 的鎖。

| 介面 | 回應 |
| --- | --- |
| `GET /status` | `{state: idle\|running, scheduled, run_at（HH:MM）, next_run_at（排程關閉時 null）, current: {kind, trigger, started_at} \| null, last: last_run.json \| null, job: last_job.json \| null}`；`kind` 是 `run`、`kg-rebuild` 或 `sources-check` |
| `POST /job?kind=<工作>` | 見下面「手動的工作」 |
| `POST /run[?from=<步驟>]` | 202 `{started_at, id}`；已經在跑（這裡或別的程序握著鎖）回 409。不收 body。`from`（collect／write／render／send，預設 collect）從那一步開始，前面的產出要今天已經有；send 會從 render 起；其他值回 400 |

沒有登入，靠網路隔離：compose 不 publish 這個 port，`be` 網路只有 `worker` 與 `web`（web 的 `/ops` 只有區網入口開得到）；systemd 版綁 `127.0.0.1`（見 [deploy/README.md](../../deploy/README.md)）。
輸出進 `logs/<YYYY-MM>.log`（月份以開始時間為準），服務自己的訊息印 stdout；失敗時附上這次 log 的最後 20 行。
SIGTERM：轉給執行中的那一步、等它結束、紀錄記成 143，然後離開；被打斷的那天下次啟動會補跑。

**手動的工作**：`POST /job?kind=` 跑每日流程以外的工作，web 的維運頁（`/ops`）也是呼叫這兩個介面：

| `kind` | 做什麼 |
| --- | --- |
| `sources-check` | `newsletter-collect --dry-run --no-search`：抓一次所有來源、印統計，不寫檔、不用 Claude（時限 30 分鐘） |
| `kg-rebuild` | `newsletter-kg --rebuild`：清掉 `state/kg.sqlite`，依日期重跑全部 curated（用訂閱額度，時限 4 小時） |

和每日流程握同一把 pipeline 的鎖，同時只會有一個；在跑時回 409，不合法的 `kind` 回 400。輸出在 `logs/ops-<kind>.log`（每次覆寫），metrics 標籤是 `ops`，不混進每日執行的統計。
`POST /run` 與 `POST /job` 開始時回 202 `{started_at, id}`，開始與結束都寫進 `state/last_job.json`（`id, kind, from, requested_at, started_at, finished_at, exit_code, log, offset`；
`run` 的輸出在當月 log，`offset` 是這次開始的位置），web 從 volume 讀它顯示結果與 log。排程跑的不記在這裡（看 `last_run.json`）。

## 加一個步驟

在 `daily.py` 的 `MODULES` 加一行模組名，在 `_run` 依序加一次 `go(...)`，並在 `tests/` 的測試裡涵蓋它（假步驟見 `tests/helpers.py`）。
