# agent 已知問題

最後更新：2026-09-28。每則格式：現象 → 原因 → 建議。解決後移到文末「已解決」並寫日期。編號沿用原本根目錄 `ISSUES.md` 的，不重排。

## 未解決

### 中：影響報告品質

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

**5. Tech-industry 常常 0 則，科技段偏單薄**（處理中）
- 現象：tech-industry 只收有命中關鍵字的新聞，週末或關鍵字沒對上的日子會整段空掉。這個主題沒有後手：沒過門檻的新聞不會進 curated JSON，Claude 看不到；WebSearch 補充只在整個段落 0 則時才觸發。
- 已做（2026-09-28）：修了複數比對；Ars Technica、IEEE Spectrum 保底 0.5，命中任一關鍵字就過門檻。IEEE 試過 0.8（無條件收錄），五則裡三則是無關雜項，所以沒採用。
- 觀察：跑幾天看 tech-industry 的則數。仍然太少的話，下一步是把每個主題「差一點過門檻」的前幾則附進 curated JSON，讓 Claude 決定要不要撈回來。
- 另外：`vision` 命中了〈Poetry for Engineers: The UI Designer's Dream〉，這個 boost_low 關鍵字太泛，可考慮改成 `computer vision`。

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

## 已解決（2026-09-28）

- **Anthropic RSS 404**：官網根本沒有 RSS，原設定的網址是猜的。已改用社群 feed，見第 10 點。
- **Nature、Science 抓到 0 則**：它們用 RSS 1.0（RDF）格式，解析器找不到項目。`fetch.parse_feed` 現在會先去掉預設 namespace，再用 RSS 2.0 的方式解析。
- **「DocInsights at EMNLP」被判成主會議**：會議判斷改成三個固定清單（conference／journal／minor_tracks），加上「名稱 at 會議」規則，並直接產出中文 `label`。
- **抓取 50 秒裡有 30 秒在等待**：請求間隔改成只套用在同一網域，並把同網域的來源穿插排開。arXiv 依 API 規範改為間隔 3 秒。抓取時間降到 23.7 秒。
- **報告格式和排程 prompt 互相衝突**（散文 vs 條列）：skill 改成條列版晨間簡報，email 版型交給 `render_email.py`（notify）。
- **英文關鍵字比對不到複數**（原第 3 點）：`curate._kw_matcher` 結尾允許 `s`／`es`，`agent` 現在會命中 `agents`。代價是「federal agents」這類新聞也會拿到 `agent` 的低權重加分（boost_low），靠門檻與 Claude 判讀擋掉。不規則複數與同義詞直接在 `config.json` 多列一個關鍵字。
