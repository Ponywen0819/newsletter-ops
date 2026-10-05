# newsletter-ops

每日新聞自動蒐集 → 整理 → 報告的 pipeline。主題：AI / 研究論文動態 + 科技產業與市場。

這份 README 只放專案概覽；各模組怎麼運作、怎麼調整，看該模組目錄裡的 README。已知問題與限制記在各模組的 `ISSUES.md`：[agent](agent/ISSUES.md)、[shared](shared/ISSUES.md)。

## 架構

monorepo，用 [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) 組成：每個模組是獨立的 Python 套件，
`uv sync` 把它們以 editable 裝進同一個環境。依賴方向由 `deploy/check_boundaries.py` 檢查（CI 也跑），違規的 import 會失敗：

```
shared ◄── agent ◄── web
   ▲
   └────── notify
```

| 模組 | 職責 | 文件 |
| --- | --- | --- |
| [shared/](shared/) | 共用底層（標準庫）：repo 路徑、報告格式的解析與驗證、回饋標記、量測、OAuth token 儲存 | [README](shared/README.md) |
| [agent/](agent/) | 核心：抓取 → 整理 → Claude 寫報告。唯一的第三方依賴 `claude-agent-sdk` 在這裡，**不認得 email** | [README](agent/README.md) |
| [notify/](notify/) | 外圍：報告 → email → 寄出；之後加別的通知管道放這裡 | [README](notify/README.md) |
| [web/](web/) | 晨報網頁：後端 JSON API（`server/`）＋前端 Vite + React PWA（`ui/`）；依賴 `agent` 只為了驗證 OAuth token | [README](web/README.md)、[前端](web/ui/README.md) |

分工原則：**確定性的部分交給 Python，判斷性的部分交給 Claude。**
抓取、去重、排序、arXiv 會議判斷、HTML 版型全部可重現；挑選重點與條列改寫由 Claude
依 `news-digest` skill 讀 curated JSON 後改寫 `reports/<date>.md`。

### 目錄

```
shared/ agent/ notify/ web/    四個模組，內容見上表；已知問題在各模組的 ISSUES.md
config/interests.md            關注範圍（自然語言），報告判讀的依據
config/config.json             執行參數：時間窗、關鍵字權重、收錄門檻、HTTP 設定
config/sources.d/*.json        來源清單，一個主題一個檔
.claude/skills/news-digest/    報告撰寫的 skill，隨專案進版控
run_daily.sh                   排程入口：載入 env 檔 → 抓取 → agent → render → send，失敗留 log、exit 非 0
Dockerfile, docker-compose.yml, docker/   容器部署：web + scheduler + cloudflared，生成物放 volume
deploy/                        部署說明（README）、systemd 單元、cloudflared 設定、env 範本；check_boundaries.py 依賴方向檢查
pyproject.toml, uv.lock        workspace 的根（只列成員）與整個 workspace 的鎖檔；.python-version 固定直譯器版本

# 執行期生成物（都不進版控；位置都在 repo 根，Docker 裡是 volume）
data/raw/<date>.jsonl          當日原始抓取（append，供回溯）
data/curated/<date>.json       排序後的收錄清單 ← SKILL 的輸入
reports/<date>.md              最終報告
reports/<date>.html            email 版（newsletter-render 產生）
state/seen.json                30 天去重記憶
state/feedback.jsonl           累積的人工標記，供日後調整關鍵字與 interests.md
state/oauth_token.json         /auth 頁面存的 OAuth token（權限 600）
logs/<YYYY-MM>.log             排程執行紀錄
```

## 環境（uv）

Python 版本與依賴由 [uv](https://docs.astral.sh/uv/) 管理：`.python-version` 固定直譯器（3.11）、`uv.lock` 鎖定依賴。
安裝 uv 後不必自己建 venv，`uv run` 第一次執行會自動建立 `.venv` 並裝好依賴；沒有該版本的 Python 時 uv 會自動下載。

```bash
uv sync               # 依 uv.lock 同步環境（所有成員一起裝）
uv add --package newsletter-agent <套件>   # 替某個成員新增依賴（改該成員的 pyproject.toml 與 uv.lock，一起 commit）
uv lock --upgrade     # 升級鎖定的版本
```

抓取、整理、寄信都只用標準庫，只有 `agent` 成員（`agent_run`）需要第三方套件（`claude-agent-sdk`）。
skill 裡由 agent 呼叫的指令都是 `uv run --locked …`，要在 repo 根目錄、有 uv 的環境下執行。

## 快速開始

```bash
uv run newsletter-fetch --dry-run     # 只測來源連通性
uv run newsletter-fetch --no-report   # 只產 curated JSON，報告留給 Claude 寫
uv run newsletter-agent               # Claude 依 skill 寫 reports/<date>.md 並驗收（需要 OAuth token）
uv run newsletter-render | uv run newsletter-send   # 轉成 email 並寄出
(cd web/ui && npm install && npm run build)        # 第一次（以及改了前端之後）：建置網頁前端
uv run newsletter-web                 # 晨報網頁，預設 http://127.0.0.1:8787
uv run newsletter-feedback            # 收集報告裡填的標記
```

各指令的選項與細節：抓取與 agent → [agent/README.md](agent/README.md)；email 與寄信 → [notify/README.md](notify/README.md)；網頁 → [web/README.md](web/README.md)。

## 排程

正式排程見[部署](#部署)。`run_daily.sh` 是唯一的排程入口，Docker 的 scheduler 容器、systemd timer 與 cron 都呼叫它：

```cron
0 8 * * * $HOME/newsletter-ops/run_daily.sh
```

它依序跑 `newsletter-fetch --no-report` → `agent_run.py` → `render_email.py` → `send_email.py`（`render_email.py` 的 stdout 是一行 JSON，
先收進變數再餵給 `send_email.py`）。任一步失敗就停下、以該步的 exit code 結束（`agent_run.py` 的 1～6 見 [agent/README.md](agent/README.md)「exit code」），
並在 `logs/<YYYY-MM>.log` 留一行失敗紀錄。額外參數（如 `--lookback 72`）轉給 `newsletter-fetch`。
先由 `newsletter-fetch` 抓好當日 curated JSON，agent 讀到的就是今天的檔，不必自己再抓一次。

內部用 `uv run --locked` 執行，並替 cron 精簡的 PATH 補上 uv 常見的安裝位置（`~/.local/bin`、`~/.cargo/bin`、Homebrew）。
`--locked` 讓鎖檔與 `pyproject.toml` 對不上時直接失敗，不會在排程裡自己改鎖檔。

機密從 env 檔載入（預設 `~/.config/newsletter-ops/env`，`NEWSLETTER_ENV_FILE` 可改，範本在 `deploy/newsletter.env.example`），
權限必須是 `600`，太鬆會拒絕執行（exit 78）。env 檔是 shell 語法，值含空格（Gmail 應用程式密碼）要加引號。
排程需要 OAuth token（環境變數 `CLAUDE_CODE_OAUTH_TOKEN`，或 `/auth` 頁面存的檔，見 [agent/README.md](agent/README.md)「認證」）；兩者都沒有時 `agent_run.py` 直接 exit 2。
macOS 的 cron 需要「完整磁碟取用權」，或改用 launchd。

## 讓它跟著你的研究重心走

興趣會變，但系統不會自己察覺——報告每天照常產出，你只是慢慢覺得「最近好像沒什麼有趣的」。
所以分兩條路處理：

**突變**（換題目、接新案子）→ 改 `config/interests.md`，用自然語言寫「核心 / 關注 /
背景 / 不要」四級，並更新檔頂的 `updated:` 日期。`news-digest` skill 每次寫報告前都讀它，
skill 本身不存任何興趣清單。超過 90 天沒更新時，`newsletter-fetch` 每次執行都會在 stderr 提醒一行。

**漸變**（慢慢偏移，自己察覺不到）→ 靠標記累積資料。看完報告每則填 `+`（有用）、`-`（沒用）：
可以直接寫在報告 Markdown 裡再跑 `uv run newsletter-feedback` 收集（格式見 [shared/README.md](shared/README.md)「回饋標記」），
或在網頁（[web/README.md](web/README.md)）、email 連結（[notify/README.md](notify/README.md)）按按鈕，都累積到 `state/feedback.jsonl`。

累積兩三個月後可以看出：收錄很多卻從未拿到 `+` 的關鍵字該降權、`+` 項目裡反覆出現卻
不在 boost 清單的詞該加進去、長期沒命中的關鍵字該移除。

**建議由人確認，不自動套用。** 會自己調參數的系統，出錯時你查不出它為什麼開始推垃圾。

## 部署

目標：pipeline 與 web 跑在家裡一台 host（VM 也行，只要有 Docker），經 Cloudflare 在外也能看晨報、按有用／沒用。本機只當測試區。

```
手機／筆電 ──https──▶ Cloudflare（Access：只放行你的 email）──Tunnel──▶ cloudflared 容器 ──▶ web 容器
                                                                    scheduler 容器：每天 08:00 跑 run_daily.sh
```

`web` 沒有登入、而且能寫入 `state/feedback.jsonl`，**唯一的防線是 Cloudflare Access**；Tunnel 的網域一定要先掛上 Access 再對外使用，也不要為了方便而 publish 任何 port。
Docker（`docker compose up -d --build`）與不用 Docker 的 systemd + cloudflared 兩種做法、Cloudflare 設定與驗收清單見 [deploy/README.md](deploy/README.md)。

## 資料保存：不保存

生成物——`reports/`、`data/`、`state/`（含 `state/feedback.jsonl`）、`logs/`——不備份、不進版控（Docker 部署時放在 volume `newsletter-data`，見 [deploy/README.md](deploy/README.md)），
壞了就重來：抓取結果可以重跑，`seen.json` 沒了只會讓時間窗內已報過的新聞再出現一次，
`feedback.jsonl` 只是改 `config/interests.md` 時參考的中間產物（本 repo 是 public，標記含標題與來源，更不該推上來）。

版控裡只有程式、設定與 `interests.md`；這些是唯一需要「持續更新」的內容，`git pull` 後重 build 即可。
要新增被追蹤的檔案就加進 `.gitignore` 的白名單（`.dockerignore` 也是白名單，要進映像的話一併加）。
之後若想留報告歷史，另外決定備份方式（例如定期把 volume 打包），不要放進這個 repo。
