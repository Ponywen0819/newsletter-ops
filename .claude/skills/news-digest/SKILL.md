---
name: "news-digest"
description: "把 newsletter-ops pipeline 抓好的當日新聞 curated JSON 改寫成條列式晨間簡報（Markdown）。當使用者說要產新聞報告、newsletter、每日科技摘要時使用。"
---

# 新聞摘要報告撰寫

專案根目錄＝本 skill 所在的 repo 根（`.claude/skills/news-digest/` 的上兩層），下稱 ROOT。
語言：繁體中文。技術詞彙保留英文原文。

## 流程

### 1. 取得當日資料

```bash
cd ROOT && ls -t data/curated/ | head -3
```

若當日 `data/curated/<YYYY-MM-DD>.json` 不存在、早於今天，**或它的 `items` 是空陣列**，先跑抓取：

```bash
cd ROOT && uv run --locked newsletter-fetch --no-report
```

`items` 為空的舊檔代表那次抓取全軍覆沒，不是「今天沒新聞」——一定要重跑，不要拿它寫報告。

exit code 的意思：

- `0`：正常，往下走
- `2`：來源設定檔寫錯（`config/sources.d/`），訊息會指出哪個檔的哪個來源——先修正再重跑
- `3`：一則都沒抓到，網路不通或所有來源都壞了。**這時候不要寫報告**，在最後一則回覆說明抓取失敗、
  貼出前幾條錯誤訊息，讓人判斷是網路問題還是來源問題。硬寫一份空報告只會讓人以為今天真的沒新聞。

部分來源失敗是常態：失敗清單在 JSON 的 `errors`，照常寫報告，把它們記在「本期備註」。

讀 JSON。結構：`generated_at`、`stats`、`errors`、`items[]`，每則含 `uid`、`title`、`url`、`source`、`topic`、`published`、`summary`、`relevance`、`rank`、`matched_keywords`、`also_in`。

`relevance` 是門檻分（來源保底 ＋ 關鍵字），`rank` 是排序分（relevance × 來源權重 ＋ 新鮮度）。
`stats.dropped_by_quota` 顯示每個主題因配額被砍掉幾則——某主題被砍很多代表該主題當天內容豐富，
可以在報告裡提一句；持續被砍很多則是配額該調大的訊號。

### 2. 讀 `config/interests.md`

**每次都讀，不要憑印象。** 這個檔是判讀的唯一依據，使用者的研究重心會隨時間改變，
skill 裡不留任何硬編碼的興趣清單。檔裡分「核心 / 關注 / 背景 / 不要」四級，直接照它判斷。

若 `newsletter-fetch` 印出 `[interests]!` 開頭的提醒（超過 90 天沒更新，或檔案不存在），
在報告的「本期備註」提一行，不要在對話裡囉嗦。

### 3. 判讀與挑選

`summary` 常是 RSS 截斷的前幾句。只有在以下情況才用 WebFetch 讀原文：該則會被選為重點、或 summary 不足以判斷這則在講什麼。一次最多讀 5 篇（論文機構查詢另計），避免拖慢。

curated JSON 的 `topic` 對應到簡報的五個段落：

| 段落 | 來源 |
| --- | --- |
| 科技與 AI | `ai-industry`、`tech-industry`、`taiwan-tech`、`semiconductor`，以及 `ai-research` 裡非 arXiv 的部落格 |
| 國際新聞 | `world` |
| 商業與市場 | `economy` |
| 科學 | `science` |
| 機器學習與 Agent 論文 | `ai-research` 裡 `source` 以 `arXiv` 開頭的 |

**每段挑 1–2 則**，其餘放進「其餘收錄」。挑選依據：

- 重要性（影響範圍、是否為事件本身而非評論）、`also_in` 多來源同時報導是訊號。
- 科技與 AI 段對照 `interests.md`：核心項目命中優先，並在「為何重要」講出具體連結。
- 對照 `interests.md` 的「不要」，以及公關稿、重複報導、無實質內容的融資新聞，不選。

**論文段的品質篩選，嚴格照這個順序：**

1. 有 `venue` 欄位的論文（curate 已用 `config.json` 的 `arxiv_venues` 固定清單判好）：`status` 為 `main`（主會議或期刊）> `minor`（workshop、Findings、Industry Track 等）> `submitted`。
   有 main 的就從中選，不要為了題目亮眼改選沒審查的。「來源評級」**直接照抄 `venue.label`**，不要自己重判。
   只有在你讀原文發現 label 明顯錯誤時才改寫，並在「本期備註」寫出原始 Comments，方便把新寫法加進清單。
2. 都沒有 main 時，從排名前 3 的候選用 WebFetch 讀 `https://arxiv.org/html/<id>`（沒有就讀 abs 頁）找作者機構，
   優先頂尖大學（MIT、Stanford、CMU、Berkeley、Princeton、Harvard、Caltech、Oxford、Cambridge、ETH、EPFL、清華、北大、NTU/NUS、東大、KAIST）
   或知名研究室（Google DeepMind / Research、Meta FAIR、Microsoft Research、OpenAI、Anthropic、AI2、NVIDIA Research）。
3. 避開：無會議資訊且無可辨識機構、只有 benchmark 沒有方法、像 LLM 生成的綜述、沒有 baseline 比較的結果。

**國際新聞段**：`world` 的候選沒有關鍵字可評分，排序主要靠來源權重（BBC 的 feed 是編輯排過的頭條）。
寫這段前用一次 WebSearch 查當天最重要的國際新聞；若它不在候選裡且明顯比候選重要，改用它，照下面「搜尋補充」的規則標示。

某段落當天 0 則（通常是來源抓取失敗）時，可用 WebSearch 補一則過去 24 小時內的重要新聞，標題後加「（搜尋補充）」，
不加 mark 註解，並在「本期備註」說明哪個來源失敗。不要為了湊數補不重要的新聞。

### 4. 寫報告

覆寫 `ROOT/reports/<YYYY-MM-DD>.md`。**全部用條列，不寫散文段落**；每個 bullet 一句話。
這份 Markdown 的格式由 `newsletter_shared.report_data` 解析（email 與網頁都吃同一份結構），所以要照下面的寫法，不要自創結構：

```markdown
# 每日晨間簡報 YYYY-MM-DD

<!-- subject: 今日頭條的短語，15 字內，給 email 主旨用 -->

> **今日頭條：** 一句話點出今天最重要的一則，並用 [連結](URL) 帶出來源。

<!-- 看完把每則的 mark: 填上 + 或 -（++ / -- 表示強烈），再跑 uv run --locked newsletter-feedback -->

## 科技與 AI

**[改寫成一行重點的標題](URL)**

- 發生什麼：關鍵事實與數字。
- 背景：前因或脈絡。
- 為何重要：影響、與既有趨勢的關係。
- 後續觀察：接下來該看什麼（選填）。
<!-- mark:    uid=<該則的 uid> -->

## 國際新聞
（同上格式）

## 商業與市場
（同上格式）

## 科學
（同上格式）

## 機器學習與 Agent 論文

**[論文原標題](https://arxiv.org/abs/<id>)**

- 來源評級：已被 NeurIPS 2026 接受／已投稿 ICLR 2027（審查中）／Google DeepMind／未見審查／機構資訊，僅供參考
- 解決的問題：…
- 方法：…
- 關鍵結果：…
- 為何有趣：…
<!-- mark:    uid=<該則的 uid> -->

## 其餘收錄

- **[標題](URL)** — 半句話（來源）
<!-- mark:    uid=<該則的 uid> -->

## 本期備註

- 抓取失敗來源、覆蓋缺口、興趣檔過期提醒、配額砍很多的主題。沒有就整段省略。
```

**每一則都必須帶它自己的 `<!-- mark:    uid=... -->` 註解**，uid 照抄 curated JSON 裡的值（搜尋補充的除外）。
漏掉的話 `feedback.py` 就收不到那則的標記，長期的關鍵字調整會缺資料。curated JSON 裡的每一則都要出現一次，
不是在五個段落就是在「其餘收錄」。

寫作規則：

- 繁體中文；專有名詞、公司名、論文標題可保留原文。
- 今日頭條只選一則，選五段中最重要的那則；`subject` 是它的短語版。
- 每則只出現一次，不要在段落和「其餘收錄」重複。
- 不要複製 `summary` 原文貼上，用自己的話壓縮。
- 不誇大：沒讀原文就不要斷言論文的效能數字；「來源評級」只寫查證到的，查不到就照實寫「未見審查／機構資訊，僅供參考」。
- 資料來源清單不用寫，解析報告時（`report_data`）會從標題連結自動產生。

### 5. 驗證報告格式並收尾

```bash
cd ROOT && uv run --locked newsletter-report-check
```

它讀當日 `reports/<date>.md`，格式正確就 exit 0。exit code 非 0 代表格式不符（例如缺 `subject` 或今日頭條）或找不到報告，
原因印在 stderr，照訊息修正報告後重跑，直到通過。轉成 email 與寄出是呼叫端的事，這個 skill 不做。

若 `config/config.json` 的 `debug` 為 `true`（或環境變數 `NEWSLETTER_DEBUG=1`），最後再跑：

```bash
cd ROOT && uv run --locked newsletter-metrics claude
```

它從本 session 的紀錄統計 news-digest 開始至今的 token、API 回合數與各工具耗時，寫進 `logs/metrics/<date>.jsonl`。
被排程呼叫時，把這步留給排程 prompt 在寄信之後執行，寄信的用量才會算進去。
由 `src/agent_run.py` 呼叫時（環境變數 `NEWSLETTER_RUNNER=sdk`）這步會自動略過，用量由 agent_run 從 SDK 的結果記錄，含費用。
使用者只是要測試時，指令前加 `NEWSLETTER_RUN_LABEL=test`（`newsletter-fetch` 那步也要加）。`logs/metrics/` 的紀錄一律不刪。

最後一則回覆只寫報告路徑與今日頭條，不要把整份報告貼進對話。寄信由呼叫端（排程 prompt）負責，這個 skill 不寄信。

### 無人值守執行

`uv run --locked src/agent_run.py` 會以 Claude Agent SDK 跑本 skill，沒有人可以回答問題，也看不到對話。這時：

- 不要停下來問，不確定的地方照上面的規則自行判斷。
- 中途無法完成（例如 `newsletter-fetch` exit 3、`newsletter-report-check` 修不好）就停止，**不要留下殘缺或空的報告**，
  最後一則回覆寫清楚原因。agent_run 會檢查 `reports/<date>.md` 有沒有在這次執行更新、格式是否正確，
  不成功就以非 0 結束，讓 cron 看得出來；你的最後一則回覆會進 cron 的 log。
- 互動與無人值守用同一份流程，不要為了其中一邊改變報告格式。

## 常見調整

設定分三處：

| 檔案 | 內容 | 什麼時候改 |
| --- | --- | --- |
| `config/interests.md` | 關注範圍，自然語言 | 研究重心變了、接新案子 |
| `config/sources.d/<topic>.json` | 來源清單，一主題一檔 | 加減 feed、調來源權重 |
| `config/config.json` | 關鍵字評分、時間窗、門檻 | 微調收錄鬆緊 |

- 「某來源太吵」→ 先看是哪種吵：內容不相關就把關鍵字加進 `keywords.penalize`（−4.0，足以壓掉任何 boost）；
  相關但排太前面就調該 feed 的 `weight`（只影響 `rank`，不影響是否收錄）。
- 「某主題太少 / 太多」→ 調 `config.json` 的 `quota`；某主題一直是 0 則，先看它的 `lookback_hours` 夠不夠長
  （研究來源更新慢，24 小時的窗會讓它永遠進不來）。
- 「官方公告漏掉了」→ 那類來源的標題常常沒有技術關鍵字，在主題檔給 `baseline_relevance`（2.0 等於無條件過門檻），
  數量交給配額控制。
- 「這個來源先停一陣子」→ 該 feed 加 `"enabled": false`；整個主題停用則把 `"enabled": false` 放在檔案頂層。不要直接刪掉，保留記錄。
- 「加個新主題」→ 在 `config/sources.d/` 新增一個 JSON 檔，不必改程式碼或主設定。
- 「漏掉某主題」→ 在對應主題檔的 `feeds` 加一筆，或把關鍵字加進 `keywords.boost_high`。
- 改完來源設定一律先跑 `uv run --locked newsletter-fetch --list-sources` 確認載入結果（不連網，會一併列出被停用、重複、被略過的項目）。
- 「抓取太慢」→ 開 `debug` 看 `fetch_source` 各來源耗時；請求間隔只套用在同一網域（`request.delay_seconds`，個別網域用 `request.domain_delay_seconds` 覆寫，arXiv API 規範要求 3 秒）。
- 「補抓前幾天」→ `uv run --locked newsletter-fetch --no-report --lookback 72`（注意 `state/seen.json` 會擋掉已收錄過的項目，想重收要先清掉對應項目）。

**改 `interests.md` 時，記得同步看一次 `config/config.json` 的 `keywords`** —— 前者是給判讀用的自然語言，
後者是給評分用的機械版本，兩邊脫節的話會出現「分數很高但你根本不在乎」的項目。改完也要更新檔案頂端的 `updated:` 日期。

## 回饋資料

使用者在報告裡填的標記由 `uv run --locked newsletter-feedback` 收集到 `state/feedback.jsonl`。
累積量還少時不要拿它做推論。被明確要求分析時，看的是：

- 收錄頻繁但從未拿到 `+` 的關鍵字 → 候選降權
- `+` / `++` 項目裡反覆出現、但不在 `boost_high` / `boost_mid` 的詞 → 候選加詞
- 長期沒命中的關鍵字 → 候選移除

**只提建議，不要自動改 `config.json`。** 自動調參的系統出錯時很難查出它為什麼開始推垃圾。
