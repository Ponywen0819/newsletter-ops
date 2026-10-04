# Spec: deploy

屬於 [SPEC.md](SPEC.md) 的 Capability Map。依賴：agent、notify、web（執行期）。**最後動工**，但每個模組搬完時，它自己那部分的路徑要同一個 commit 一起更新（否則 CI 或映像會壞）。

## Objective

組裝與排程，不放業務邏輯。重構後**正在跑的 Docker stack 要能原地重建**（volume、容器內路徑、環境變數、對外網址都不變）。

## 內容與改動

| 檔案 | 位置 | 改動 |
|---|---|---|
| `pyproject.toml` | 根 | 改成虛擬 workspace 根：整檔只剩 `[tool.uv.workspace] members = ["shared","agent","notify","web/server"]`（沒有 `[project]`，`claude-agent-sdk` 依賴移到 `agent/pyproject.toml`） |
| `uv.lock` | 根 | `uv lock` 重生；`claude-agent-sdk` 版本不得變 |
| `run_daily.sh` | 根（不搬） | 四步，見下 |
| `Dockerfile` | 根（不搬） | 見下 |
| `docker-compose.yml` | 根（不搬） | `web` 的 command 改 `["newsletter-web","--host","0.0.0.0","--port","8787"]`；healthcheck、服務名、volume 名、環境變數名不變 |
| `docker/scheduler.sh`、`docker/entrypoint.sh` | 根 | 通常不用改（仍呼叫 `./run_daily.sh`） |
| `.dockerignore`、`.gitignore` | 根 | 兩個都是白名單：新增 `shared/ agent/ notify/ web/server/ web/ui/` 的放行規則（只放 `pyproject.toml`、`src/**/*.py`、前端原始碼與鎖檔），並確認 `git status` 沒有漏檔 |
| `deploy/newsletter-web.service`、`newsletter-daily.service` | `deploy/` | `ExecStart` 改成 `uv run --locked newsletter-web …`；README 註明已安裝的 unit 需重新複製並 `systemctl --user daemon-reload` |
| `.github/workflows/selftest.yml` | `.github/` | 安裝改 `astral-sh/setup-uv`＋`uv sync --locked`；十行檢查換成 SPEC.md Commands 的版本；加 `python3 deploy/check_boundaries.py` |
| `deploy/check_boundaries.py` | **新增** | 見下 |

### `run_daily.sh` 的新流程

```bash
uv run --locked newsletter-fetch --no-report "$@"                # 1 抓取
uv run --locked newsletter-agent                                 # 2 agent 寫報告並驗收（stdout 空）
meta=$(uv run --locked newsletter-render)                        # 3 notify：render，收下那行 JSON
printf '%s\n' "$meta" | uv run --locked newsletter-send          # 4 notify：send
```

沿用現有寫法：`exec >> "$LOG"` ＋ `ERR trap` 讓 `set -e` 生效，render 的輸出先收進變數再交給 send，不用 `render | send`（pipefail 下 render 失敗會被 send 的 traceback 蓋掉 exit code）。env 檔 600 權限檢查、log 檔名、`"$@"` 轉給 fetch 都不變。第 2 步原本的 `meta=$(… agent_run)` 取消，第 3 步失敗的 exit code 就是 `newsletter-render` 的 exit code。

### `Dockerfile`

- node 階段：路徑改 `web/ui/`（`COPY web/ui/package.json web/ui/package-lock.json`、`COPY web/ui/index.html …`、`COPY web/ui/src`），最終映像 `COPY --from=web /web/dist ./web/ui/dist`。
- python 階段：先 `COPY pyproject.toml uv.lock .python-version` 與各成員的 `pyproject.toml`，跑 `uv sync --locked --no-install-workspace`（依賴快取層）；再 `COPY` 各成員 `src`，跑 `uv sync --locked`（成員 editable 安裝，`paths.ROOT` 因此是 `/app`）。
- 不變：`/var/lib/newsletter/{reports,data,state,logs}`、`/app/{reports,data,state,logs}` 符號連結、`ENV PATH=/app/.venv/bin:…`、非 root 使用者、`VOLUME`、`ENTRYPOINT`。

### `deploy/check_boundaries.py`

標準庫＋`ast`，約 30 行。內建依賴表（shared→∅、agent→{shared}、notify→{shared}、web→{shared,agent}），掃每個成員 `src/**/*.py` 的 `import`／`from … import`，凡 `newsletter_*` 不在該成員的允許集合內就列出並 exit 1。同時檢查：每個成員的 `pyproject.toml` `dependencies` 與這張表一致。

## 要特別小心

- 這個 repo 的 `.gitignore`、`.dockerignore` 都是白名單，漏放行的檔案**不會出錯，只是默默不進版控／不進映像**。每搬完一個模組就跑 `git status --short` 與 `docker build`。
- Dockerfile 原本釘 uv 0.8.17、本機是 0.11.13；已決議升到 0.11.13（Dockerfile、CI、`uv_build>=0.11.13,<0.12` 一致）。`ghcr.io/astral-sh/uv:0.11.13` 這個 tag 是否存在，在計畫 T1 用 `docker build` 驗證。
- `docker compose up -d` 會重建正在服務的容器；只在使用者同意後執行，且不用 `down -v`。

## Success Criteria

1. `docker build .` 成功；`docker compose config` 通過。
2. 在同意重建後：`web` healthy、`GET /api/reports` 回 200、volume 內既有報告與 `state/feedback.jsonl` 都在、`https://newsletter.ponygames.net` 可開。
3. 容器內 `docker compose run --rm scheduler newsletter-fetch --list-sources` 成功（不連網、不耗額度）。
4. CI 在 push 上全綠；`check_boundaries.py` 對違規 import 會失敗（手動驗證一次）。
5. `git status --short` 沒有該被追蹤卻被 `.gitignore` 擋掉的檔案（`git ls-files | wc -l` 與預期一致：搬移是 rename，加上五個 `pyproject.toml`、`paths.py`、`check_boundaries.py`、`SPEC*.md`）。

## Boundaries

- Always：改路徑時同一個 commit 更新 README 對應段落。
- Ask first：升 uv／Python 版本釘選；CI 加 job；重建正在跑的容器；動 `deploy/` 範本裡的對外網址或 Tunnel 設定。
- Never：`docker compose down -v`；刪 volume；把 `.env`、token 放進映像或版控。
