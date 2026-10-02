# newsletter-ops

每日新聞自動蒐集 → 整理 → 報告的 pipeline。主題：AI / 研究論文動態 + 科技產業與市場。

## 架構

```
ISSUES.md                  已知問題與限制（先看這裡）
config/interests.md        關注範圍（自然語言），報告判讀的依據
config/config.json         執行參數：時間窗、關鍵字權重、收錄門檻、HTTP 設定
config/sources.d/*.json    來源清單，一個主題一個檔
src/sources.py             來源載入層：掃 sources.d、驗證欄位、去重、處理停用
src/fetch.py               抓取層：RSS 2.0 / Atom / arXiv API（零第三方依賴）
src/curate.py              整理層：時間窗 → 跨日去重 → 近似標題合併 → 關鍵字+新鮮度評分
src/report.py              輸出層：模板版 Markdown（無 LLM 保底）
src/metrics.py             量測層：debug 開啟時記錄各階段耗時與 Claude token 用量
src/render_email.py        email 層：條列版報告 Markdown → inline-CSS HTML（reports/<date>.html）
src/send_email.py          寄信層：Gmail SMTP 寄出 email HTML（不依賴 Claude 的 Gmail connector）
src/agent_run.py           無人值守層：Claude Agent SDK 跑 news-digest skill，記錄用量、驗收產出（唯一的第三方依賴）
src/feedback.py            回饋層：從報告收集人工標記
src/run.py                 入口 CLI
run_daily.sh               cron 包裝
requirements.txt           agent_run.py 的依賴（claude-agent-sdk）
data/raw/<date>.jsonl      當日原始抓取（append，供回溯）
data/curated/<date>.json   排序後的收錄清單 ← SKILL 的輸入
reports/<date>.md          最終報告
state/seen.json            30 天去重記憶
state/feedback.jsonl       累積的人工標記，供日後調整關鍵字
logs/<YYYY-MM>.log         cron 執行紀錄
.claude/skills/news-digest/  報告撰寫的 skill，隨專案進版控
```

分工原則：**確定性的部分交給 Python，判斷性的部分交給 Claude。**
抓取、去重、排序、arXiv 會議判斷、HTML 版型全部可重現；挑選重點與條列改寫由 Claude
依 `news-digest` skill 讀 curated JSON 後改寫 `reports/<date>.md`。

## 使用

```bash
python3 src/run.py --dry-run     # 只測來源連通性
python3 src/run.py               # 完整跑一次（含模板版報告）
python3 src/run.py --no-report   # 只產 curated JSON，報告留給 Claude 寫
python3 src/run.py --lookback 72 # 放寬時間窗到 72 小時
python3 src/feedback.py          # 收集報告裡填的標記
python3 src/render_email.py | python3 src/send_email.py   # 寄出當日 email
```

`send_email.py` 讀環境變數 `GMAIL_USER`、`GMAIL_APP_PASSWORD`（Google 帳號的應用程式密碼，需先開兩步驟驗證）、
`NEWSLETTER_MAIL_TO`（逗號分隔，沒設就寄給自己）。加 `--dry-run` 只印標頭不寄。

## 讓它跟著你的研究重心走

興趣會變，但系統不會自己察覺——報告每天照常產出，你只是慢慢覺得「最近好像沒什麼有趣的」。
所以分兩條路處理：

**突變**（換題目、接新案子）→ 改 `config/interests.md`，用自然語言寫「核心 / 關注 /
背景 / 不要」四級，並更新檔頂的 `updated:` 日期。`news-digest` skill 每次寫報告前都讀它，
skill 本身不存任何興趣清單。超過 90 天沒更新時，`run.py` 每次執行都會在 stderr 提醒一行。

**漸變**（慢慢偏移，自己察覺不到）→ 靠標記累積資料。報告每則末尾有一行：

```markdown
<!-- mark:    uid=3f9a1c2b0d4e5678 -->
```

看完隨手填 `+`（有用）、`-`（沒用）、`++` / `--`（強烈），Markdown 預覽時不會顯示。
跑 `python3 src/feedback.py` 收集到 `state/feedback.jsonl`，重複標記以最新為準。

累積兩三個月後可以看出：收錄很多卻從未拿到 `+` 的關鍵字該降權、`+` 項目裡反覆出現卻
不在 boost 清單的詞該加進去、長期沒命中的關鍵字該移除。

**建議由人確認，不自動套用。** 會自己調參數的系統，出錯時你查不出它為什麼開始推垃圾。

## cron

```cron
0 8 * * * /Users/pony/project/newssletter-ops/run_daily.sh --no-report
```

macOS 的 cron 需要「完整磁碟取用權」，或改用 launchd。抓完之後在 Claude 對話中
執行 `/news-digest`，讀當日 curated JSON 寫出條列版晨間簡報，並用 `render_email.py` 產出 email HTML。

arXiv 論文的會議／期刊接受資訊從 API 的 Comments / Journal-Ref 解析，清單在 `config.json` 的 `arxiv_venues`（conference / journal / minor_tracks）；主會議或期刊 +2.0、workshop 等次級 track +0.8、投稿中 +0.4，結果連同中文 `label` 寫進 curated JSON 的 `venue`，自我檢查：`python3 src/curate.py`、`python3 src/render_email.py --selftest`。

## 無人值守（Claude Agent SDK）

不開 Claude app 也能跑完整流程：`src/agent_run.py` 用 Claude Agent SDK 呼叫**同一份**
`.claude/skills/news-digest/SKILL.md`，與在對話裡打 `/news-digest` 並存、結果一致。

```bash
pip install -r requirements.txt           # 或 python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...       # 按 token 計費
python3 src/agent_run.py                  # 抓取 → 寫報告 → render_email.py，約數分鐘
python3 src/agent_run.py --max-turns 80   # 預設 60 回合，超過就中止並視為失敗
python3 src/agent_run.py | python3 src/send_email.py   # stdout 是 render_email.py 的那行 JSON，可直接寄信
```

- **認證與計費**：SDK 底層是隨套件附帶的 claude CLI。有 `ANTHROPIC_API_KEY` 就按 token 計費；沒設會退回本機
  Claude 的登入身分（走訂閱額度），`agent_run.py` 會在 stderr 警告一行。排程請設 API key。
- **範圍**：只載入專案層級的 skill（`setting_sources=["project"]`），不吃使用者層級的同名 skill；預先允許
  `Skill / Bash / Read / Write / Edit / WebFetch / WebSearch`，其餘工具一律拒絕（不會卡在沒人回答的提示）。
- **失敗會以非 0 結束**，cron 看得到：

  | exit | 意思 |
  | --- | --- |
  | 0 | 成功 |
  | 1 | agent 失敗：API 錯誤、超過 `--max-turns`、SDK 例外 |
  | 2 | 沒裝 `claude-agent-sdk` |
  | 3 | 報告沒產出：`reports/<date>.md` 沒在這次執行更新，或當天 curated 沒有收錄項目（抓取全失敗） |
  | 4 | `render_email.py` 失敗（報告格式不符） |

- **用量與成本**：結束時從 SDK 的結果取 token 與 `total_cost_usd`，寫成 `stage: claude`、`runner: sdk` 的紀錄
  （label 沿用 `NEWSLETTER_RUN_LABEL`）。和其他 stage 一樣，**要開 debug 才會寫**：`NEWSLETTER_DEBUG=1`。
  stderr 的 `[agent]` 摘要行不受 debug 影響，一定會進 log。`summary` 的 `via` 欄分辨路線（`sdk` / `chat`），
  `usd` 欄只有 `sdk` 路線有值。跑幾天後用它調整 `--max-turns` 與評估成本：

  ```bash
  NEWSLETTER_DEBUG=1 python3 src/agent_run.py
  python3 src/metrics.py summary 14
  ```

- 用 `agent_run.py` 時，skill 裡的 `metrics.py claude` 會自動略過（`NEWSLETTER_RUNNER=sdk`），避免和 SDK 的用量重複記錄。
- 自我檢查：`python3 src/agent_run.py --selftest`。

## 量測（debug）

`config.json` 設 `"debug": true`（或臨時用 `NEWSLETTER_DEBUG=1 python3 src/run.py ...`），每個階段會寫一行到
`logs/metrics/<date>.jsonl`：`fetch_source`（每個來源的耗時／則數／錯誤）、`fetch`、`curate`、`render`，
以及 news-digest 跑完後由 `python3 src/metrics.py claude` 從 Claude Code session 紀錄統計的 token 與工具耗時。

```bash
python3 src/metrics.py summary 14         # 最近 14 天，每次正式執行一行
python3 src/metrics.py summary 14 --all   # 連測試執行一起列
NEWSLETTER_DEBUG=1 NEWSLETTER_RUN_LABEL=test python3 src/run.py --no-report   # 測試執行，紀錄標 test
```

- 紀錄一律保留，不要刪；測試用 `NEWSLETTER_RUN_LABEL=test` 區分，預設是 `prod`。
- 每遇到一筆 `fetch` 就算新的一次執行，之後的 curate／render／claude 歸到這一次。
- token 只含主 session 的 API 回合；WebFetch 內部用的小模型不在紀錄裡，所以不計。
- `non_tool_seconds` 是總時間扣掉工具時間，含模型生成、串流與排隊。

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

```bash
python3 src/run.py --list-sources   # 不連網，列出載入結果與停用項目
```

改 `interests.md` 時順手看一次 `keywords`——前者是判讀用的自然語言，後者是評分用的
機械版本，兩邊脫節就會出現「分數很高但你根本不在乎」的項目。

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

### 來源保底分

`baseline_relevance` 寫在主題檔，給「則則值得看但標題常沒有技術關鍵字」的來源。
官方公告叫〈Introducing X〉，一個關鍵字都沒有，但不該漏掉。目前 ai-industry 給 2.0，
Hugging Face / Google Research 給 1.2。數量由配額控制。

```bash
python3 src/run.py --list-sources   # 不連網，列出載入結果與停用項目
```

改 `interests.md` 時順手看一次 `keywords`——前者是判讀用的自然語言，後者是評分用的
機械版本，兩邊脫節就會出現「分數很高但你根本不在乎」的項目。

評分參數仍在 `config/config.json`：`boost_high` +2.0、`boost_mid` +0.8、
`penalize` -4.0，再加新鮮度加權（半衰期 12 小時，最高 +1.5）。低於 `min_score` 不收錄。
