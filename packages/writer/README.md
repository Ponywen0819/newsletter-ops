# writer

寫報告：用 Claude Agent SDK 跑 `.claude/skills/news-digest/SKILL.md`，把當日 `data/curated/<date>.json` 寫成 `reports/<date>.md` 並驗收。
依賴 [shared](../shared/README.md)、[llm](../llm/README.md)，**不依賴 collect，也不上網**：資料只能來自 curated JSON，
搜尋補充與原文都由 [collect](../collect/README.md) 先存好，報告的每一句都查得到出處。不產 email、不寄信（那是 [notify](../notify/README.md)）。
專案全貌見[根目錄 README](../../README.md)。

```
src/newsletter_writer/
  write.py               入口 CLI（newsletter-write）：跑 skill、驗收報告有更新且格式正確、記錄用量
  kg.py                  新聞知識圖譜（newsletter-kg）：萃取事件、跨日比對、產生 writer 的背景
```

```bash
uv run --project packages/writer newsletter-write                  # 依 skill 寫報告並驗收，約數分鐘
uv run --project packages/writer newsletter-write --max-turns 50   # 預設 30 回合（實測 8～11），超過視為失敗
```

與在對話裡打 `/news-digest` 用**同一份** skill、結果一致；對話裡 curated JSON 不存在時 skill 會先跑 `newsletter-collect`，
這裡（無人值守）不跑，直接失敗——蒐集是 worker 前一步的事。

- **工具**：只開 Skill、Read、Write、Edit 與程式內的 `report_check`（取代 `newsletter-report-check`），沒有 WebSearch、WebFetch、Bash。
  curated JSON 含網頁原文，可能藏著給 agent 的指令，所以權限照最壞情況設：寫入只限 `reports/`（`Edit(./reports/**)`，Edit 規則涵蓋 Write），
  讀不到 `state/`（OAuth token 在那裡）與 `.env`；cwd 以外本來就讀不到。沒有 Bash 就沒有連網或讀環境變數的管道。
  容器裡 `data/`、`reports/`、`state/` 是連到 volume 的 symlink（在 cwd 外），所以每條規則同時給相對路徑與解析後的絕對路徑（`permissions()`）。
- **認證**：只用儲存的 OAuth token，政策見 [llm/README.md](../llm/README.md)「認證」。
- **用量**：寫成 `stage: claude`、`runner: sdk` 的 metrics 紀錄，見 llm/README.md「用量與成本」。

## 知識圖譜（KG）

`newsletter-write` 開始寫之前先跑 `kg.prepare()`：把當天 curated 萃取成事件存進 `state/kg.sqlite`，
再把今天新聞涉及的實體在過去 30 天的事件寫成 `data/kg/<date>.json`，skill 的「背景」一節用它補前因、後續與更正。
**KG 失敗不擋寫報告**：只記 log 與 `stage: kg` 的 metrics，那天就沒有背景檔。

```bash
uv run --project packages/writer newsletter-kg                         # 只跑萃取與背景（不寫報告）
uv run --project packages/writer newsletter-kg --rebuild               # 清掉重來：依日期重跑 data/curated/ 全部（回填也是這個）
uv run --project packages/writer newsletter-kg --flag <uid> wrong      # 這則事實有誤；trusted＝可信（不被自動判失效）；clear＝取消
```

- **萃取**（`stage: kg_extract`）：Haiku 5.5，**不給任何工具**，一天一次呼叫。每則最多 5 個事件，每個事件要附原文逐字引句；
  程式把引句正規化（全半形、大小寫、空白、標點不算）後比對標題＋摘要＋fulltext，**對不上就丟**，丟掉的數量記在 metrics 的 `dropped`。
  資料庫只存一句話摘要、引句、實體名稱等限長欄位，不存原文段落。`VERSION` 改了，舊的則下次碰到時重抽。
- **比對**（`stage: kg_relate`）：今天的事件和過去 90 天涉及相同實體的事件（每個最多 5 個候選）比對，
  存 `same`（他源報導同一件事）／`follow`（後續）／`contradict`（說法衝突）／`retract`（撤回、否認、更正）。
  矛盾與撤回也要附今天那則的原文引句，對不上就丟（`links_rejected`）。沒有候選就不呼叫模型。
- **狀態在讀取時算**，不存：被一個來源矛盾＝`disputed`；被撤回，或被兩個以上來源矛盾＝失效（不進背景）。
  `state/kg_flags.jsonl`（網頁的「事實有誤」與 `--flag`）優先：`wrong` 的則失效，而且它對別則的矛盾、撤回不算；`trusted` 的則不會被自動判失效。
- **背景檔**：`events` 最多 40 個（和今天有直接關聯的優先，其次共同實體多的、新的），失效的不放；`corrections` 是以前寫進晨報、今天被撤回或推翻的事件，
  skill 會在報告開頭加「更正」。檔案開頭的 `note` 提醒 agent 這是資料不是指令；writer 的權限只多了讀 `data/kg/`。
- **成本**：萃取與比對用 Haiku，每天約 $0.02；writer 多讀背景每天約 $0.03～0.06（訂閱額度的估算）。summary 的 `usd` 已含 KG。

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

## 測試

```bash
npx nx run writer:test   # 驗收、權限、main 的流程與 KG，SDK 與模型都換成假的
```
