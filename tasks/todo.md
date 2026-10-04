# Task List：newsletter-ops 改成 monorepo

順序、依賴圖、標準搬移清單（M1–M8）、驗證工具（V-CI／V-GOLD／V-DOCKER／V-GREP／V-GIT）與完成定義都在 [plan.md](plan.md)。
任務寫「特例」；沒寫的照 M1–M8。規格見 [SPEC.md](../SPEC.md) 與 `SPEC-<module>.md`。

`BASE_SHA`：`844d60d`（origin/main，T0 完成時記錄；基準檔用 `golden.sh old` 從該 SHA 的 worktree 產生）

---

## Phase 0：準備

- [x] **T0：commit 規格與計畫、合併 main、建立 golden 基準**
  - Description：把本分支更新到最新 main，commit 規格與計畫，並用舊程式產生之後每個任務要對照的基準輸出。
  - Acceptance：
    - [x] `git merge-base --is-ancestor origin/main HEAD` 成立；把當時 main 的 SHA 填進上面的 `BASE_SHA`
    - [x] `SPEC.md`、`SPEC-*.md`（5 份）、`tasks/plan.md`、`tasks/todo.md` 已追蹤；`.gitignore` 已放行 `SPEC*.md`、`tasks/*.md`
    - [x] 基準 worktree 在 `BASE_SHA`；`golden.sh old` 在其中產出 `$G/old.*` 六個檔且皆非空
    - [x] 合併後的樹上，現有 CI 全套通過（此時仍是裸 `python3 src/…`）
  - Verification：
    - [x] `git ls-files SPEC.md tasks/plan.md tasks/todo.md`
    - [x] V-CI（舊版 CI 檔，原樣）
    - [x] `ls -l "$G"/old.*`
  - Dependencies：無
  - Files：`SPEC*.md`（6）、`tasks/*.md`（2）、`.gitignore`
  - Scope：S
  - 備註：本 session 沒有 `sync_with_base_branch`，用 `git merge origin/main` 手動合；合併前工作區只有未追蹤的規格檔與 `.gitignore` 的修改，先 commit 再合。

## Phase 1：shared

- [x] **T1：workspace 骨架 ＋ `shared` 空殼 ＋ Docker 能建**（本計畫風險最高的一項，所以最早）
  - Description：根 `pyproject.toml` 保留 `[project]` 並加上 workspace 與 `newsletter-shared` 依賴；建立只有 `paths.py` 的 `shared` 套件；Dockerfile 支援 workspace 並升級 uv 釘選。沒有任何既有程式被改動。
  - Acceptance：
    - [x] 根 `[tool.uv.workspace] members = ["shared"]`；根 `dependencies` 含 `claude-agent-sdk>=0.2.123` 與 `newsletter-shared`（`{ workspace = true }`）；`package = false` 保留
    - [x] `shared/pyproject.toml`（uv_build `>=0.11.13,<0.12`）、`newsletter_shared/__init__.py`、`paths.py`（`ROOT = Path(__file__).resolve().parents[3]`）；`python -m newsletter_shared.paths` 跑 selftest：斷言 `ROOT/"uv.lock"` 存在且 `ROOT/"config"` 是目錄
    - [x] Dockerfile：`ghcr.io/astral-sh/uv:0.11.13`；先 `COPY` 根與成員的 `pyproject.toml`、`uv sync --locked --no-install-workspace`（依賴層），再 `COPY shared`、`uv sync --locked`；其餘不變
    - [x] `.gitignore`／`.dockerignore` 放行 `shared/pyproject.toml`、`shared/src/**/*.py`
    - [x] `uv.lock` 裡 `claude-agent-sdk` 版本與 T0 時相同
  - Verification：
    - [x] `uv lock --check && uv sync --locked && uv run --locked python -m newsletter_shared.paths`
    - [x] `git diff HEAD -- uv.lock | grep -B1 -A2 'claude-agent-sdk'` 沒有版本變動
    - [x] V-DOCKER（建置成功；`docker run … python -c "import newsletter_shared"` 成功；web 容器 `/api/reports` 回 200）
    - [x] V-CI（CI 檔此任務不改，仍是裸 `python3 src/…`，應仍綠）
    - [x] V-GIT
  - Dependencies：T0
  - Files：`pyproject.toml`、`uv.lock`、`shared/pyproject.toml`、`shared/src/newsletter_shared/{__init__,paths}.py`、`Dockerfile`、`.dockerignore`、`.gitignore`
  - Scope：M（配置為主，3 個小檔；這些無法拆開而不讓 Docker 或 CI 變紅）

- [x] **T2：工具鏈——CI 與 skill 改用 `uv run`、邊界檢查上線**
  - Description：先把「呼叫程式」的入口換成 uv，之後任何 `src/*.py` import `newsletter_shared` 才不會壞。同時加上依賴方向檢查，保護後面每一步。
  - Acceptance：
    - [x] `selftest.yml`：`astral-sh/setup-uv`（`version: "0.11.13"`、`enable-cache: true`）＋ `uv sync --locked`；每個既有檢查改成 `uv run --locked python <原路徑>`（路徑此時不變）；新增 `python3 deploy/check_boundaries.py`
    - [x] skill 裡 `python3 src/X.py` 一律改 `uv run --locked src/X.py`（run.py、render_email.py、metrics.py、feedback.py）
    - [x] `deploy/check_boundaries.py`（標準庫＋`ast`，約 30 行）：內建依賴表 shared→∅、agent→{shared}、notify→{shared}、web→{shared,agent}；掃每個**存在的**成員 `src/**/*.py` 的 `newsletter_*` import，超出就列出並 exit 1；成員的 `pyproject.toml` `dependencies` 也要與表一致
    - [x] `.gitignore` 放行 `deploy/*.py`
  - Verification：
    - [x] V-CI（新版 CI 檔）全綠
    - [x] 反向驗證：暫時在 `shared/.../paths.py` 加 `import newsletter_agent`，`check_boundaries.py` 必須 exit 1；還原後 exit 0
    - [x] `git diff` 裡 skill 沒有殘留 `python3 src/`
  - Dependencies：T1
  - Files：`.github/workflows/selftest.yml`、`.claude/skills/news-digest/SKILL.md`、`deploy/check_boundaries.py`、`.gitignore`
  - Scope：S

- [x] **T3：`feedback` 搬進 `shared`**
  - Description：回饋 mark 格式、檔案鎖、`collect` 搬進 shared，改寫所有 importer。
  - Acceptance：
    - [x] `src/feedback.py` → `shared/src/newsletter_shared/feedback.py`；`ROOT` 改用 `paths.ROOT`；加 `main()`（`--selftest` 或 collect），`[project.scripts] newsletter-feedback`
    - [x] importer 全改：`src/report.py`、`src/report_data.py`、`src/auth_store.py`、`src/web.py`；`grep -rnE '^(import|from) feedback\b' src` 無結果
    - [x] CI 的 `src/feedback.py --selftest` 行 → `newsletter-feedback --selftest`；skill 的 `uv run --locked src/feedback.py` → `uv run --locked newsletter-feedback`；README 對應段落
  - Verification：
    - [x] V-CI；`uv run --locked newsletter-feedback --selftest`
    - [x] `check_boundaries.py`（shared 不 import 其他成員）；V-GREP（`Path(__file__)` 只在 `paths.py`）
    - [x] V-GIT（`git log --follow shared/src/newsletter_shared/feedback.py` 追得到歷史）
  - Dependencies：T2
  - Files：（搬）`feedback.py`；（改）`src/report.py`、`src/report_data.py`、`src/auth_store.py`、`src/web.py`、`shared/pyproject.toml`、`selftest.yml`、`SKILL.md`、`README.md`
  - Scope：M（1 搬 ＋ 多個一行 import 改動）

- [x] **T4：`report_data` 搬進 `shared` ＋ 新增 `newsletter-report-check`**
  - Description：報告格式的唯一解析與驗證者搬進 shared，並提供 CLI，讓 agent 之後能自我修正報告（取代「跑 render 看有沒有報錯」）。
  - Acceptance：
    - [x] `src/report_data.py` → `shared/.../report_data.py`；importer 改：`src/render_email.py`、`src/web.py`
    - [x] `newsletter-report-check [YYYY-MM-DD]`：讀 `ROOT/reports/<date>.md`（預設今天），成功 exit 0；`ValueError` → 訊息到 stderr、exit 1。`[project.scripts]`、`--selftest` 保留
    - [x] selftest 新增：缺 `<!-- subject: … -->`、缺「今日頭條」兩種報告各自 exit 1
    - [x] `parse_report` 對 fixture 的輸出與基準完全相同
  - Verification：
    - [x] V-GOLD（`new` 用 `RENDER="uv run --locked src/render_email.py" WEB="uv run --locked src/web.py" FETCH="uv run --locked src/run.py"`）：`parse.json`、`render*.html`、`api.json` 與 old 相同
    - [x] `uv run --locked newsletter-report-check 2026-10-04` → 0；對刪掉 subject／頭條的複本 → 1
    - [x] V-CI、`check_boundaries.py`
  - Dependencies：T3
  - Files：（搬）`report_data.py`；（改）`src/render_email.py`、`src/web.py`、`shared/pyproject.toml`、`selftest.yml`、`README.md`
  - Scope：M

- [x] **T5：`metrics` 搬進 `shared`**
  - Description：量測模組搬進 shared；路徑一律由 `paths.ROOT` 推算。
  - Acceptance：
    - [x] `src/metrics.py` → `shared/.../metrics.py`；`__main__` 的分派包成 `main()`；`[project.scripts] newsletter-metrics`
    - [x] `METRICS_DIR = ROOT/"logs"/"metrics"`、`TRANSCRIPTS` 由 `paths.ROOT` 推算；selftest 斷言兩者的來源是 `paths.ROOT`，且 `enabled()` 仍讀 `ROOT/"config"/"config.json"`
    - [x] importer 改：`src/fetch.py`、`src/run.py`、`src/render_email.py`、`src/agent_run.py`
    - [x] CI 與 skill（`metrics.py claude`、`summary`）、README 對應指令改成 `newsletter-metrics …`
  - Verification：
    - [x] V-CI；`NEWSLETTER_RUN_LABEL=test uv run --locked newsletter-metrics summary` 能執行（沒有紀錄也不報錯）
    - [x] `check_boundaries.py`；V-GREP；`git status` 確認 `logs/metrics/` 沒被碰（工作區沒有該目錄是正常的）
  - Dependencies：T4
  - Files：（搬）`metrics.py`；（改）`src/fetch.py`、`src/run.py`、`src/render_email.py`、`src/agent_run.py`、`shared/pyproject.toml`、`selftest.yml`、`SKILL.md`、`README.md`
  - Scope：M

- [x] **T6：`auth_store` 搬進 `shared`**
  - Acceptance：
    - [x] `src/auth_store.py` → `shared/.../auth_store.py`（借 `feedback.locked` 改為套件內 import）；importer 改：`src/agent_run.py`、`src/web.py`
    - [x] `token_path()` 仍是 `ROOT/state/oauth_token.json`；環境變數名稱（`CLAUDE_CODE_OAUTH_TOKEN` 等）不變
    - [x] CI 該行 → `uv run --locked python -m newsletter_shared.auth_store --selftest`
  - Verification：V-CI；`check_boundaries.py`；V-GREP；README 的 token 段落指令已更新
  - Dependencies：T5
  - Files：（搬）`auth_store.py`；（改）`src/agent_run.py`、`src/web.py`、`selftest.yml`、`README.md`
  - Scope：S

### ◆ Checkpoint A：shared 完成

- [x] `ls src/` 已沒有 `feedback`／`report_data`／`metrics`／`auth_store`
- [x] V-CI 全綠、`check_boundaries.py` 通過
- [x] V-GOLD（同 T4 的 `new` 覆寫）六項全部相同
- [x] V-DOCKER：建置成功；容器內 `python src/web.py --selftest` 通過（CMD 仍是舊路徑，靠 venv 的 python 能 import 新套件）
- [x] `git log --follow` 抽查兩個被搬的檔案追得到歷史
- [ ] **使用者審閱後才繼續**

## Phase 2：反轉

- [x] **T7：agent 不碰 email（唯一的行為變更）**
  - Description：照 [SPEC-agent.md](../SPEC-agent.md) 的反轉表修改。此時 `agent_run.py`、`render_email.py` 都還在 `src/`，所以 diff 只含行為變更，不夾雜搬檔。
  - Acceptance：
    - [x] `agent_run.py`：刪除 `render_email()` 與 `NEWSLETTER_DEBUG=0` 抑制手法；`verify()` 讀報告後呼叫 `parse_report`，`ValueError` → exit 4（`reason` 帶 ValueError 訊息，metrics 的 status 由 `render_failed` 改名為 `bad_format`）；stdout 為空（`--auth-check` 例外，仍印 JSON）；docstring 的 exit code 表更新（4＝報告格式不符），0–3、5、6 不變
    - [x] `agent_run` selftest：新增「格式不符 → verify 回傳原因／main exit 4」；移除 render 相關斷言
    - [x] `run_daily.sh`：四步 `fetch → agent → meta=$(render) → printf | send`；render 與 send 此時仍是 `uv run --locked src/render_email.py`、`src/send_email.py`；`exec >> "$LOG"` ＋ `ERR trap`、env 檔 600 檢查、`"$@"` 轉給 fetch 全部沿用
    - [x] skill：第 5 步改成「驗證報告格式」，指令 `uv run --locked newsletter-report-check`，失敗依訊息修正後重跑；`render_email`／email HTML 的敘述全部移除；「這份 Markdown 會被 `src/render_email.py` 轉成 email」改為「格式由 `newsletter_shared.report_data` 定義，email 與網頁都吃同一份」
    - [x] README：每日流程、exit code 表、「互動使用不再自動產出 html」的說明
  - Verification：
    - [x] `uv run --locked python src/agent_run.py --selftest`（本機有 SDK）通過
    - [x] `bash -n run_daily.sh`；用 scratchpad 的假 `uv`（記錄 argv、可指定某步 exit 非 0、render 那步印一行 JSON）把 `run_daily.sh` 複製到暫存目錄執行：步驟順序為 fetch→agent→render→send、`meta` 確實餵給 send、render 失敗時 send 不執行且 exit code 等於 render 的
    - [x] `grep -nE 'render_email|render_failed' src/agent_run.py .claude/skills/news-digest/SKILL.md` 無結果
    - [x] V-CI、`check_boundaries.py`
  - Dependencies：T4、T5、T6
  - Files：`src/agent_run.py`、`run_daily.sh`、`.claude/skills/news-digest/SKILL.md`、`README.md`
  - Scope：M（行為變更，單獨成 commit 以利審閱）
  - 不做：不跑 `newsletter-agent`（耗訂閱額度）、不真的寄信。

## Phase 3：agent 與 notify

- [x] **T8：agent 函式庫——`sources`、`fetch`、`curate` 進 `agent` 套件**
  - Description：建立 `agent` 套件，先搬三個不含 CLI 的函式庫；`run.py`（還在 `src/`）改 import 新套件。
  - Acceptance：
    - [x] 三檔 → `agent/src/newsletter_agent/`；`agent/pyproject.toml`（依賴 `newsletter-shared`，此時不含 SDK）；根 `members`／`dependencies` 加 `agent`；`uv.lock` 重生且 SDK 版本不變
    - [x] **`curate.STATE` 改為 `ROOT/"state"/"seen.json"`**（原本是 `Path(__file__).parent.parent`，搬後會指進套件目錄）；curate 的 selftest 斷言實際路徑
    - [x] `src/run.py` 的 `import curate as curate_mod` 等改 `from newsletter_agent import …`
    - [x] Dockerfile、`.dockerignore`、`.gitignore`、CI（`python -m newsletter_agent.curate`、`.fetch`）更新
  - Verification：
    - [x] V-CI；`check_boundaries.py`（agent 只 import shared）；V-GREP；V-GIT
    - [x] `uv run --locked src/run.py --list-sources` 與 `old.sources.txt` 相同
    - [x] V-DOCKER（建置成功、`docker run … python -c "import newsletter_agent.curate"`）
  - Dependencies：T7
  - Files：（搬）`sources.py`、`fetch.py`、`curate.py`；（改）`src/run.py`、`agent/pyproject.toml`、根 `pyproject.toml`、`uv.lock`、`Dockerfile`、`.dockerignore`、`.gitignore`、`selftest.yml`
  - Scope：M-L（搬 3 ＋配置；配置改動各只有幾行）

- [x] **T9：`run.py`、`report.py` 進 `agent` ＋ `newsletter-fetch`**
  - Acceptance：
    - [x] 兩檔 → `agent/.../`；`[project.scripts] newsletter-fetch = "newsletter_agent.run:main"`；`--config` 預設路徑改 `paths.ROOT/"config"/"config.json"`
    - [x] `run_daily.sh` 第 1 步、skill、CI（`--list-sources`）、README 使用說明 → `newsletter-fetch`
    - [x] `src/` 只剩 `agent_run.py`、`render_email.py`、`send_email.py`、`web.py`
  - Verification：
    - [x] V-GOLD（`FETCH="uv run --locked newsletter-fetch"`，其餘仍 `src/`）：`sources.txt` 相同
    - [x] 離線煙霧測試：`NEWSLETTER_RUN_LABEL=test uv run --locked newsletter-fetch --list-sources` exit 0
    - [x] V-CI、`check_boundaries.py`、V-GREP
  - Dependencies：T8
  - Files：（搬）`run.py`、`report.py`；（改）`agent/pyproject.toml`、`run_daily.sh`、`SKILL.md`、`selftest.yml`、`README.md`
  - Scope：M

- [x] **T10：`agent_run` 進 `agent` ＋ `newsletter-agent`；SDK 依賴移到 `agent`**
  - Acceptance：
    - [x] `src/agent_run.py` → `agent/.../agent_run.py`；`agent/pyproject.toml` 依賴 `claude-agent-sdk>=0.2.123`、`newsletter-shared`；根 `dependencies` 移除直接的 SDK 依賴；`[project.scripts] newsletter-agent`
    - [x] `cwd` 仍是 repo 根（`paths.ROOT`），`PROMPT` 不變
    - [x] `src/web.py` 的 token 驗證子程序改成 `[sys.executable, "-m", "newsletter_agent.agent_run", "--auth-check", "--token-from-env"]`（`cwd=ROOT`、以環境變數傳 token 不變）；web 的註解與 docstring 同步
    - [x] `run_daily.sh` 第 2 步、README「無人值守」段落更新
    - [x] `uv.lock` 的 `claude-agent-sdk` 版本不變
  - Verification：
    - [x] `uv run --locked python -m newsletter_agent.agent_run --selftest` 通過
    - [x] 無 token：`env -u CLAUDE_CODE_OAUTH_TOKEN uv run --locked newsletter-agent` → exit 2、訊息指向 `/auth` 或環境變數（不消耗額度）
    - [x] `uv run --locked python src/web.py --selftest` 通過
    - [x] `grep -rn newsletter_notify agent/` 無結果；V-CI、`check_boundaries.py`；V-DOCKER
  - Dependencies：T9
  - Files：（搬）`agent_run.py`；（改）`agent/pyproject.toml`、根 `pyproject.toml`、`uv.lock`、`src/web.py`、`run_daily.sh`、`README.md`
  - Scope：M

- [x] **T11：`render_email`、`send_email` 進 `notify`**
  - Acceptance：
    - [x] 兩檔 → `notify/src/newsletter_notify/`；`notify/pyproject.toml`（依賴 `newsletter-shared`）；`[project.scripts] newsletter-render`、`newsletter-send`；根 `members`／`dependencies`、`uv.lock`、Dockerfile、白名單更新
    - [x] `run_daily.sh` 第 3、4 步 → `newsletter-render`、`newsletter-send`；CI（`--selftest` 兩行）、README → 新指令
    - [x] 環境變數 `GMAIL_USER`／`GMAIL_APP_PASSWORD`／`NEWSLETTER_MAIL_TO`／`NEWSLETTER_BASE_URL` 名稱與語意不變；`newsletter-render` 的 stdout JSON 欄位不變
    - [x] `src/` 只剩 `web.py`
  - Verification：
    - [x] V-GOLD（`RENDER="uv run --locked newsletter-render"`，`WEB` 仍 `src/web.py`）：`render.json`、`render.html`、`render-base.html` 與 old 逐位元相同
    - [x] `uv run --locked newsletter-render 2026-10-04 | uv run --locked newsletter-send --dry-run` 印出信件標頭、不連線
    - [x] metrics：`NEWSLETTER_DEBUG=1 NEWSLETTER_RUN_LABEL=test` 跑 `newsletter-render` 後，`newsletter-metrics summary --all` 能看到 render 階段，並確認沒有把同一次執行切成兩列的現象（有就停下來問）
    - [x] `grep -rnE 'newsletter_(agent|web)' notify/` 無結果；V-CI、`check_boundaries.py`；V-DOCKER
  - Dependencies：T7（agent 已不呼叫 render）；T10（避免同時改 `run_daily.sh`、Dockerfile、根 `pyproject.toml`）
  - Files：（搬）`render_email.py`、`send_email.py`；（改）`notify/pyproject.toml`、根 `pyproject.toml`、`uv.lock`、`Dockerfile`、`.dockerignore`、`.gitignore`、`run_daily.sh`、`selftest.yml`、`README.md`
  - Scope：M-L（搬 2 ＋ 多個幾行的配置）

### ◆ Checkpoint B：核心流程完成

- [ ] `src/` 只剩 `web.py`；V-CI 全綠；`check_boundaries.py` 通過，**反向驗證一次**（暫時讓 notify import agent → 必須失敗，再還原）
- [ ] V-GOLD 六項全部相同（`RENDER`／`FETCH` 用最終指令，`WEB` 此時仍是 `uv run --locked src/web.py`）
- [ ] V-DOCKER：建置成功；`docker run … newsletter-render --selftest`、`newsletter-fetch --list-sources` 成功
- [ ] 假 `uv` 驗證 `run_daily.sh`：四步用的是最終指令名稱（`newsletter-fetch`／`newsletter-agent`／`newsletter-render`／`newsletter-send`）
- [ ] 實際抓取煙霧測試一次（`NEWSLETTER_RUN_LABEL=test`，工作區的 `data/`、`state/`；對真實 RSS 唯讀請求）：`data/curated/<date>.json` 的頂層欄位與 `items[]` 欄位與舊程式同一天的結果相同（只比結構，不比內容）；`state/seen.json` 寫在 `ROOT/state/`
- [ ] **使用者審閱後才繼續**

## Phase 4：web

- [ ] **T12：前端搬到 `web/ui/`**（先把 main 再合一次，降低前端衝突）
  - Description：整包 `web/` 內容搬進 `web/ui/`；後端（此時仍是 `src/web.py`）改讀 `web/ui/dist`。
  - Acceptance：
    - [ ] `git mv web/{index.html,package.json,package-lock.json,tsconfig.json,vite.config.ts,src} web/ui/`
    - [ ] `src/web.py`：`self.dist`、selftest 的假 dist、`BUILD_COMMAND`（`npm --prefix web/ui install && npm --prefix web/ui run build`）、503 頁說明與 docstring 全部指向 `web/ui`；`SPA_ROUTES`、`is_local_request`、CSP、API 路徑與欄位一律不動
    - [ ] Dockerfile node 階段改 `web/ui/…`，最終映像 `COPY --from=web /web/dist ./web/ui/dist`；`.dockerignore`、`.gitignore`（含 `/web/ui/node_modules/`、`/web/ui/dist/`）更新；README 的 Web 前端段落
    - [ ] 本機 `.claude/launch.json`（未追蹤）若需要也更新
  - Verification：
    - [ ] `npm --prefix web/ui ci && npm --prefix web/ui run typecheck && npm --prefix web/ui test && npm --prefix web/ui run build`
    - [ ] `uv run --locked python src/web.py --selftest`；建置後 `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:<port>/` 為 200
    - [ ] V-DOCKER（映像內有 `/app/web/ui/dist/index.html`）；V-CI、V-GIT
  - Dependencies：T11
  - Files：（搬）`web/` 前端整包；（改）`src/web.py`、`Dockerfile`、`.dockerignore`、`.gitignore`、`README.md`
  - Scope：M（一次目錄搬移 ＋ 5 個小改動）

- [ ] **T13：後端搬到 `web/server/` ＋ `newsletter-web`**
  - Acceptance：
    - [ ] `src/web.py` → `web/server/src/newsletter_web/web.py`（不拆）；`ROOT` 用 `paths.ROOT`；加 `main()`
    - [ ] `web/server/pyproject.toml`：依賴 `newsletter-shared`、`newsletter-agent`；`[project.scripts] newsletter-web`；根 `members`／`dependencies`、`uv.lock` 更新
    - [ ] Dockerfile 的 `CMD` 與 `docker-compose.yml` 的 `web.command` → `newsletter-web …`；healthcheck、服務名、volume 名、環境變數、埠號不變
    - [ ] `deploy/newsletter-web.service` 的 `ExecStart` → `uv run --locked newsletter-web --host 127.0.0.1 --port 8787`；README 註明已安裝的 unit 需重新複製並 `systemctl --user daemon-reload`
    - [ ] CI（`newsletter-web --selftest`）、README、本機 `.claude/launch.json` 更新；`src/` 目錄已空並刪除
  - Verification：
    - [ ] V-GOLD（全部用最終指令）：六項全部相同，含 `api.json`
    - [ ] 本機 `newsletter-web` 開 `/auth`，用**假 token** 觸發測試：回「無效」，證明子程序走的是 `python -m newsletter_agent.agent_run`（不耗額度）
    - [ ] V-DOCKER（`/api/reports` 回 200；`docker compose config` 通過）；`grep -rn newsletter_notify web/server` 無結果
    - [ ] V-CI、`check_boundaries.py`、V-GREP
  - Dependencies：T12
  - Files：（搬）`web.py`；（改）`web/server/pyproject.toml`、根 `pyproject.toml`、`uv.lock`、`Dockerfile`、`docker-compose.yml`、`deploy/newsletter-web.service`、`selftest.yml`、`README.md`、`.dockerignore`、`.gitignore`
  - Scope：M-L

### ◆ Checkpoint C：所有模組已搬

- [ ] `src/` 不存在；V-CI 全綠；`check_boundaries.py` 通過
- [ ] V-GOLD 六項全部相同
- [ ] V-DOCKER 全套；`npm --prefix web/ui` 的 typecheck／test／build
- [ ] `docker compose config` 通過（只驗證設定，**不 `up`**）
- [ ] **使用者審閱後才繼續**

## Phase 5：收斂

- [ ] **T14：根變成虛擬 workspace 根 ＋ 邊界檢查轉嚴格**
  - Acceptance：
    - [ ] 根 `pyproject.toml` 只剩 `[tool.uv.workspace] members = ["shared","agent","notify","web/server"]`（刪 `[project]`、`[tool.uv] package`、`[tool.uv.sources]`）；`uv.lock` 重生
    - [ ] `check_boundaries.py` 嚴格模式：四個成員目錄都必須存在、repo 根不得有 `src/`、成員 `pyproject.toml` 的 `dependencies` 與依賴表完全一致、`Path(__file__)` 只能在 `paths.py`
    - [ ] Dockerfile 的 `COPY pyproject.toml uv.lock .python-version` 仍成立
  - Verification：
    - [ ] 乾淨環境：`rm -rf .venv && uv sync --locked && uv lock --check`
    - [ ] `uv.lock` 的 `claude-agent-sdk` 版本不變；V-CI；V-DOCKER
    - [ ] 反向驗證兩次：① notify import agent ② 在 agent 加一行 `Path(__file__)` → 兩者都必須讓 `check_boundaries.py` 失敗，再還原
  - Dependencies：T13
  - Files：`pyproject.toml`、`uv.lock`、`deploy/check_boundaries.py`
  - Scope：S

- [ ] **T15：文件掃尾與 grep 閘門**
  - Description：README 的架構段落與全文指令改成 monorepo 現況；加一張「舊指令 → 新指令」對照表與主機端遷移注意事項。
  - Acceptance：
    - [ ] README 的「架構」改為新目錄樹與依賴圖；`src/` 相關指令（原 56 處）全部更新；新增「舊指令 → 新指令」對照表（`python3 src/run.py` → `uv run --locked newsletter-fetch` 等）
    - [ ] README 遷移注意：已安裝的 systemd unit 需重新複製並 `daemon-reload`；Docker 使用者重建映像即可（volume 與環境變數不變）；互動使用改跑 `newsletter-render`
    - [ ] `ISSUES.md` 只更新被搬動的路徑（2 處）；**不還原、不新增任何項目**
    - [ ] Dockerfile、compose、`deploy/` 範本的註解與現況一致
    - [ ] V-GREP 兩條全部為空：`Path(__file__)` 只在 `paths.py`；舊 `src/…py` 路徑在 README、skill、Dockerfile、compose、systemd 範本、CI、`run_daily.sh` 皆無
  - Verification：V-GREP；`grep -c 'src/' README.md` 只剩 `shared/src`、`agent/src` 等套件內路徑；通讀 README 一遍
  - Dependencies：T14
  - Files：`README.md`、`ISSUES.md`、`.claude/skills/news-digest/SKILL.md`、`Dockerfile`、`docker-compose.yml`、`deploy/*.service`（註解）
  - Scope：M

- [ ] **T16：最終驗證與交接**
  - Description：依序驗 [SPEC.md](../SPEC.md) 的 8 條成功條件，整理證據；不 push、不開 PR、不重建正在跑的 stack，除非使用者另外同意。
  - Acceptance：
    - [ ] SPEC 成功條件 1–8 逐條有證據（指令與輸出摘要）；條件 7 的「`docker compose up -d` 後 web healthy、volume 資料在、對外網址可開」改為**列為使用者合併後要做的步驟**，本任務只驗到 `migration-test` 映像
    - [ ] 條件 8：`logs/metrics/` 檔案數在重構前後一致（用 `git` 以外的方式比對：這個目錄不進版控，工作區沒有就記為「未涉及」）
    - [ ] 完成定義清單逐項勾完
    - [ ] 交接摘要：變更範圍、行為變更只有一項、使用者要做的主機端步驟、已知限制
  - Verification：V-CI、V-GOLD、V-DOCKER 全套；`git log --oneline main..HEAD` 每個 commit 對應一個任務；`git diff --stat main..HEAD | tail -1`
  - Dependencies：T15
  - Files：無（只驗證）
  - Scope：S
  - 之後（需使用者說）：push、開 PR（描述附 SPEC 連結與上面的交接摘要）、合併後 `docker compose build && up -d`。

### ◆ Checkpoint 完成

- [ ] 所有 SPEC 成功條件達成
- [ ] 使用者核可後才 push／開 PR
