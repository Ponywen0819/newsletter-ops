# Spec: shared

屬於 [SPEC.md](SPEC.md) 的 Capability Map。依賴：無。被依賴：agent、notify、web。**最先動工。**

## Objective

放「兩個以上模組都要用」的底層，讓其他模組的依賴箭頭都往它指、不必互相借用。純標準庫，不得依賴任何其他 `newsletter_*`。

## 內容

| 原檔 | 新位置 | 備註 |
|---|---|---|
| （新增） | `shared/src/newsletter_shared/paths.py` | `ROOT = Path(__file__).resolve().parents[3]`，全 repo 唯一推算 repo 根的地方 |
| `src/feedback.py` | `…/feedback.py` | mark 格式、`locked`、`append_rows`、`build_row`、`collect`；加 `main()` 供 console script |
| `src/report_data.py` | `…/report_data.py` | `parse_report`（格式驗證者）；加 `--check [date]` |
| `src/metrics.py` | `…/metrics.py` | 把 `__main__` 的分派包成 `main()` |
| `src/auth_store.py` | `…/auth_store.py` | 借 `feedback.locked` 的方式不變 |

## 對外介面（其他模組只准用這些）

- `paths.ROOT`
- `feedback`：`MARK_RE`、`mark_comment`、`locked`、`append_rows`、`build_row`、`read_feedback`
- `report_data`：`parse_report`、`uids_of`、`iter_items`、`plain`、`votable_after`；格式不符 `raise ValueError`
- `metrics`：`record`、`timed`、`enabled`、`TZ`、`claude_usage`
- `auth_store`：`resolve`、`save`、`status`、`scrub_environ`、`token_path`、`TOKEN_ENV` 等
- console scripts：`newsletter-feedback`、`newsletter-metrics`、`newsletter-report-check`

## 新增行為

`newsletter-report-check [YYYY-MM-DD]`：讀 `reports/<date>.md`（預設今天），呼叫 `parse_report`；成功 exit 0，失敗 exit 1 並把 ValueError 訊息印到 stderr。agent 用它自我修正報告，這是反轉後取代「跑 render_email 看有沒有報錯」的驗證。

## 要特別小心

- `metrics.TRANSCRIPTS` 由 ROOT 路徑推算 session 紀錄位置。`paths.ROOT` 必須與舊的 `ROOT` 相同（repo 根），否則 `metrics claude` 找不到紀錄。
- `metrics.enabled()` 讀 `ROOT/config/config.json` 的 `debug`；`config/` 留在根目錄，不要改成相對於 shared。
- `Path(__file__)` 只准留在 `paths.py`。

## Success Criteria

1. `python -m newsletter_shared.paths` 的 selftest 斷言 `ROOT/"uv.lock"` 存在、`ROOT/"config"` 是目錄。
2. `feedback`、`metrics`、`report_data`、`auth_store` 四個 selftest 原樣通過。
3. `newsletter-report-check` 對缺 subject、缺今日頭條的報告各自 exit 1，對現有報告 exit 0。
4. `check_boundaries.py` 確認 shared 不 import 任何 `newsletter_*`（paths 以外的成員）。
5. `metrics.TRANSCRIPTS` 在搬家前後算出同一個路徑。

## Boundaries

- Always：搬移與 import 改寫分開想，不改函式行為。
- Ask first：新增對外介面；改 feedback.jsonl 或 `state/oauth_token.json` 的欄位格式。
- Never：讓 shared import agent／notify／web；動 `logs/metrics/`。
