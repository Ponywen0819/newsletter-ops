# newsletter-ops

每日新聞自動蒐集 → 整理 → 報告的 pipeline。主題：AI / 研究論文動態 + 科技產業與市場。

這份 README 只放專案概覽；各模組怎麼運作、怎麼調整，看該模組目錄裡的 README。已知問題與限制記在各模組的 `ISSUES.md`：[agent](packages/agent/ISSUES.md)、[shared](packages/shared/ISSUES.md)。

## 架構

monorepo，用 [Nx](https://nx.dev/) 管所有模組（不分語言）的任務與依賴圖；依賴本身仍由各語言的工具管：
每個 Python 模組是獨立的 uv 專案（自己的 `uv.lock`、`.venv`），依賴其他模組時用 path 依賴以 editable 裝進來；
前端用 npm。Nx 透過 [`@nxlv/python`](https://github.com/lucasvieirasilva/nx-plugins) 從 `pyproject.toml` 讀出 Python 模組間的依賴。
依賴方向由 `deploy/check_boundaries.py` 檢查（main 上的 CI 也跑），違規的 import 會失敗：

```
shared ◄── agent ◄── web
   ▲
   └────── notify

pipeline ──▶ shared、agent、notify    apps/：只負責編排每日流程；沒有任何成員依賴它
```

| 模組 | Nx 專案（套件名） | 語言 | 職責 | 文件 |
| --- | --- | --- | --- | --- |
| [packages/shared/](packages/shared/) | `newsletter-shared` | Python | 共用底層（標準庫）：repo 路徑、報告格式的解析與驗證、回饋標記、量測、OAuth token 儲存 | [README](packages/shared/README.md) |
| [packages/agent/](packages/agent/) | `newsletter-agent` | Python | 核心：抓取 → 整理 → Claude 寫報告。唯一的第三方依賴 `claude-agent-sdk` 在這裡，**不認得 email** | [README](packages/agent/README.md) |
| [packages/notify/](packages/notify/) | `newsletter-notify` | Python | 外圍：報告 → email → 寄出；之後加別的通知管道放這裡 | [README](packages/notify/README.md) |
| [web/server/](web/server/) | `newsletter-web` | Python | 晨報網頁的後端 JSON API；依賴 `agent` 只為了驗證 OAuth token | [README](web/README.md) |
| [web/ui/](web/ui/) | `newsletter-web-ui` | TypeScript | 晨報網頁的前端：Vite + React PWA | [README](web/ui/README.md) |
| [apps/pipeline/](apps/pipeline/) | `newsletter-pipeline` | Python | 每日流程編排：fetch → agent → render → send 四步串起來，整條握一把防重疊的鎖；排程與手動補跑都走它 | [README](apps/pipeline/README.md) |

`web/` 之後會拆成 `apps/web-server/`（`newsletter-web-server`）與 `apps/web-ui/`。

### 模組慣例

- **apps/**：可以執行、部署的模組，**沒有模組可以依賴它**；**packages/**：被其他模組依賴的函式庫。
- 目錄名 = id = 套件名去掉 `newsletter-`（`packages/agent/` → `newsletter-agent`）；Python 的 import 名把連字號換成底線（`newsletter_agent`）。
  指令名（`newsletter-run`、`newsletter-web`…）不跟套件名綁在一起。
- 每個模組有一個 `project.json`（Nx 專案名 = 套件名），定義 `lint`、`selftest` 等任務；前端的任務直接來自 `package.json` 的 scripts。
- 新增模組：建目錄與 `project.json`；Python 模組再 `uv lock --project <目錄>` 產生自己的鎖檔；最後登記到 `deploy/check_boundaries.py` 的 `MEMBERS`（Python）或 `NODE_MODULES`。
  `.gitignore`、`.dockerignore` 用萬用字元放行 `*/*/` 底下的模組檔，不必另外加；Dockerfile 要不要帶進映像則要自己決定。

分工原則：**確定性的部分交給 Python，判斷性的部分交給 Claude。**
抓取、去重、排序、arXiv 會議判斷、HTML 版型全部可重現；挑選重點與條列改寫由 Claude
依 `news-digest` skill 讀 curated JSON 後改寫 `reports/<date>.md`。

### 目錄

```
apps/ packages/ web/           模組，內容見上表；已知問題在各模組的 ISSUES.md
config/interests.md            關注範圍（自然語言），報告判讀的依據
config/config.json             執行參數：時間窗、關鍵字權重、收錄門檻、HTTP 設定
config/sources.d/*.json        來源清單，一個主題一個檔
.claude/skills/news-digest/    報告撰寫的 skill，隨專案進版控
run_daily.sh                   排程入口（見「排程」）
Dockerfile, docker-compose.yml, docker/   容器部署：web + scheduler + cloudflared，生成物放 volume
deploy/                        部署說明、自動部署、systemd 單元與範本；check_boundaries.py 依賴方向檢查
ruff.toml                      全 repo 共用的 Ruff 設定；.python-version 固定所有 Python 模組的直譯器版本
package.json, nx.json          只裝 Nx（含 @nxlv/python）與它的設定；前端自己的依賴在 web/ui/package.json

# 執行期生成物（都不進版控；位置都在 repo 根，Docker 裡是 volume）
data/raw/<date>.jsonl          當日原始抓取（append，供回溯）
data/curated/<date>.json       排序後的收錄清單 ← SKILL 的輸入
reports/<date>.md              最終報告
reports/<date>.html            email 版（newsletter-render 產生）
state/seen.json                30 天去重記憶
state/feedback.jsonl           累積的人工標記，供日後調整關鍵字與 interests.md
state/oauth_token.json         /auth 頁面存的 OAuth token（權限 600）
state/pipeline.lock            newsletter-run 的防重疊鎖（空檔，只是鎖的載體）
logs/<YYYY-MM>.log             排程執行紀錄
```

## 環境（uv）

Python 版本與依賴由 [uv](https://docs.astral.sh/uv/) 管理：`.python-version` 固定直譯器（3.11，所有模組共用），每個模組的 `uv.lock` 鎖定它自己的依賴。
安裝 uv 後不必自己建 venv，`uv run --project <模組>` 第一次執行會在該模組底下建 `.venv` 並裝好依賴；沒有該版本的 Python 時 uv 會自動下載。

```bash
uv sync --project apps/pipeline                    # 依該模組的 uv.lock 同步它的環境
uv add --project packages/agent <套件>              # 替某個模組新增依賴（改它的 pyproject.toml 與 uv.lock，一起 commit）
uv lock --project packages/agent --upgrade         # 升級該模組鎖定的版本
```

改了被依賴的模組的依賴（例如 `agent` 加了套件）時，依賴它的模組也要各自 `uv lock --project …`，否則 `--locked` 會失敗。

指令都在 repo 根目錄執行：程式讀寫的 `config/`、`reports/`、`data/`、`state/`、`logs/` 以**工作目錄**為根（`newsletter_shared.ROOT`），
要放別處就設環境變數 `NEWSLETTER_ROOT`。

所有模組的 lint、自我檢查、前端測試都由 Nx 跑（main 上的 CI 也跑這一行）。第一次先在根目錄與 `web/ui` 各 `npm ci`：

```bash
npx nx run-many -t lint selftest typecheck test   # 全部模組；改成 `nx affected -t …` 只跑受影響的
npx nx run newsletter-agent:selftest              # 單一模組的單一任務
npx nx graph                                      # 模組依賴圖
```

Python 的 lint 是 `uvx ruff@0.17.3 check`（規則在 ruff.toml），前端是 oxlint（規則在 web/ui/.oxlintrc.json）。不屬於任何模組的檢查另外跑：

```bash
git ls-files '*.sh' | xargs uvx --from shellcheck-py shellcheck --severity=warning
uv run --no-project python deploy/check_boundaries.py
```

commit 前自動跑 Ruff（每個 clone 啟用一次；要略過用 `git commit --no-verify`）：

```bash
git config core.hooksPath .githooks
```

抓取、整理、寄信都只用標準庫，只有 `agent` 模組（`agent_run`）需要第三方套件（`claude-agent-sdk`）。
skill 裡由 agent 呼叫的指令都是 `uv run --locked --project <模組> …`，要在 repo 根目錄、有 uv 的環境下執行。

## 快速開始

```bash
uv run --project packages/agent newsletter-fetch --dry-run     # 只測來源連通性
uv run --project packages/agent newsletter-fetch --no-report   # 只產 curated JSON，報告留給 Claude 寫
uv run --project packages/agent newsletter-agent               # Claude 依 skill 寫 reports/<date>.md 並驗收（需要 OAuth token）
uv run --project packages/notify newsletter-render | uv run --project packages/notify newsletter-send   # 轉成 email 並寄出
uv run --project apps/pipeline newsletter-run                 # 整條流程一次跑完（抓取 → agent → render → 寄信），排程跑的就是它；有防重疊的鎖
(cd web/ui && npm install && npm run build)        # 第一次（以及改了前端之後）：建置網頁前端
uv run --project web/server newsletter-web                 # 晨報網頁，預設 http://127.0.0.1:8787
uv run --project packages/shared newsletter-feedback            # 收集報告裡填的標記
```

各指令的選項與細節：抓取與 agent → [agent/README.md](agent/README.md)；email 與寄信 → [notify/README.md](notify/README.md)；網頁 → [web/README.md](web/README.md)。

## 排程

`run_daily.sh` 是唯一的排程入口：補 PATH、載入 env 檔（預設 `~/.config/newsletter-ops/env`，權限須為 600）、輸出導向 `logs/<YYYY-MM>.log`，再交給 `newsletter-run`（四步、失敗語意與防重疊的鎖見 [pipeline/README.md](pipeline/README.md)）。

| 觸發 | 時間 | 錯過了（停機、關機） |
| --- | --- | --- |
| Docker 的 scheduler 容器 | 每天 08:00，`.env` 的 `NEWSLETTER_RUN_AT` 可改 | 不補跑 |
| systemd timer（`deploy/newsletter-daily.timer`） | 每天 08:00 | 開機後補跑 |
| cron：`0 8 * * * $HOME/newsletter-ops/run_daily.sh` | 自訂 | 不補跑（macOS 要給 cron「完整磁碟取用權」） |

**規劃中**：scheduler 升級成常駐服務見 [#41](https://github.com/Ponywen0819/newsletter-ops/issues/41)；認證的定時驗證從 web 搬過去見 [#39](https://github.com/Ponywen0819/newsletter-ops/issues/39)。

## 讓它跟著你的研究重心走

興趣會變，但系統不會自己察覺——報告每天照常產出，你只是慢慢覺得「最近好像沒什麼有趣的」。
所以分兩條路處理：

**突變**（換題目、接新案子）→ 改 `config/interests.md`，用自然語言寫「核心 / 關注 /
背景 / 不要」四級，並更新檔頂的 `updated:` 日期。`news-digest` skill 每次寫報告前都讀它，
skill 本身不存任何興趣清單。超過 90 天沒更新時，`newsletter-fetch` 每次執行都會在 stderr 提醒一行。

**漸變**（慢慢偏移，自己察覺不到）→ 靠標記累積資料。看完報告每則填 `+`（有用）、`-`（沒用）：
可以直接寫在報告 Markdown 裡再跑 `uv run --project packages/shared newsletter-feedback` 收集（格式見 [shared/README.md](shared/README.md)「回饋標記」），
或在網頁（[web/README.md](web/README.md)）、email 連結（[notify/README.md](notify/README.md)）按按鈕，都累積到 `state/feedback.jsonl`。

累積兩三個月後可以看出：收錄很多卻從未拿到 `+` 的關鍵字該降權、`+` 項目裡反覆出現卻
不在 boost 清單的詞該加進去、長期沒命中的關鍵字該移除。

**建議由人確認，不自動套用。** 會自己調參數的系統，出錯時你查不出它為什麼開始推垃圾。

## 部署

pipeline 與 web 跑在家裡一台 host，經 Cloudflare Tunnel + Access 對外；本機只當測試區。`web` 沒有登入，**唯一的防線是 Access**。
做法（Docker 與 systemd 兩種）、自動部署、Cloudflare 設定與驗收清單見 [deploy/README.md](deploy/README.md)。

## 資料保存：不保存

生成物——`reports/`、`data/`、`state/`（含 `state/feedback.jsonl`）、`logs/`——不備份、不進版控（Docker 部署時放在 volume `newsletter-data`，見 [deploy/README.md](deploy/README.md)），
壞了就重來：抓取結果可以重跑，`seen.json` 沒了或壞了（讀不了時當作空的）只會讓時間窗內已報過的新聞再出現一次，
`feedback.jsonl` 只是改 `config/interests.md` 時參考的中間產物（本 repo 是 public，標記含標題與來源，更不該推上來）。

版控裡只有程式、設定與 `interests.md`；這些是唯一需要「持續更新」的內容，`git pull` 後重 build 即可。
要新增被追蹤的檔案就加進 `.gitignore` 的白名單（`.dockerignore` 也是白名單，要進映像的話一併加）。
之後若想留報告歷史，另外決定備份方式（例如定期把 volume 打包），不要放進這個 repo。
