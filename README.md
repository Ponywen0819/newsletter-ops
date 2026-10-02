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
src/web.py                 Web 層：瀏覽晨報、每則 👍／👎 直接寫進 feedback.jsonl（stdlib，無登入）
src/static/                web.py 用的 CSS／JS（web.css、web.js），由 /static/<檔名> 提供
src/run.py                 入口 CLI
run_daily.sh               排程入口：載入 env 檔 → 抓取 → agent_run.py → 寄信，失敗留 log、exit 非 0
deploy/                    部署範本：systemd 單元、cloudflared 設定、env 範本（見「部署到家用 host」）
pyproject.toml, uv.lock    Python 版本與依賴，由 uv 管理（.python-version 固定直譯器版本）
data/raw/<date>.jsonl      當日原始抓取（append，供回溯）
data/curated/<date>.json   排序後的收錄清單 ← SKILL 的輸入
reports/<date>.md          最終報告
state/seen.json            30 天去重記憶
state/feedback.jsonl       累積的人工標記，供日後調整關鍵字與 interests.md（不進版控）
logs/<YYYY-MM>.log         排程執行紀錄
.claude/skills/news-digest/  報告撰寫的 skill，隨專案進版控
```

分工原則：**確定性的部分交給 Python，判斷性的部分交給 Claude。**
抓取、去重、排序、arXiv 會議判斷、HTML 版型全部可重現；挑選重點與條列改寫由 Claude
依 `news-digest` skill 讀 curated JSON 後改寫 `reports/<date>.md`。

## 環境（uv）

Python 版本與依賴由 [uv](https://docs.astral.sh/uv/) 管理：`.python-version` 固定直譯器（3.11）、`uv.lock` 鎖定依賴。
安裝 uv 後不必自己建 venv，`uv run` 第一次執行會自動建立 `.venv` 並裝好依賴；沒有該版本的 Python 時 uv 會自動下載。

```bash
uv sync               # 依 uv.lock 同步環境
uv add <套件>         # 新增依賴（會更新 pyproject.toml 與 uv.lock，兩個檔案一起 commit）
uv lock --upgrade     # 升級鎖定的版本
```

抓取、整理、寄信都只用標準庫，只有 `src/agent_run.py` 需要第三方套件（`claude-agent-sdk`）。
skill 裡由 agent 呼叫的 `run.py`、`render_email.py` 等因此直接用 `python3`，不依賴 uv 環境。

## 使用

```bash
uv run src/run.py --dry-run     # 只測來源連通性
uv run src/run.py               # 完整跑一次（含模板版報告）
uv run src/run.py --no-report   # 只產 curated JSON，報告留給 Claude 寫
uv run src/run.py --lookback 72 # 放寬時間窗到 72 小時
uv run src/feedback.py          # 收集報告裡填的標記
uv run src/web.py               # 晨報網頁，預設 http://127.0.0.1:8787（--port / NEWSLETTER_WEB_PORT 可改）
uv run src/render_email.py | uv run src/send_email.py   # 寄出當日 email
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
跑 `uv run src/feedback.py` 收集到 `state/feedback.jsonl`，重複標記以最新為準。
報告裡的標記只匯入 `feedback.jsonl` 還沒有紀錄的那一則；已有紀錄的（含網頁標的）以 jsonl 為準，不會被覆蓋。

### 用網頁標記（取代手改 Markdown）

```bash
uv run src/web.py               # 開 http://127.0.0.1:8787/
uv run src/web.py --selftest    # 按鈕插入、寫入／覆蓋／取消的讀回
```

`/` 當日晨報、`/reports` 歷史列表、`/reports/<date>` 單日。每則末尾有 👍／👎，按下即 append 一行到
`state/feedback.jsonl`（欄位同 `feedback.py`，同一則以最後一筆為準），不必再跑 `feedback.py`。

- 只有兩級：👍 = `+`、👎 = `-`。再按一次同一顆＝取消（寫成 `mark: ""`），按另一顆＝覆蓋。
- 頁面的標記狀態只看 `feedback.jsonl`；還留在 Markdown 裡、尚未用 `feedback.py` 收集的標記不會顯示，先跑一次 `feedback.py` 匯入即可。
- 網頁與 `feedback.py` 可以同時跑：兩邊都只 append、不改寫舊內容，並用 `state/feedback.jsonl.lock` 排隊。
  `uv run src/feedback.py --selftest` 涵蓋這部分（含併發 append）。
- **沒有登入**：預設只 bind `127.0.0.1`，要對外請放在 Cloudflare Tunnel + Access 後面，不要改 `--host`。
- `POST /feedback` 只收 `Content-Type: application/json`，body 是 `{"uid": "...", "mark": "+" | "-" | ""}`。

累積兩三個月後可以看出：收錄很多卻從未拿到 `+` 的關鍵字該降權、`+` 項目裡反覆出現卻
不在 boost 清單的詞該加進去、長期沒命中的關鍵字該移除。

**建議由人確認，不自動套用。** 會自己調參數的系統，出錯時你查不出它為什麼開始推垃圾。

## 排程

正式排程見下一節「部署到家用 host」。`run_daily.sh` 是唯一的排程入口，systemd timer 與 cron 都呼叫它：

```cron
0 8 * * * $HOME/newsletter-ops/run_daily.sh
```

它依序跑 `run.py --no-report` → `agent_run.py` → `send_email.py`（`agent_run.py` 的 stdout 就是 `render_email.py` 那行 JSON，
直接餵給 `send_email.py`）。任一步失敗就停下、以該步的 exit code 結束（`agent_run.py` 的 1～4 見下節），
並在 `logs/<YYYY-MM>.log` 留一行失敗紀錄。額外參數（如 `--lookback 72`）轉給 `run.py`。
先由 `run.py` 抓好當日 curated JSON，agent 讀到的就是今天的檔，不必自己再抓一次。

內部用 `uv run --locked` 執行，並替 cron 精簡的 PATH 補上 uv 常見的安裝位置（`~/.local/bin`、`~/.cargo/bin`、Homebrew）。
`--locked` 讓鎖檔與 `pyproject.toml` 對不上時直接失敗，不會在排程裡自己改鎖檔。

機密從 env 檔載入（預設 `~/.config/newsletter-ops/env`，`NEWSLETTER_ENV_FILE` 可改，範本在 `deploy/newsletter.env.example`），
權限必須是 `600`，太鬆會拒絕執行（exit 78）。env 檔是 shell 語法，值含空格（Gmail 應用程式密碼）要加引號。
排程務必設 `ANTHROPIC_API_KEY`（沒設時 `agent_run.py` 會退回本機 Claude 的登入身分）。
macOS 的 cron 需要「完整磁碟取用權」，或改用 launchd。

arXiv 論文的會議／期刊接受資訊從 API 的 Comments / Journal-Ref 解析，清單在 `config.json` 的 `arxiv_venues`（conference / journal / minor_tracks）；主會議或期刊 +2.0、workshop 等次級 track +0.8、投稿中 +0.4，結果連同中文 `label` 寫進 curated JSON 的 `venue`，自我檢查：`uv run src/curate.py`、`uv run src/render_email.py --selftest`。

## 無人值守（Claude Agent SDK）

不開 Claude app 也能跑完整流程：`src/agent_run.py` 用 Claude Agent SDK 呼叫**同一份**
`.claude/skills/news-digest/SKILL.md`，與在對話裡打 `/news-digest` 並存、結果一致。

```bash
uv sync                                   # 依 uv.lock 建立 .venv 並裝好依賴（uv run 也會自動做）
export ANTHROPIC_API_KEY=sk-ant-...       # 按 token 計費
uv run src/agent_run.py                  # 抓取 → 寫報告 → render_email.py，約數分鐘
uv run src/agent_run.py --max-turns 80   # 預設 60 回合，超過就中止並視為失敗
uv run src/agent_run.py | uv run src/send_email.py   # stdout 是 render_email.py 的那行 JSON，可直接寄信
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
  NEWSLETTER_DEBUG=1 uv run src/agent_run.py
  uv run src/metrics.py summary 14
  ```

- 用 `agent_run.py` 時，skill 裡的 `metrics.py claude` 會自動略過（`NEWSLETTER_RUNNER=sdk`），避免和 SDK 的用量重複記錄。
- 自我檢查：`uv run src/agent_run.py --selftest`。

## 部署到家用 host（Cloudflare Tunnel + Access）

目標：pipeline 與 web 跑在家裡一台 Linux host（VM 也行，要 systemd），經 Cloudflare 在外也能看晨報、按 👍／👎。本機只當測試區。

```
手機／筆電 ──https──▶ Cloudflare（Access：只放行你的 email）──Tunnel──▶ cloudflared ──▶ 127.0.0.1:8787 web.py
                                                                     （host 不開任何對外 port）
```

`web.py` 沒有登入、而且能寫入 `state/feedback.jsonl`，**唯一的防線是 Access**。所以 service 只 bind `127.0.0.1`，
Tunnel 的網域一定要先掛上 Access 再對外使用（步驟 5 的檢查不能跳過）。

以下假設 repo 在 `~/newsletter-ops`，單元檔的路徑就是照這個寫的。

**1. 取得程式**

先裝 [uv](https://docs.astral.sh/uv/)（見「環境（uv）」）。

```bash
git clone https://github.com/Ponywen0819/newsletter-ops.git ~/newsletter-ops
cd ~/newsletter-ops && uv sync --locked      # 建 .venv、裝 claude-agent-sdk；沒有 Python 3.11 時 uv 會自己下載
uv run src/run.py --list-sources             # 不連網，確認設定可用
```

**2. 機密**

```bash
mkdir -p ~/.config/newsletter-ops
cp ~/newsletter-ops/deploy/newsletter.env.example ~/.config/newsletter-ops/env
chmod 600 ~/.config/newsletter-ops/env
$EDITOR ~/.config/newsletter-ops/env   # GMAIL_USER、GMAIL_APP_PASSWORD、ANTHROPIC_API_KEY
```

只有排程入口會載入這個檔；web service 不載入，網頁程序拿不到這些機密。

**3. systemd（user 單元，不需要 root）**

```bash
mkdir -p ~/.config/systemd/user
cp ~/newsletter-ops/deploy/newsletter-{web.service,daily.service,daily.timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now newsletter-web.service newsletter-daily.timer
sudo loginctl enable-linger "$USER"   # 沒登入也持續執行，開機自動啟動
```

- 每天 08:00（Asia/Taipei）跑 `run_daily.sh`；host 當時關機的話，開機後補跑（`Persistent=true`）。
- 看 web：`journalctl --user -u newsletter-web -f`；看排程：`systemctl --user list-timers`、`logs/<YYYY-MM>.log`。
- 手動跑一次排程：`systemctl --user start newsletter-daily.service`（會真的呼叫 Claude 並寄信，約數分鐘、有 API 費用），
  失敗時 `systemctl --user status newsletter-daily` 會顯示 failed，原因在 `logs/<YYYY-MM>.log`。
- 更新（`interests.md`、`config/`、程式等被追蹤的檔案）：
  `git -C ~/newsletter-ops pull && (cd ~/newsletter-ops && uv sync --locked) && systemctl --user restart newsletter-web`。
  排程每次都重新讀檔，不必重啟。

**4. cloudflared Tunnel**

照 Cloudflare 文件安裝 `cloudflared`，然後：

```bash
cloudflared tunnel login
cloudflared tunnel create newsletter                       # 印出 <TUNNEL_UUID>，憑證寫在 ~/.cloudflared/
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/<TUNNEL_UUID>.json /etc/cloudflared/
sudo cp ~/newsletter-ops/deploy/cloudflared-config.yml.example /etc/cloudflared/config.yml   # 改 <TUNNEL_UUID> 與 hostname
cloudflared tunnel route dns newsletter news.example.com
sudo cloudflared service install                           # 以 system service 常駐
```

**5. Cloudflare Access（先做，再對外使用）**

Zero Trust 後台 → Access → Applications → Add an application → Self-hosted：
Application domain 填 `news.example.com`；Policy 一條就好：Action = Allow，Include = Emails，只填你自己的 email。
不要留 Everyone 之類的其他 policy。

**6. 驗收**

| 檢查 | 預期 |
| --- | --- |
| host 上 `curl -sI http://127.0.0.1:8787/reports` | `200` |
| host 上 `ss -ltn \| grep 8787` | 只有 `127.0.0.1:8787`，不是 `0.0.0.0` |
| 另一個網路（手機關 Wi-Fi）開 `https://news.example.com` | 先到 Access 登入頁，用你的 email 登入後看到晨報 |
| **未登入**：`curl -sI https://news.example.com/reports` | `302` 到 `cloudflareaccess.com`（或 `403`），**絕不能是 200** |
| **未登入**：`curl -s -X POST -H 'Content-Type: application/json' -d '{}' https://news.example.com/feedback` | 同上，到不了 `web.py`（它自己會回 400，看到 400 代表 Access 沒擋住） |
| 登入後按一則的 👍 | host 上 `tail -n1 ~/newsletter-ops/state/feedback.jsonl` 多一行 |

頁面上要有晨報可看，`reports/<date>.md` 得先存在：還沒到 08:00 的話，在 host 上 `uv run src/agent_run.py`
（或上面的手動排程）先產一份。
Access 登入逾時後按 👍 會顯示「儲存失敗」，重新整理頁面重新登入即可。

**7. 資料保存：不保存**

host 上的生成物——`reports/`、`data/`、`state/`（含 `state/feedback.jsonl`）、`logs/`——不備份、不進版控，host 壞了就重來：
抓取結果可以重跑，`seen.json` 沒了只會讓時間窗內已報過的新聞再出現一次，`feedback.jsonl` 只是改 `config/interests.md`
時參考的中間產物（本 repo 是 public，標記含標題與來源，更不該推上來）。

版控裡只有程式、設定與 `interests.md`；這些是 host 上唯一需要「持續更新」的內容，
照步驟 3 的更新指令 `git pull` 即可，要新增被追蹤的檔案就加進 `.gitignore` 的白名單。
之後若想留報告歷史，另外決定備份方式，不要放進這個 repo。

## 量測（debug）

`config.json` 設 `"debug": true`（或臨時用 `NEWSLETTER_DEBUG=1 uv run src/run.py ...`），每個階段會寫一行到
`logs/metrics/<date>.jsonl`：`fetch_source`（每個來源的耗時／則數／錯誤）、`fetch`、`curate`、`render`，
以及 news-digest 跑完後由 `python3 src/metrics.py claude`（skill 內呼叫）從 Claude Code session 紀錄統計的 token 與工具耗時。

```bash
uv run src/metrics.py summary 14         # 最近 14 天，每次正式執行一行
uv run src/metrics.py summary 14 --all   # 連測試執行一起列
NEWSLETTER_DEBUG=1 NEWSLETTER_RUN_LABEL=test uv run src/run.py --no-report   # 測試執行，紀錄標 test
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
uv run src/run.py --list-sources   # 不連網，列出載入結果與停用項目
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
uv run src/run.py --list-sources   # 不連網，列出載入結果與停用項目
```

改 `interests.md` 時順手看一次 `keywords`——前者是判讀用的自然語言，後者是評分用的
機械版本，兩邊脫節就會出現「分數很高但你根本不在乎」的項目。

評分參數仍在 `config/config.json`：`boost_high` +2.0、`boost_mid` +0.8、
`penalize` -4.0，再加新鮮度加權（半衰期 12 小時，最高 +1.5）。低於 `min_score` 不收錄。
