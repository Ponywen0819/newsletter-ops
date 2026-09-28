# 已知問題

最後更新：2026-09-28。每則格式：現象 → 原因 → 建議。解決後移到文末「已解決」並寫日期。

## 未解決

### 高：會擋住每日自動化

**1. 本機 Claude 執行的 OAuth 過期，09-26 之後沒有報告**
- 現象：`logs/stdout.log` 最後兩行是 `Failed to authenticate: OAuth session expired and could not be refreshed`，`reports/` 停在 2026-09-26。
- 原因：本機 `claude` CLI 的登入過期，cron 在無人互動的情況下無法重新授權。
- 建議：在終端機互動模式下重新登入 `claude`；之後改用 Claude 桌面 App 的本機排程任務，或定期檢查這個 log。

**2. 排程還沒接到這條 pipeline**
- 現象：目前實際在跑的是雲端排程，用的是舊的整合型 prompt（全部靠 web search），本機沒有排程任務。
- 原因：雲端環境讀不到這個 repo；專案也不是 git repo，無法推到 GitHub。
- 建議：
  - 本機排程（推薦）：先解決第 1 點，prompt 照 README 的 5 步流程寫。
  - 雲端排程：先 `git init` 並推到 GitHub，而且 `state/seen.json` 要 commit 回 repo，否則跨日去重會失效。

### 中：影響報告品質

**3. 英文關鍵字比對不到複數**
- 現象：2026-09-28 tech-industry 收錄 0 則，〈OpenAI agents tried to 'bruteforce' a UN website〉被漏掉。
- 原因：`curate._kw_matcher` 用詞界比對，`agent` 後面接 `s` 就不算命中。
- 建議：英文關鍵字的比對樣式允許結尾多一個 `s`／`es`，也就是 `(?:e?s)?(?![a-z0-9])`，並補一條自我檢查。

**4. 國際新聞排序幾乎沒有訊號**
- 現象：world 的候選是 BBC 的 VMAs 紅毯、沉船影片這類新聞，當天真正的頭條（荷莫茲海峽）靠 skill 裡的 WebSearch 才補上。
- 原因：
  - 國際新聞沒有關鍵字可評分，每則都只有保底分 1.5，排序只剩來源權重加新鮮度。
  - BBC 的 feed 混了影片（URL 含 `/videos/`、標題以 `Watch:` 開頭）。
  - 各家標題寫法不同，`dedupe` 的標題相似度（門檻 0.88）幾乎合併不了，所以「多家同時報導」這個訊號拿不到。
- 建議：
  - 先把 `/videos/` 的 URL 和 `Watch:` 開頭的標題降分。
  - 再考慮用 feed 內的排列順序加分（BBC 的頭條是編輯排過的）。
  - 或改用關鍵名詞重疊做跨來源比對。

**5. Tech-industry 常常 0 則，科技段偏單薄**
- 現象：tech-industry 只收有命中關鍵字的新聞，週末或關鍵字沒對上的日子會整段空掉。
- 原因：這個主題沒有 `baseline_relevance`，關鍵字又偏向 edge AI、半導體。
- 建議：先修第 3 點再觀察；仍然太少的話，給 Ars Technica、IEEE Spectrum 小幅保底分（0.5–0.8）。

**6. 「其餘收錄」塞了不相關的新聞**
- 現象：VMAs 紅毯、沉船影片也出現在 email 裡。
- 原因：skill 規定 curated JSON 的每一則都要出現在報告裡（為了 feedback 標記）；world 和 science 靠保底分過門檻，配額內的都會被收錄。
- 建議：第 4 點改善後會自然減少；或者讓 skill 對 world、science 的低價值項目只保留 mark 註解、不寫可見文字。

**7. 科學段被 ScienceDaily 佔滿**
- 現象：2026-09-28 收錄的 4 則科學新聞全部來自 ScienceDaily。
- 原因：Nature 的 RSS 日期只到「日」（時間是 00:00），新鮮度分數吃虧；Nature 的 feed 也混了論文和 Briefing。
- 建議：調高 Nature、Science 的 `weight`，或降低 ScienceDaily 的權重（目前 0.8）。

**8. 24 小時時間窗和仍在發展中的事件衝突**
- 現象：荷莫茲海峽的事件發生在 9/26，超過 24 小時窗，但 9/28 仍是最重要的國際新聞。
- 建議：暫時維持靠 skill 的「搜尋補充」處理；如果常發生，world 的 `lookback_hours` 可以放寬到 36 小時。

**15. `reports/` 裡 09-19～09-26 的 8 份報告不見了**
- 現象：2026-09-28 對話開始時還在，後來只剩 `2026-09-28.md` 和 `.html`；垃圾桶裡也沒有。
- 原因：不明。所有 Claude session 紀錄裡都沒有刪除 `reports/` 的指令，是在 Claude 之外被移除的。
- 影響：那幾天報告裡的 mark 標記如果還沒用 `feedback.py` 收集，就遺失了。`data/curated` 還在，需要的話可以重寫報告。
- 建議：`.gitignore` 目前不追蹤 `reports/`；如果報告要當作「會填 mark 的工作檔」，改為放行 `!/reports/*.md`。

### 低：已知限制，先記錄

**9. arXiv 會議判斷的誤判情境**（`curate.venue_of`）
- 「rejected from ICLR」會被判成主會議。
- 「以前在某 workshop 發表過，現被主會議接受」會被降成非主會議。
- 會議名稱是大小寫敏感比對，「neurips 2026」這種全小寫寫法抓不到。
- arXiv API 不提供作者機構，需要時由 Claude 用 WebFetch 查。
- 遇到新的寫法就加進 `config.json` 的 `arxiv_venues`，並補一條自我檢查。

**10. Anthropic News 依賴第三方 feed**
- 目前用社群專案 [Olshansk/rss-feeds](https://github.com/Olshansk/rss-feeds) 的 feed，因為官網沒有 RSS。
- 它停止更新時，改抓 `https://www.anthropic.com/sitemap.xml`（裡面有 260 篇以上的 `/news/` 網址），但需要另外寫解析。

**11. 抓取還有約 5 秒可以省**
- 現象：請求本身合計約 18 秒，但整體抓取花了 23.7 秒。
- 原因：5 個 arXiv 來源穿插之後，仍有 4 個排在清單最後、彼此相連，每兩個之間要等 3 秒。
- 建議：把 arXiv 平均分散到整個清單，或讓不同網域並行抓取。目前效益不大。

**12. Metrics 的量測範圍**
- WebFetch 內部摘要網頁用的小模型，它的 token 不在 session 紀錄裡，沒有算進去。
- `model_seconds` 是總時間扣掉工具時間，包含串流與排隊，不是純推論時間。
- `metrics.py claude` 要在寄信之後執行，寄信的用量才會算進去。
- 同一天跑多次時，`summary` 只取每個階段的最後一筆。

**13. 專案沒有版本控制**
- 現象：今天對 `fetch.py`、`curate.py`、skill、config 的修改都沒有歷史紀錄，出錯時無法回溯。
- 建議：`git init`；`data/`、`logs/`、`state/` 視需要加進 `.gitignore`。

**14. 今天的測試執行已經寫進 `state/seen.json`**
- 現象：2026-09-28 那次 `/news-digest` 把 27 則標記為已收錄。
- 影響：今天如果再跑正式流程，這些項目不會再出現，curated JSON 會被較少的結果覆蓋。
- 處理：只影響今天，不需要動作。

## 已解決（2026-09-28）

- **Anthropic RSS 404**：官網根本沒有 RSS，原設定的網址是猜的。已改用社群 feed，見第 10 點。
- **Nature、Science 抓到 0 則**：它們用 RSS 1.0（RDF）格式，解析器找不到項目。`fetch.parse_feed` 現在會先去掉預設 namespace，再用 RSS 2.0 的方式解析。
- **「DocInsights at EMNLP」被判成主會議**：會議判斷改成三個固定清單（conference／journal／minor_tracks），加上「名稱 at 會議」規則，並直接產出中文 `label`。
- **抓取 50 秒裡有 30 秒在等待**：請求間隔改成只套用在同一網域，並把同網域的來源穿插排開。arXiv 依 API 規範改為間隔 3 秒。抓取時間降到 23.7 秒。
- **報告格式和排程 prompt 互相衝突**（散文 vs 條列）：skill 改成條列版晨間簡報，email 版型交給 `render_email.py`。
