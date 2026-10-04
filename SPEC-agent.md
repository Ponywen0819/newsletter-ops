# Spec: agent

屬於 [SPEC.md](SPEC.md) 的 Capability Map。依賴：shared。被依賴：web（只為了 `--auth-check`）。**核心，不認得 email。**

## Objective

抓取 → 整理 → Claude 依 `news-digest` skill 寫報告。輸出是兩個檔案：`data/curated/<date>.json` 與 `reports/<date>.md`。到這裡為止；怎麼把報告變成 email（或別的通知）是 notify 的事。

## 內容

| 原檔 | 新位置 |
|---|---|
| `src/sources.py` `fetch.py` `curate.py` `run.py` `report.py` `agent_run.py` | `agent/src/newsletter_agent/` 同名 |
| `.claude/skills/news-digest/SKILL.md` | **不搬位置**，改內容（見下） |
| `config/` | **不搬** |

console scripts：`newsletter-fetch` → `run:main`、`newsletter-agent` → `agent_run:main`。
依賴：`claude-agent-sdk>=0.2.123`、`newsletter-shared`。

## 反轉：agent 不碰 email（本次唯一的行為變更）

| 項目 | 現在 | 之後 |
|---|---|---|
| `agent_run.render_email()` | 驗收時再跑一次 `render_email.py` | **刪除** |
| `agent_run.verify()` | 檢查 curated 與 report 檔存在、不是舊檔 | 再加 `report_data.parse_report(text)`；ValueError → exit 4 |
| stdout | render_email 那行 JSON | **空**（訊息一律 stderr）；`--auth-check` 仍印 JSON |
| exit code | 0–6 | 不變；4 的意義改成「報告格式不符」（已核可） |
| `NEWSLETTER_DEBUG=0` 抑制重複 render 紀錄的手法 | 有 | 刪除（render 只在 notify 記一次） |
| skill 第 5 步「產出 email HTML」 | `python3 src/render_email.py` | 改成「驗證報告格式」：`uv run --locked newsletter-report-check`，失敗就依訊息修正後重跑 |
| skill 其他 `src/*.py` 指令 | `python3 src/run.py` 等 | `uv run --locked newsletter-fetch`、`newsletter-metrics claude`、`newsletter-feedback` |
| skill「這份 Markdown 會被 `src/render_email.py` 轉成 email」 | 有 | 改成「格式由 `newsletter_shared.report_data` 定義，email 與網頁都吃同一份」 |

`agent_run` 啟動 Claude 時 `cwd` 仍是 `ROOT`（repo 根），prompt 內容不變。

## 要特別小心

- `curate.py` 的 `STATE = Path(__file__).parent.parent / "state" / "seen.json"` 搬家後會指進 `agent/src/`，必須改用 `paths.ROOT`；去重記憶 `state/seen.json` 若寫到錯處，隔天會把前一天的新聞全部重收。
- selftest 裡若有 `ROOT / "src" / …` 的子程序呼叫要一併改。
- 反轉後 `metrics` 的 `render` 階段改由 notify 的獨立程序記錄。要確認 `metrics summary` 仍把同一天的 fetch／claude／render 併成一列（用 `NEWSLETTER_RUN_LABEL=test`、`NEWSLETTER_DEBUG=1` 驗證）。

## Success Criteria

1. `grep -rn newsletter_notify agent/` 無結果；`check_boundaries.py` 通過。
2. `curate`、`fetch` selftest 與 `newsletter-fetch --list-sources` 通過；`agent_run` selftest（本機有 SDK）通過且已移除 render 相關斷言。
3. `newsletter-fetch --no-report`（帶 test label）產出的 `data/curated/<date>.json` 欄位結構與搬家前一致，`state/seen.json` 寫在 `ROOT/state/`。
4. 報告缺 subject 或今日頭條時，`verify()` 回傳 exit 4 的原因；正常報告 exit 0 且 stdout 為空。
5. skill 內不再出現 `render_email`、`src/` 字樣。

## Boundaries

- Always：skill 只改上表列的段落。
- Ask first：改 `PROMPT`、`max_turns`、認證流程；跑 `newsletter-agent`（耗訂閱額度）。
- Never：讓 agent import notify；把 `config/` 或 `.claude/` 搬走；刪 `logs/metrics/`。
