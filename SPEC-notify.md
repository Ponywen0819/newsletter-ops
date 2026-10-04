# Spec: notify

屬於 [SPEC.md](SPEC.md) 的 Capability Map。依賴：shared。被依賴：無（只由 `run_daily.sh` 在執行期呼叫）。

## Objective

把 agent 寫好的 `reports/<date>.md` 變成通知並送出。目前只有 email；日後加新管道（Line、Telegram…）是在這個模組新增檔案，agent 不必知道。

## 內容

| 原檔 | 新位置 |
|---|---|
| `src/render_email.py` | `notify/src/newsletter_notify/render_email.py` |
| `src/send_email.py` | `notify/src/newsletter_notify/send_email.py` |

console scripts：`newsletter-render` → `render_email:main`、`newsletter-send` → `send_email:main`。
依賴：`newsletter-shared`。純標準庫。

## 介面（檔案契約，不是 Python import）

- 輸入：`reports/<date>.md`，格式由 `newsletter_shared.report_data` 定義。
- `newsletter-render [date]`：寫 `reports/<date>.html`；stdout 一行 JSON `{"subject","headline","html_path"}`；格式不符 exit 非 0。
- `newsletter-send [--dry-run]`：stdin 吃上面那行 JSON，用 Gmail SMTP 寄出。環境變數 `GMAIL_USER`、`GMAIL_APP_PASSWORD`、`NEWSLETTER_MAIL_TO`、`NEWSLETTER_BASE_URL` **名稱與語意不變**。

## 改動

只有搬移與 import 改寫：`import metrics` → `from newsletter_shared import metrics`，`parse_report` 從 `newsletter_shared.report_data` 取，`ROOT` 從 `newsletter_shared.paths` 取。`render` 階段的 `metrics.timed("render")` 保留。

## Success Criteria

1. `render_email --selftest`、`send_email --selftest` 通過。
2. 同一份 `reports/<date>.md`，搬家前後 `newsletter-render` 產出的 HTML **逐位元相同**，stdout JSON 相同。
3. `newsletter-render | newsletter-send --dry-run` 印出信件標頭、不連線。
4. `grep -rnE 'newsletter_(agent|web)' notify/` 無結果；`check_boundaries.py` 通過。

## Boundaries

- Always：驗證只用 `--dry-run`。
- Ask first：改 JSON 欄位、信件版型、SMTP 流程。
- Never：真的寄信當測試；import agent 或 web。
