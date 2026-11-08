# collect

蒐集：抓取 → 整理 → 搜尋補 RSS 弱的主題 → 補原文，產出當日的 `data/curated/<date>.json`，寫報告的 [writer](../writer/README.md) 只讀這份檔。
抓取與整理是純標準庫；只有搜尋經由 [llm](../llm/README.md) 用到 Claude。依賴 [shared](../shared/README.md)、llm，**不認得報告與 email**。
專案全貌見[根目錄 README](../../README.md)，已知問題見 [ISSUES.md](ISSUES.md)。

```
src/newsletter_collect/
  sources.py             來源載入層：掃 sources.d、驗證欄位、去重、處理停用
  fetch.py               抓取層：RSS 2.0 / Atom / arXiv API（零第三方依賴）
  curate.py              整理層：時間窗 → 跨日去重 → 近似標題合併 → 關鍵字+新鮮度評分
  search.py              搜尋層：RSS 弱的主題交給 Claude 用 WebSearch 補，程式驗收後併進 curated
  enrich.py              補原文：最可能入選的項目抓原文存進 fulltext，writer 不必上網
  run.py                 入口 CLI（newsletter-collect）
```

設定在 repo 根的 `config/`（`interests.md`、`config.json`、`sources.d/*.json`）。

## 使用

```bash
uv run --project packages/collect newsletter-collect                  # 完整跑一次，寫 data/curated/<date>.json
uv run --project packages/collect newsletter-collect --no-search      # 不用 LLM 搜尋（沒有 token 或離線測試時）
uv run --project packages/collect newsletter-collect --lookback 72    # 放寬時間窗到 72 小時
uv run --project packages/collect newsletter-collect --dry-run        # 只測來源連通性，不寫任何檔
uv run --project packages/collect newsletter-collect --list-sources   # 不連網，列出來源載入結果與停用項目
```

exit code：`0` 成功、`2` 來源設定有誤、`3` 一則都沒抓到（不寫 curated JSON）。搜尋與補原文失敗只在 stderr 警告，不影響 exit code。

同一天可以重跑：當天 `data/curated/<date>.json` 上次收錄的項目（含搜尋補來的）不算「看過」，會重新排名，所以頭條不會因為重跑而消失。

## 搜尋補充

RSS 弱的主題由 Claude 用 WebSearch 補，但**補哪些主題、收不收下都由程式決定**，LLM 只負責找：

- **補哪些**：`config.json` 的 `search.always`（目前是 `world`，國際新聞沒有關鍵字可評分），加上 curate 後 0 則的主題（通常是來源抓取失敗）。
  其他主題不搜。
- **怎麼找**：只開 WebSearch 一個工具（`search.max_turns` 回合內），提示裡列出每個主題已有的候選，請它補最多 `search.per_gap` 則
  過去 `search.lookback_hours` 小時內的新聞，最後回一份固定格式的 JSON（topic、title、url、published、summary、query）。
- **程式驗收**：欄位齊全、主題在這次的清單裡、網址是 http(s)、發佈時間在窗內、沒和已收錄的重複、前幾天沒收過，
  最後程式自己抓網址，頁面的 `<title>` 或 `og:title` 要對得上宣稱的標題。通過的才加上 uid 併進 curated，
  標成 `origin: "search"`、`source: "搜尋：<網域>"`，之後和 RSS 的項目一樣帶 mark、收得到回饋。
- **稽核**：`data/search/<date>.json` 記下這次的缺口、搜過的 query、LLM 的原始回覆、收下的項目，以及退回的項目與原因。
  curated JSON 頂層的 `search` 是摘要（缺口、收下／退回則數、失敗原因）。
- **失敗不擋流程**：沒有 token、額度用完、回覆格式不符都只警告，照常寫 curated JSON；writer 會在「本期備註」提。
  用量寫成 `stage: search` 的 metrics 紀錄（要開 debug），和寫報告的 `claude` 分開。

## 補原文

writer 不上網，判讀需要的原文由這裡先抓好，存在項目的 `fulltext`（約 3000 字純文字；有 `<article>` 或 `<main>` 就只取它）：

- 每個主題 `rank` 前 3 則（arXiv 除外）與搜尋補來的全部；
- 還沒有主會議資訊的 arXiv 論文前 3 篇，讀 `arxiv.org/html/<id>`（開頭有作者機構），沒有 HTML 版就讀 abs 頁。

抓不到的記在 `fulltext_error`（例如 OpenAI、WSJ 對非瀏覽器回 403），writer 就只依 summary 判斷。

## 調整來源

來源不寫在程式碼裡，全部在 `config/sources.d/`，一個主題一個 JSON 檔。
新增主題＝新增一個檔，不必改任何程式碼或主設定。

```json
{
  "topic": "ai-research",
  "label": "AI / 研究論文動態",
  "enabled": true,
  "defaults": { "weight": 1.0 },
  "feeds": [
    { "name": "arXiv cs.CV", "type": "arxiv", "query": "cat:cs.CV" },
    { "name": "Hugging Face Blog", "type": "rss", "url": "https://huggingface.co/blog/feed.xml" },
    { "name": "先關起來的來源", "type": "rss", "url": "https://...", "enabled": false, "weight": 0.6 }
  ]
}
```

- `type: "rss"` 需要 `url`；`type: "arxiv"` 需要 `query`（如 `cat:cs.CV`、`all:"knowledge graph"`）
- `defaults` 套用到本檔所有 feed，個別 feed 可覆寫
- `enabled: false` 放在檔案層＝整個主題停用，放在 feed 層＝單一來源停用
- 檔名以 `_` 開頭的會被當草稿略過
- 同一個 url / arxiv query 重複出現時只留第一個，並印出提示

設定寫錯不會等到抓一半才爆：`sources.py` 在連網前就檢查 name / type / url / query /
weight，錯誤訊息會指出是哪個檔的哪個來源，程式以 exit code 2 結束。

## 評分與配額

每則有兩個分數，這是整個 curate 層最重要的設計：

| 值 | 組成 | 用途 |
| --- | --- | --- |
| `relevance` | 來源保底分 ＋ 關鍵字命中 | **門檻**，低於 `min_relevance` 一律不收 |
| `rank` | `relevance` × 來源 `weight` ＋ 新鮮度 | **只決定排序**，不影響是否收錄 |

關鍵字權重：`boost_high` +2.0、`boost_mid` +0.8、`boost_low` +0.4、`penalize` −4.0。
新鮮度為指數衰減，半衰期 12 小時，最高 +1.5。

早期版本只有單一分數，結果來源權重加新鮮度就超過門檻，關鍵字形同虛設——只要夠新，
「iPhone 霧面貼」也會被收錄。**分數拆開之後，「夠新」永遠無法讓不相關的東西進來。**

英文關鍵字用詞界比對（`agent` 不會命中 `federal agents`），中文用子字串。

改 `interests.md` 時順手看一次 `keywords`——前者是判讀用的自然語言，後者是評分用的
機械版本，兩邊脫節就會出現「分數很高但你根本不在乎」的項目。

### 時間窗依來源而定

`lookback_hours` 可寫在主題檔的 `defaults` 或個別 feed。理由：arXiv 週末不公告，
最新論文往往是 36 小時前；Hugging Face Blog 一週才發幾篇。跟每天產 40 則的新聞站
共用 24 小時的窗，研究類來源永遠進不來。目前設定：

| 主題 | 窗 | 例外 |
| --- | --- | --- |
| ai-research | 96h | HF Blog / Google Research 240h |
| ai-industry | 240h | — |
| semiconductor | 48h | — |
| tech-industry | 24h | IEEE Spectrum 96h |
| taiwan-tech | 24h | — |

### 各主題配額

`config.json` 的 `quota` 決定每個主題最多收幾則，沒列到的用 `default_quota`。
高產量來源（TechNews 一天 40 則）會把研究類主題整個擠掉，不是因為更相關，只是因為量大。

### 略過影片等不適合的項目

feed 可以設 `skip`（正規表示式），標題或網址命中就不收。BBC World 設了 `^Watch:|/videos/`，擋掉影片新聞。

### 跨來源的同一則新聞

標題相似度（0.88）合併不了各家寫法不同的標題。`config.json` 的 `same_story_topics`（目前 `["world"]`）裡的主題另外比對標題＋摘要的專有名詞：
不同來源、共有 3 個以上、Jaccard ≥ 0.4 就當同一則，留 rank 高的，其他來源記進 `also_in`。

不論哪種方式併的，`also_in` 每多一家 rank +0.5、最多 +1.0：多家同時報導是重要性的訊號（arXiv 的跨類別不算）。

### feed 順序加分

國際新聞沒有關鍵字可評分，排序原本只剩來源權重與新鮮度。編輯排過的 feed 可以設 `order_boost`：前 10 則依序加 rank（第一則加滿，往後線性遞減），只影響排序、不影響是否過門檻。
BBC World 設 1.0（它的頭條在 feed 最前面）。

### 來源保底分

`baseline_relevance` 寫在主題檔，給「則則值得看但標題常沒有技術關鍵字」的來源。
官方公告叫〈Introducing X〉，一個關鍵字都沒有，但不該漏掉。目前 ai-industry 給 2.0，
Hugging Face / Google Research 給 1.2。數量由配額控制。

### arXiv 會議／期刊判斷

論文的會議／期刊接受資訊從 API 的 Comments / Journal-Ref 解析，清單在 `config.json` 的 `arxiv_venues`（conference / journal / minor_tracks）；
主會議或期刊 +2.0、workshop 等次級 track +0.8、投稿中 +0.4，結果連同中文 `label` 寫進 curated JSON 的 `venue`。
自我檢查見文末。

## 自我檢查

```bash
uv run --project packages/collect python -m newsletter_collect.curate
uv run --project packages/collect python -m newsletter_collect.fetch
uv run --project packages/collect python -m newsletter_collect.search
uv run --project packages/collect python -m newsletter_collect.enrich
```
