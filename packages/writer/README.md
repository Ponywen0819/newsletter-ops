# writer

寫報告：用 Claude Agent SDK 跑 `.claude/skills/news-digest/SKILL.md`，把當日 `data/curated/<date>.json` 寫成 `reports/<date>.md` 並驗收。
依賴 [shared](../shared/README.md)、[llm](../llm/README.md)，**不依賴 collect，也不上網**：資料只能來自 curated JSON，
搜尋補充與原文都由 [collect](../collect/README.md) 先存好，報告的每一句都查得到出處。不產 email、不寄信（那是 [notify](../notify/README.md)）。
專案全貌見[根目錄 README](../../README.md)。

```
src/newsletter_writer/
  write.py               入口 CLI（newsletter-write）：跑 skill、驗收報告有更新且格式正確、記錄用量
```

```bash
uv run --project packages/writer newsletter-write                  # 依 skill 寫報告並驗收，約數分鐘
uv run --project packages/writer newsletter-write --max-turns 80   # 預設 60 回合，超過視為失敗
```

與在對話裡打 `/news-digest` 用**同一份** skill、結果一致；對話裡 curated JSON 不存在時 skill 會先跑 `newsletter-collect`，
這裡（無人值守）不跑，直接失敗——蒐集是 worker 前一步的事。

- **工具**：只開 Skill、Bash、Read、Write、Edit，沒有 WebSearch、WebFetch。Bash 仍在（skill 要跑 `newsletter-report-check`），
  所以技術上還能用 curl 連網，靠 skill 的規則禁止；要徹底擋得另外做沙箱。
- **認證**：只用儲存的 OAuth token，政策見 [llm/README.md](../llm/README.md)「認證」。
- **用量**：寫成 `stage: claude`、`runner: sdk` 的 metrics 紀錄，見 llm/README.md「用量與成本」。

## exit code

失敗會以非 0 結束，排程看得到：

| exit | 意思 |
| --- | --- |
| 0 | 成功 |
| 1 | agent 失敗：API 錯誤、超過 `--max-turns`、SDK 例外 |
| 2 | 環境問題：沒裝 `claude-agent-sdk`，或沒有可用的 OAuth token |
| 3 | 報告沒產出：`reports/<date>.md` 沒在這次執行更新，或當天 curated 沒有收錄項目（蒐集失敗） |
| 4 | 報告格式不符：shared 的 `parse_report` 拒絕（缺 `subject` 註解或今日頭條），原因在 stderr |
| 5 | **授權失敗**：token 無效或已過期，到 `/auth` 重新貼上新的 token |
| 6 | **額度用完**：訂閱的使用額度或帳務問題；不會改用別的認證，等額度恢復再跑 |

成功之後還會比對 uid：curated 有但報告漏掉的（收不到回饋）、報告有但 curated 沒有的，都印警告到 stderr、記進 metrics 的 `coverage`，不影響 exit code。

## 自我檢查

```bash
uv run --project packages/writer newsletter-write --selftest
```
