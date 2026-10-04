# Spec: newsletter-ops 改成 monorepo

> 狀態：**已實作**於分支 `claude/monorepo-restructure-ab5b9d`（2026-10-04 核可、同日完成），待 push 與合併。實作順序與任務見 [tasks/plan.md](tasks/plan.md)、[tasks/todo.md](tasks/todo.md)。
> 各模組的細節在 `SPEC-<module id>.md`；本檔是索引，也是整個重構共用的規則。

## Capability Map

| Module id | 職責 | 現有檔案 | 依賴（import） | Spec |
|---|---|---|---|---|
| `shared` | 跨模組底層：repo 根路徑、報告格式解析與驗證、回饋 mark 與檔案鎖、metrics、OAuth token 儲存 | feedback, report_data, metrics, auth_store | — | [SPEC-shared.md](SPEC-shared.md) |
| `agent` | 抓取 → 整理 → Claude 寫報告（核心）。**不認得 email** | sources, fetch, curate, run, report, agent_run；skill | shared | [SPEC-agent.md](SPEC-agent.md) |
| `notify` | 報告 → email HTML → 寄出（外圍；日後別的通知管道也放這） | render_email, send_email | shared | [SPEC-notify.md](SPEC-notify.md) |
| `web` | JSON API（`server/`）＋ React 前端（`ui/`） | web.py、原 `web/` | shared, agent（只為了 `--auth-check`） | [SPEC-web.md](SPEC-web.md) |
| `deploy` | 組裝，只編排不寫業務邏輯：Docker、compose、systemd、`run_daily.sh`、CI、邊界檢查 | 同左 | agent, notify, web（執行期） | [SPEC-deploy.md](SPEC-deploy.md) |

```
shared ◄── agent ◄── web
   ▲
   └────── notify
```

**Build order：** shared → agent ∥ notify → web → deploy

**已定案的決定（使用者 2026-10-04 選的）：**

1. **agent 不碰 email。** `agent_run` 只產出並驗收 `reports/<date>.md`；render 與 send 改由 `run_daily.sh` 依序呼叫 notify。這是本次唯一的行為變更。
2. **uv workspace，真套件。** 每個模組一個 `pyproject.toml`，用 `from newsletter_shared import …`，不再用 `sys.path.insert`。
3. **web 一個模組**：`web/server`（Python）＋ `web/ui`（React，原 `web/` 整包搬進去）。

## Objective

把「根目錄全是 Python、`web/` 是 React」的 repo 改成 monorepo，依責任拆成 `shared / agent / notify / web / deploy`，讓**核心（agent）與外圍（email 通知）的依賴方向由工具強制，而不只是資料夾名稱**。使用者是 repo 的唯一維護者；成功的樣子是：之後加一個新的通知管道（例如 Line）只需新增 `notify` 內的程式，不必碰 `agent`。

純重構。除上面第 1 點之外，**行為、輸出、環境變數名稱、volume 路徑、對外 URL 一律不變**。

## Tech Stack

- Python 3.11（`.python-version`）；成員 `requires-python = ">=3.10"`（與現在相同）
- uv workspace；build backend `uv_build`（成員用 src layout）
- 第三方依賴只有 `claude-agent-sdk>=0.2.123`，只掛在 `agent`；其餘 Python 模組維持純標準庫
- 前端：Vite + React + TypeScript + Vitest（版本不動）
- 已在暫存目錄用 uv 0.11.13 做原型驗證：虛擬 workspace 根（無 `[project]`）下，`uv sync --locked` 與 `uv run --locked <script>` 會把所有成員以 editable 裝好；`Path(__file__).parents[3]` 能正確指到 repo 根；`uv sync --locked --no-install-workspace` 只裝第三方依賴（給 Docker 快取層用）。Docker 映像與 CI 的 uv 釘選一律 0.11.13（與本機一致），成員的 build-system 寫 `uv_build>=0.11.13,<0.12`。

## Commands

```bash
# 環境
uv sync --locked                                  # 裝好全部成員（editable）與 claude-agent-sdk
uv lock --check                                   # 鎖檔是否與各 pyproject 一致

# 每日流程（run_daily.sh 做的事，逐步執行）
uv run --locked newsletter-fetch --no-report      # agent：抓取 → data/curated/<date>.json
uv run --locked newsletter-agent                  # agent：Claude 寫 reports/<date>.md 並驗收
uv run --locked newsletter-render                 # notify：reports/<date>.md → .html，stdout 一行 JSON
uv run --locked newsletter-render | uv run --locked newsletter-send [--dry-run]

# 其他 CLI（原本的 python3 src/*.py）
uv run --locked newsletter-feedback               # shared：收集報告裡的人工標記
uv run --locked newsletter-metrics summary        # shared：metrics 報表
uv run --locked newsletter-report-check [YYYY-MM-DD]   # shared：驗證報告格式（新增；取代 render 給 agent 的驗證）
uv run --locked newsletter-web --port 8787        # web：JSON API ＋ 提供 web/ui/dist

# 自我檢查（原 CI 那十行，路徑換成 -m）
uv run --locked python -m newsletter_agent.curate
uv run --locked python -m newsletter_agent.fetch
uv run --locked newsletter-metrics --selftest
uv run --locked newsletter-render --selftest
uv run --locked newsletter-send --selftest
uv run --locked newsletter-feedback --selftest
uv run --locked python -m newsletter_shared.auth_store --selftest
uv run --locked python -m newsletter_shared.report_data --selftest
uv run --locked newsletter-web --selftest
uv run --locked newsletter-fetch --list-sources
python3 deploy/check_boundaries.py                # 新增：依賴方向檢查

# 前端
npm --prefix web/ui ci && npm --prefix web/ui run build   # 型別檢查 + 建置到 web/ui/dist
npm --prefix web/ui test
npm --prefix web/ui run typecheck

# 容器
docker compose build && docker compose up -d
```

`agent_run` 的 selftest（`python -m newsletter_agent.agent_run --selftest`）需要 SDK，維持原本「CI 不跑」。

## Project Structure

```
newsletter-ops/
├── pyproject.toml              虛擬 workspace 根：只有 [tool.uv.workspace]，沒有 [project]
├── uv.lock                     整個 workspace 一份
├── .python-version
├── shared/                     newsletter-shared
│   ├── pyproject.toml
│   └── src/newsletter_shared/  paths.py feedback.py report_data.py metrics.py auth_store.py
├── agent/                      newsletter-agent（依賴 claude-agent-sdk、shared）
│   ├── pyproject.toml
│   └── src/newsletter_agent/   sources.py fetch.py curate.py run.py report.py agent_run.py
├── notify/                     newsletter-notify（依賴 shared）
│   ├── pyproject.toml
│   └── src/newsletter_notify/  render_email.py send_email.py
├── web/
│   ├── server/                 newsletter-web（依賴 shared、agent）
│   │   ├── pyproject.toml
│   │   └── src/newsletter_web/ web.py
│   └── ui/                     Vite + React（原 web/ 內容整包搬入）
├── config/                     不動。agent 的設定，shared.metrics 也讀它的 debug 旗標
├── .claude/skills/news-digest/ 不動位置（Claude 專案根）；內容隨指令更新
├── run_daily.sh                不動位置；內容改成 fetch → agent → render → send 四步
├── Dockerfile, docker-compose.yml, .dockerignore, docker/    不動位置；內容改路徑
├── deploy/                     systemd／cloudflared 範本；新增 check_boundaries.py
├── .github/workflows/selftest.yml
├── reports/ data/ state/ logs/ 執行期資料：不進版控、不搬動
└── README.md  ISSUES.md  SPEC*.md
```

**為什麼 `config/` 和 `.claude/` 留在根目錄：** Claude Agent SDK 以 `cwd=ROOT` 找專案 skill；`metrics.py` 用 cwd 路徑推算 `~/.claude/projects/<路徑>` 的 session 紀錄位置。cwd 不變，這兩件事才不會悄悄壞掉。

## Code Style

沿用現有風格：繁體中文註解與 docstring、`from __future__ import annotations`、型別標註、模組開頭 docstring 寫用法、模組內自帶 `selftest()`（`assert` 為主，不引入 pytest）。唯一的風格變更是 import：

```python
# newsletter_notify/render_email.py（搬家後）
from __future__ import annotations

from newsletter_shared import metrics
from newsletter_shared.paths import ROOT              # repo 根只從這裡拿
from newsletter_shared.report_data import parse_report
```

搬家前是 `sys.path.insert(0, str(ROOT / "src"))` ＋ `import metrics`。規則：

- **`Path(__file__)` 只准出現在 `shared/.../paths.py`。** 搬到 `src/<pkg>/` 之後，所有 `parent.parent` 都會悄悄指到錯的地方（`curate.py` 的 `STATE` 就是這樣）。
- 同一個套件內互相引用用 `from newsletter_agent import curate`。
- 套件名：發行名 `newsletter-<id>`，import 名 `newsletter_<id>`，console script 一律 `newsletter-*`。
- 一個檔案只做搬移與 import 改寫，**不順手重構**（`web.py` 921 行也不拆）。

## Testing Strategy

- **層級：** 沿用「模組自帶 selftest」＋ 前端 Vitest。不新增測試框架。
- **新增兩個檢查：**
  1. `deploy/check_boundaries.py`（標準庫、用 `ast`）：掃每個模組的 `import newsletter_*`，與上面的依賴表比對，多出任何一條就失敗。這是 uv workspace 值得用的原因——所有成員裝在同一個 venv，沒有它的話違規 import 不會有人發現。
  2. `newsletter-report-check`／`report_data` 的 selftest：驗證「缺頭條」「缺 subject」都會失敗（agent 靠它自我修正報告）。
- **行為不變的對照（搬家前後）：** 用同一份 `reports/<date>.md`，比較 ① `parse_report` 輸出 ② `newsletter-render` 產出的 HTML ③ web 的 `/api/reports/<date>` JSON，三者必須逐位元相同。基準在動手前先用 main 上的舊程式產生並存在 scratchpad。
- **CI：** 同一組檢查，只換指令與路徑；安裝步驟改為 `astral-sh/setup-uv` ＋ `uv sync --locked`。
- 任何會跑 `fetch`／`render`／`metrics` 的驗證都要加 `NEWSLETTER_RUN_LABEL=test`。

## Boundaries

- **Always**
  - 用 `git mv` 搬檔，保留歷史；一個模組一個 commit，每個 commit 自己 CI 全綠。
  - 文件（README、skill、systemd 範本、Dockerfile 註解）跟程式碼在同一個 commit 更新。
  - 驗證用的任何執行都帶 `NEWSLETTER_RUN_LABEL=test`；寄信一律 `--dry-run`。
  - `ROOT` 只從 `newsletter_shared.paths` 取。
- **Ask first**
  - 新增任何第三方依賴；`uv.lock` 裡 `claude-agent-sdk` 的解析版本有變。
  - CI 新增 job 或改觸發條件（只換路徑不算）；Dockerfile 的 uv／Python 版本釘選。
  - 改環境變數名稱、volume 名稱、容器內路徑、埠號、exit code 的意義（agent 的 4 除外，見「決議」2）。
  - 搬動 `config/`、`.claude/`、`run_daily.sh`；改動 skill 第 5 步與格式指引以外的內容。
  - 動到正在跑的 Docker stack（`docker compose up -d` 會重建容器）。
- **Never**
  - 刪除、搬動、覆寫 `reports/ data/ state/ logs/`（含 `logs/metrics/`）或 Docker volume `newsletter-data`（不用 `down -v`）。
  - 在驗證時實際寄信，或在沒被要求時跑 `newsletter-agent`（會消耗訂閱額度）。
  - 為了讓測試過而刪掉或放寬失敗的 selftest。
  - commit `.env`、token；`git push --force`；改寫 main 的歷史。
  - 還原 ISSUES.md 裡被使用者刪掉的項目（只更新被搬動的路徑）。

## Success Criteria

1. 乾淨 clone 後 `uv sync --locked`、`uv lock --check` 成功；`uv.lock` 的 diff 只有成員套件的增減，`claude-agent-sdk` 版本不變。
2. 上面 Commands 的全部自我檢查、`npm test`、`npm run typecheck`、`npm run build` 通過。
3. `python3 deploy/check_boundaries.py` 通過；**手動讓 notify import agent 一次，確認它會失敗**，再還原。
4. `grep -rnE 'Path\(__file__\)' shared agent notify web/server` 只剩 `paths.py` 一處；`grep -rnE 'src/(run|agent_run|render_email|send_email|web|feedback|metrics)\.py'` 在 README、skill、Dockerfile、compose、systemd 範本、CI、`run_daily.sh` 都沒有結果。
5. 行為不變的對照三項（見 Testing Strategy）逐位元相同。
6. `agent` 套件完全不 import `newsletter_notify`；`newsletter-agent` stdout 為空（`--auth-check` 例外，仍印 JSON）；exit code 0–3、5、6 意義不變。
7. `docker build` 成功；`docker compose up -d` 後 `web` healthy、`GET /api/reports` 回 200、既有 volume 內的報告與 `state/feedback.jsonl` 還在、`https://newsletter.ponygames.net` 仍可開。
8. `logs/metrics/` 檔案數在重構前後一致。

## 決議（2026-10-04）

1. **前置條件已滿足：** `refactor/email-from-report-data` 已以 PR #18 合進 main，`render_email → report_data` 的依賴方向成立。這條分支落後 main 11 個 commit，由計畫的 T0 手動合併。
2. **exit code 4 保留**，意義改成「報告格式不符（`parse_report` 拋 ValueError）」，log 與 README 的讀法不變。
3. **uv 釘選升到 0.11.13**（Dockerfile、CI、`uv_build` 範圍一致）。
4. **CI 不多跑** `agent_run` selftest 與 `npm test`，維持現狀範圍。
5. **互動使用的差異接受：** 反轉後，在 Claude Code 手動跑 skill 不會再自動產出 `reports/<date>.html`，改跑 `newsletter-render`；README 寫明。
