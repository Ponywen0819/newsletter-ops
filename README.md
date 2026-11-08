# newsletter-ops

每日新聞自動蒐集 → 整理 → 報告的 pipeline。主題：AI / 研究論文動態 + 科技產業與市場。

這份 README 只放專案概覽；各模組怎麼運作、怎麼調整，看該模組目錄裡的 README。已知問題與限制記在各模組的 `ISSUES.md`：[collect](packages/collect/ISSUES.md)、[shared](packages/shared/ISSUES.md)。

## 架構

monorepo，用 [Nx](https://nx.dev/) 管所有模組（不分語言）的任務與依賴圖；依賴本身仍由各語言的工具管：
每個 Python 模組是獨立的 uv 專案（自己的 `uv.lock`、`.venv`），依賴其他模組時用 path 依賴以 editable 裝進來；
前端用 npm。Nx 透過 [`@nxlv/python`](https://github.com/lucasvieirasilva/nx-plugins) 從 `pyproject.toml` 讀出 Python 模組間的依賴。
依賴方向由 `deploy/check_boundaries.py` 檢查（main 上的 CI 也跑），違規的 import 會失敗：

```
shared ◄── llm ◄─┬── collect
   ▲             ├── writer
   │             └── web-server ◄┄┄ web-ui    web-ui 不 import 任何模組，只呼叫 web-server 的 API
   └────── notify

worker ──▶ shared、collect、writer、notify      apps/：網頁以外的常駐服務；沒有任何成員依賴它
```

collect 與 writer 互不依賴：蒐集只寫 `data/curated/<date>.json`，寫報告只讀它。

| 模組 | 套件名 | 語言 | 職責 | 文件 |
| --- | --- | --- | --- | --- |
| [packages/shared/](packages/shared/) | `newsletter-shared` | Python | 共用底層（標準庫）：repo 路徑、報告格式的解析與驗證、回饋標記、量測、OAuth token 儲存 | [README](packages/shared/README.md) |
| [packages/llm/](packages/llm/) | `newsletter-llm` | Python | Claude Agent SDK 的共用執行器：OAuth 認證、只開指定工具、失敗分類、用量紀錄。唯一的第三方依賴 `claude-agent-sdk` 在這裡 | [README](packages/llm/README.md) |
| [packages/collect/](packages/collect/) | `newsletter-collect` | Python | 蒐集：RSS／Atom／arXiv 抓取 → 整理評分 → LLM 搜尋補 RSS 弱的主題（程式驗收）→ 補原文，產出 curated JSON | [README](packages/collect/README.md) |
| [packages/writer/](packages/writer/) | `newsletter-writer` | Python | 寫報告：Claude 依 skill 把 curated JSON 寫成 `reports/<date>.md` 並驗收；不上網、**不認得 email** | [README](packages/writer/README.md) |
| [packages/notify/](packages/notify/) | `newsletter-notify` | Python | 外圍：報告 → email → 寄出；之後加別的通知管道放這裡 | [README](packages/notify/README.md) |
| [apps/web-server/](apps/web-server/) | `newsletter-web-server` | Python | 晨報網頁的後端 JSON API（指令 `newsletter-web`），並提供 web-ui 的建置結果；依賴 `llm` 只為了驗證 OAuth token | [README](apps/web-server/README.md) |
| [apps/web-ui/](apps/web-ui/) | `newsletter-web-ui` | TypeScript | 晨報網頁的前端：Vite + React PWA | [README](apps/web-ui/README.md) |
| [apps/worker/](apps/worker/) | `newsletter-worker` | Python | 網頁以外的常駐服務：每日排程、補跑，把 collect → write → render → send 四步串起來，整條握一把防重疊的鎖 | [README](apps/worker/README.md) |

### 模組慣例

- **apps/**：可以執行、部署的模組，**沒有模組可以依賴它**；**packages/**：被其他模組依賴的函式庫。
- 目錄名 = id = Nx 專案名；套件名是 `newsletter-<id>`（`packages/collect/` → `newsletter-collect`），import 名把連字號換成底線（`newsletter_collect`）。
  前綴只留在套件名：import 名在 venv 裡是全域的，`llm`、`shared` 這種名字容易撞到第三方套件。
  指令名不跟套件名綁在一起：被依賴的模組的指令會裝進每個依賴它的 venv，所以帶前綴（`newsletter-collect`、`newsletter-web`…）；最頂層的 worker 就叫 `worker`。
- 每個模組有一個 `project.json`（`name` 是 id），定義 `lint`、`selftest` 等任務；前端的任務直接來自 `package.json` 的 scripts。
- 新增模組：建目錄與 `project.json`；Python 模組再 `uv lock --project <目錄>` 產生自己的鎖檔；最後登記到 `deploy/check_boundaries.py` 的 `MEMBERS`（Python）或 `NODE_MODULES`。
  `.gitignore`、`.dockerignore` 用萬用字元放行 `*/*/` 底下的模組檔，不必另外加；Dockerfile 要不要帶進映像則要自己決定。

分工原則：**確定性的部分交給 Python，判斷性的部分交給 Claude；Claude 的產出由 Python 驗收。**
抓取、去重、排序、arXiv 會議判斷、HTML 版型全部可重現。Claude 用在兩處，各自只開需要的工具：
蒐集時用 WebSearch 補 RSS 弱的主題（程式抓網址驗收，紀錄在 `data/search/`），
寫報告時依 `news-digest` skill 讀 curated JSON 改寫 `reports/<date>.md`（不上網，內容都查得到出處）。

### 目錄

```
apps/ packages/                模組，內容見上表；已知問題在各模組的 ISSUES.md
config/interests.md            關注範圍（自然語言），報告判讀的依據
config/config.json             執行參數：時間窗、關鍵字權重、收錄門檻、HTTP 設定
config/sources.d/*.json        來源清單，一個主題一個檔
.claude/skills/news-digest/    報告撰寫的 skill，隨專案進版控
Dockerfile, docker-compose.yml, docker/   容器部署：一個 Dockerfile 建 web、worker 兩個映像，加上 cloudflared；生成物放 volume
deploy/                        部署說明、自動部署、systemd 單元與範本；check_boundaries.py 依賴方向檢查
ruff.toml                      全 repo 共用的 Ruff 設定；.python-version 固定所有 Python 模組的直譯器版本
package.json, nx.json          只裝 Nx（含 @nxlv/python）與它的設定；前端自己的依賴在 apps/web-ui/package.json

# 執行期生成物（都不進版控；位置都在 repo 根，Docker 裡是 volume）
data/raw/<date>.jsonl          當日原始抓取（append，供回溯）
data/search/<date>.json        搜尋補充的稽核紀錄：缺口、query、LLM 原始回覆、收下與退回（含原因）
data/curated/<date>.json       排序後的收錄清單（含搜尋補充與原文）← SKILL 的輸入
reports/<date>.md              最終報告
reports/<date>.html            email 版（newsletter-render 產生）
state/seen.json                30 天去重記憶
state/feedback.jsonl           累積的人工標記，供日後調整關鍵字與 interests.md
state/oauth_token.json         /auth 頁面存的 OAuth token（權限 600）
state/auth_health.json         最近一次 Claude 認證驗證的結果（worker 每天寫、web 讀來顯示橫幅）
state/last_run.json            上次每日流程的開始、結束與 exit code（worker 據此判斷今天跑過沒）
state/pipeline.lock            每日流程的防重疊鎖（空檔，只是鎖的載體）
logs/<YYYY-MM>.log             排程執行紀錄
```

## 環境（uv）

Python 版本與依賴由 [uv](https://docs.astral.sh/uv/) 管理：`.python-version` 固定直譯器（3.11，所有模組共用），每個模組的 `uv.lock` 鎖定它自己的依賴。
安裝 uv 後不必自己建 venv，`uv run --project <模組>` 第一次執行會在該模組底下建 `.venv` 並裝好依賴；沒有該版本的 Python 時 uv 會自動下載。

```bash
uv sync --project apps/worker                    # 依該模組的 uv.lock 同步它的環境
uv add --project packages/collect <套件>              # 替某個模組新增依賴（改它的 pyproject.toml 與 uv.lock，一起 commit）
uv lock --project packages/collect --upgrade         # 升級該模組鎖定的版本
```

改了被依賴的模組的依賴（例如 `llm` 加了套件）時，依賴它的模組也要各自 `uv lock --project …`，否則 `--locked` 會失敗。

指令都在 repo 根目錄執行：程式讀寫的 `config/`、`reports/`、`data/`、`state/`、`logs/` 以**工作目錄**為根（`newsletter_shared.ROOT`），
要放別處就設環境變數 `NEWSLETTER_ROOT`。

所有模組的 lint、自我檢查、前端測試都由 Nx 跑（main 上的 CI 也跑這一行）。第一次先在根目錄與 `apps/web-ui` 各 `npm ci`：

```bash
npx nx run-many -t lint selftest typecheck test   # 全部模組；改成 `nx affected -t …` 只跑受影響的
npx nx run collect:selftest                         # 單一模組的單一任務
npx nx graph                                      # 模組依賴圖
```

Python 的 lint 是 `uvx ruff@0.17.3 check`（規則在 ruff.toml），前端是 oxlint（規則在 apps/web-ui/.oxlintrc.json）。不屬於任何模組的檢查另外跑：

```bash
git ls-files '*.sh' | xargs uvx --from shellcheck-py shellcheck --severity=warning
uv run --no-project python deploy/check_boundaries.py
```

commit 前自動跑 Ruff（每個 clone 啟用一次；要略過用 `git commit --no-verify`）：

```bash
git config core.hooksPath .githooks
```

抓取、整理、寄信都只用標準庫，只有 `llm` 模組需要第三方套件（`claude-agent-sdk`），collect 的搜尋與 writer 經由它用 Claude。
skill 裡呼叫的指令都是 `uv run --locked --project <模組> …`，要在 repo 根目錄、有 uv 的環境下執行。

## 快速開始

```bash
uv run --project packages/collect newsletter-collect --dry-run   # 只測來源連通性
uv run --project packages/collect newsletter-collect             # 蒐集，產 data/curated/<date>.json（搜尋補充需要 OAuth token，沒有就略過）
uv run --project packages/writer newsletter-write                # Claude 依 skill 寫 reports/<date>.md 並驗收（需要 OAuth token）
uv run --project packages/notify newsletter-render | uv run --project packages/notify newsletter-send   # 轉成 email 並寄出
npx nx run worker:once                                  # 整條流程一次跑完（蒐集 → 寫報告 → render → 寄信），和排程跑的是同一件事；有防重疊的鎖
npx nx run worker:start                                 # 常駐排程服務（每天 08:00，見「排程」）
(cd apps/web-ui && npm install && npm run build)        # 第一次（以及改了前端之後）：建置網頁前端
uv run --project apps/web-server newsletter-web                 # 晨報網頁，預設 http://127.0.0.1:8787
uv run --project packages/shared newsletter-feedback            # 收集報告裡填的標記
```

各指令的選項與細節：蒐集 → [collect/README.md](packages/collect/README.md)；寫報告 → [writer/README.md](packages/writer/README.md)；認證與用量 → [llm/README.md](packages/llm/README.md)；email 與寄信 → [notify/README.md](packages/notify/README.md)；網頁 → [web-server/README.md](apps/web-server/README.md)。

## 排程

排程只有一種：常駐的 `worker`，Docker 是 `worker` 容器，不用 Docker 是 systemd 的 `deploy/newsletter-worker.service`。
每天 08:00（`NEWSLETTER_RUN_AT` 可改）跑一次，輸出進 `logs/<YYYY-MM>.log`；停機錯過了，啟動後補跑（同一天內；失敗的不重跑，要重跑用 `POST /run`）。
手動的 `worker --once` 和它進同一個 `run_pipeline()`：四步、失敗語意、防重疊的鎖與執行紀錄 `state/last_run.json` 見 [apps/worker/README.md](apps/worker/README.md)，所以混用是安全的。

每天 07:30（`NEWSLETTER_AUTH_CHECK_AT` 可改）worker 也會驗一次 Claude 認證，結果寫進 `state/auth_health.json`；失效的話網頁在晨報跑之前就會出現橫幅。

## 讓它跟著你的研究重心走

興趣會變，但系統不會自己察覺——報告每天照常產出，你只是慢慢覺得「最近好像沒什麼有趣的」。
所以分兩條路處理：

**突變**（換題目、接新案子）→ 改 `config/interests.md`，用自然語言寫「核心 / 關注 /
背景 / 不要」四級，並更新檔頂的 `updated:` 日期。`news-digest` skill 每次寫報告前都讀它，
skill 本身不存任何興趣清單。超過 90 天沒更新時，`newsletter-collect` 每次執行都會在 stderr 提醒一行。

**漸變**（慢慢偏移，自己察覺不到）→ 靠標記累積資料。看完報告每則填 `+`（有用）、`-`（沒用）：
可以直接寫在報告 Markdown 裡再跑 `uv run --project packages/shared newsletter-feedback` 收集（格式見 [shared/README.md](shared/README.md)「回饋標記」），
或在網頁（[web-server/README.md](apps/web-server/README.md)）、email 連結（[notify/README.md](packages/notify/README.md)）按按鈕，都累積到 `state/feedback.jsonl`。

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
