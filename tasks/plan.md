# Implementation Plan: newsletter-ops 改成 monorepo

規格：[SPEC.md](../SPEC.md) 與 `SPEC-<module>.md`（2026-10-04 核可）。任務清單與每項的驗收／驗證在 [todo.md](todo.md)；本檔是決策、順序與風險，任務在這裡只列索引，避免兩份真相。

## Overview

把「根目錄全是 Python、`web/` 是 React」拆成 uv workspace：`shared / agent / notify / web / deploy`。純重構，唯一的行為變更是「agent 不再呼叫 email」（SPEC 決定 1）。每個任務結束時**整套自我檢查必須是綠的**，所以搬移不是一次到位，而是讓「根目錄還是普通專案、成員一個個加入」，最後才把根變成虛擬 workspace 根。

## 已核可的決議（SPEC 的 Open Questions 全數結案）

1. 前置條件已滿足：`refactor/email-from-report-data` 已以 PR #18 合進 main（`render_email → report_data` 方向確認，`web.py` 不再 import `render_email`）。本分支落後 main 11 個 commit，T0 手動合併（本 session 沒有 `sync_with_base_branch` 工具）。
2. exit code 4 保留，意義改成「報告格式不符」。
3. Dockerfile 的 uv 釘選升到 **0.11.13**（與本機一致）；成員 `[build-system] requires = ["uv_build>=0.11.13,<0.12"]`；CI 的 `setup-uv` 也釘 0.11.13。
4. CI 不多跑 `agent_run` selftest 與 `npm test`（維持現狀範圍）。
5. 互動使用不再自動產出 `reports/<date>.html`，README 寫明改跑 `newsletter-render`。

## Architecture Decisions

| 決定 | 理由 |
|---|---|
| 根 `pyproject.toml` 搬移期間保留 `[project]`（含 `claude-agent-sdk`、`package = false`），成員逐一加進 `[tool.uv.workspace]` 與根的 `dependencies`；T14 才改成虛擬根 | 已在暫存目錄驗證：中間狀態下 `uv run --locked src/x.py` 能 import 新成員；最終虛擬根也能 `uv sync`／`uv run`。避免一次大爆炸 |
| **CI 與 skill 在 T2 就改用 `uv run`**，早於第一個模組搬移（T3） | 一旦 `src/*.py` import `newsletter_shared`，裸 `python3 src/x.py` 就找不到套件。已驗證會 `ModuleNotFoundError` |
| 反轉（T7）排在 shared 搬完之後、agent／notify 搬移之前 | 反轉需要 `newsletter-report-check`（T4）；且趁 `render_email.py` 還在 `src/`、`agent_run` 還在原處時改，diff 只剩行為變更，不夾雜搬檔 |
| 每個模組的搬移都帶著「所有 importer 的改寫」同一個 commit | 否則中間 commit 紅燈 |
| `config/`、`.claude/skills`、`run_daily.sh`、Dockerfile、compose 不搬位置 | SPEC 已定：cwd 與 `metrics` 的 session 路徑不變；已安裝的 unit／compose 路徑不變 |
| agent 與 notify 技術上可並行，但**不並行** | 兩者都改根 `pyproject.toml`、`uv.lock`、Dockerfile、CI、`run_daily.sh`，單一分支依序做比解衝突省事 |
| 驗證用 Docker 映像標籤一律 `newsletter-ops:migration-test`，**不用 `docker compose build`** | compose 會重標 `newsletter-ops:local`；正在跑的 stack 不能被動到 |

## 依賴圖與順序

```
T0 準備 ─► T1 骨架 ─► T2 工具鏈(CI/skill/邊界檢查)
                          │
        ┌─────────────────┘   shared 內部：feedback → report_data → metrics → auth_store
        ▼
   T3 feedback ─► T4 report_data(+report-check) ─► T5 metrics ─► T6 auth_store ─► ◆CP-A
                                                                                  │
                                                          T7 反轉（agent 不碰 email）
                                                                                  │
                          T8 agent 函式庫 ─► T9 run+report ─► T10 agent_run ─► T11 notify ─► ◆CP-B
                                                                                               │
                                                                    T12 web/ui ─► T13 web/server ─► ◆CP-C
                                                                                                       │
                                                                       T14 根變虛擬 ─► T15 文件掃尾 ─► T16 最終驗證
```

## Task List（索引；細節見 todo.md）

**Phase 0 準備** — T0
**Phase 1 shared** — T1 骨架、T2 工具鏈、T3 feedback、T4 report_data、T5 metrics、T6 auth_store → **Checkpoint A**
**Phase 2 反轉** — T7
**Phase 3 agent 與 notify** — T8 agent 函式庫、T9 run+report、T10 agent_run、T11 notify → **Checkpoint B**
**Phase 4 web** — T12 前端搬家、T13 後端搬家 → **Checkpoint C**
**Phase 5 收斂** — T14 根變虛擬、T15 文件掃尾、T16 最終驗證與交接

## 標準搬移清單（M1–M8）

todo.md 的模組搬移任務只寫「特例」，其餘照這張做：

1. **M1** `git mv`（保留歷史）；一個檔案同時只做搬移與 import 改寫，不順手重構。
2. **M2** 檔內：`sys.path.insert(...)` 刪除；`ROOT`／`Path(__file__)…` 改 `from newsletter_shared.paths import ROOT`；跨模組 import 改 `newsletter_*`；同套件內用 `from newsletter_x import y`。
3. **M3** 該模組的 `pyproject.toml`（`[build-system]` uv_build、`dependencies`、`[project.scripts]`）；CLI 要有 `main()`。
4. **M4** 根 `pyproject.toml`：workspace `members` 與 `dependencies`＋`[tool.uv.sources] … = { workspace = true }`；`uv lock`；確認 `claude-agent-sdk` 版本不變。
5. **M5** 白名單：`.gitignore`、`.dockerignore` 放行新目錄；Dockerfile 的 `COPY`；做完跑 `git status --short`，確認沒有被默默擋掉的檔案。
6. **M6** CI 的 selftest 行換成新指令；`run_daily.sh`、skill、README 裡該模組的指令與路徑同一個 commit 更新。
7. **M7** 驗證：V-CI ＋ `check_boundaries.py` ＋ 該任務指定的 golden／Docker 檢查。
8. **M8** 完成定義（見下）。

## Verification Toolkit

**V-CI（本機跑 CI 全套）**——CI 檔是唯一真相，每個任務同 commit 更新它：

```bash
grep -E '^[[:space:]]+- run: ' .github/workflows/selftest.yml | sed -E 's/^[[:space:]]+- run: //' \
  | while IFS= read -r c; do echo "+ $c"; sh -c "$c" || { echo "FAIL: $c"; exit 1; }; done
```

CI 實際綠燈只能 push 後在 GitHub 上看到；push 由使用者決定。

**V-GOLD（行為不變的對照）**——基準是 T0 記錄的 `BASE_SHA`（main）用 `git worktree add "$BASE" <BASE_SHA>` 取出的舊程式；fixture 是主 checkout 的 `reports/2026-10-04.md`（唯讀複製進各樹的 `reports/`，該目錄不進版控）。`golden.sh` 放 scratchpad、不進 repo；換 session 就依此重建，基準檔也是用舊程式重新產生：

```bash
#!/usr/bin/env bash
# golden.sh old|new —— 在目前目錄執行；old＝基準 worktree，new＝重構中的樹；輸出到 $G/<mode>.*
set -euo pipefail
mode=$1; G=${G:?先 export G=<輸出目錄>}; FIX=${FIX:-2026-10-04}; PORT=$([ "$mode" = old ] && echo 8791 || echo 8792)
mkdir -p "$G" reports && cp "/Users/pony/project/newssletter-ops/reports/$FIX.md" reports/
if [ "$mode" = old ]; then
  PY="python3"; PARSE='import sys; sys.path.insert(0, "src"); import report_data as r'
  RENDER="python3 src/render_email.py"; WEB="python3 src/web.py"; FETCH="python3 src/run.py"
else  # 各任務依當時實際狀態覆寫 RENDER／WEB／FETCH；預設是最終形態
  PY="uv run --locked python"; PARSE='from newsletter_shared import report_data as r'
  RENDER=${RENDER:-"uv run --locked newsletter-render"}; WEB=${WEB:-"uv run --locked newsletter-web"}
  FETCH=${FETCH:-"uv run --locked newsletter-fetch"}
fi
$PY -c "$PARSE
import json; print(json.dumps(r.parse_report(open('reports/$FIX.md', encoding='utf-8').read()), sort_keys=True, ensure_ascii=False))" > "$G/$mode.parse.json"
export NEWSLETTER_RUN_LABEL=test
$RENDER $FIX | sed "s#$PWD#<ROOT>#g" > "$G/$mode.render.json";  cp reports/$FIX.html "$G/$mode.render.html"
NEWSLETTER_BASE_URL=https://example.test $RENDER $FIX >/dev/null; cp reports/$FIX.html "$G/$mode.render-base.html"
$FETCH --list-sources > "$G/$mode.sources.txt"        # 離線；若輸出含絕對路徑同樣用 sed 正規化
$WEB --port $PORT & pid=$!; trap 'kill $pid 2>/dev/null' EXIT
for _ in $(seq 20); do curl -fsS "http://127.0.0.1:$PORT/api/reports" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS "http://127.0.0.1:$PORT/api/reports/$FIX" > "$G/$mode.api.json"
```

比對：`for f in parse.json render.json render.html render-base.html sources.txt api.json; do diff "$G/old.$f" "$G/new.$f" && echo "same $f"; done`，全部無差異才算過。

**V-DOCKER（不碰正在跑的 stack）**：

```bash
docker build -t newsletter-ops:migration-test .
docker run --rm newsletter-ops:migration-test newsletter-fetch --list-sources      # 模組都在映像裡
docker run --rm -d --name nl-mig-web -p 127.0.0.1:8799:8787 newsletter-ops:migration-test \
  newsletter-web --host 0.0.0.0 --port 8787                                          # 8799 避開本機 override 的 8787
curl -fsS http://127.0.0.1:8799/api/reports; docker rm -f nl-mig-web
```

（指令名稱依任務當時的狀態換成當時存在的形式，例如 T13 之前 web 的指令是 `python src/web.py …`。）

**V-GREP（SPEC 成功條件 4）**：`Path(__file__)` 只准在 `paths.py`；舊路徑 `src/(run|agent_run|render_email|send_email|web|feedback|metrics)\.py` 在 README、skill、Dockerfile、compose、systemd 範本、CI、`run_daily.sh` 皆無。

**V-GIT**：每個任務結束 `git status --short`（沒有該追蹤卻不見的檔案）、`git ls-files | wc -l` 與預期的 rename 數一致。

## 完成定義（每個任務都要過；引用 agent-skills 的 Definition of Done）

- 驗收條件全滿足，且**在 runtime 驗證過**，不只是 import 得過。
- 既有 selftest／vitest 全綠，沒有為了過關而刪除或放寬。
- 沒有殘留：`sys.path.insert`、`parent.parent`、被取代的舊指令、註解掉的區塊、debug 輸出。
- 範圍只含該任務；沒有順手重構。
- 文件（README、skill、範本、註解）在同一個 commit 更新，用「現在式」描述，不寫「已從…搬到」這類變更史。
- 驗證用的執行都帶 `NEWSLETTER_RUN_LABEL=test`、寄信只用 `--dry-run`、不碰 `logs/metrics/` 與使用者的 `reports/ data/ state/ logs/`。
- Checkpoint 處等使用者審閱才繼續。

## Risks and Mitigations

| 風險 | 影響 | 對策 |
|---|---|---|
| Docker 內 uv 0.11.13 ＋ `uv_build` ＋ workspace 的組合第一次出現在 T1 | 高 | T1 就做 `docker build`（fail fast）；若 `ghcr.io/astral-sh/uv:0.11.13` 不存在，改用最接近的 tag 並同步本機與 CI |
| 搬到 `src/<pkg>/` 後，沒改的 `Path(__file__).parent.parent` 悄悄指到錯處（`curate.STATE` 會讓 `state/seen.json` 寫進套件目錄，隔天整批新聞重收） | 高 | 每個任務跑 V-GREP；`paths` 與 `curate` 的 selftest 斷言實際路徑 |
| 白名單 `.gitignore`／`.dockerignore` 讓漏放行的檔案「不出錯、只是不見」 | 高 | M5 ＋ V-GIT；V-DOCKER 的「模組在映像裡」檢查 |
| 中間 commit 的 README 對尚未搬的模組仍寫 `python3 src/…`（裸 `python3` 在 T3 之後跑不起來） | 低（只影響中間 commit） | CI／skill 在 T2 先換成 `uv run`；T15 的 V-GREP 保證最終收斂 |
| 反轉後 `render` 的 metrics 由 notify 的獨立程序記錄，`metrics summary` 可能把一次執行切成兩列 | 中 | T11 用 `NEWSLETTER_DEBUG=1 NEWSLETTER_RUN_LABEL=test` 實測；有問題停下來問 |
| 單一 venv 讓違規 import 不會報錯 | 中 | T2 起 `check_boundaries.py` 進 CI；T14 轉嚴格模式，並各做一次反向驗證（故意違規確認會失敗） |
| `paths.ROOT = parents[3]` 在非 editable 安裝下會錯 | 中 | `paths` selftest 斷言 `ROOT/"uv.lock"` 存在；Dockerfile 用預設 editable，不加 `--no-editable` |
| 搬移期間 main 又有人合入前端改動，`web/src → web/ui/src` 的搬移衝突 | 中 | T12 前先把 main 合進來；git 的 rename 偵測能處理純搬移 |
| `uv.lock` 重生時 `claude-agent-sdk` 或其依賴漂移 | 中 | T1、T10、T14 都比對鎖檔裡的版本；有變就停下來問（SPEC：Ask first） |
| 正在跑的 Docker stack（`newsletter.ponygames.net`）被重建 | 高 | 只用 `migration-test` 標籤；`docker compose up -d` 留給使用者；永不 `down -v` |
| 跑 `newsletter-agent` 耗訂閱額度、`run_daily.sh` 會真的寄信 | 中 | 全程不跑；T7 用假的 `uv` 驗證 `run_daily.sh` 的步驟順序與 exit code |

## Open Questions（請在核可 plan 時一併回答，預設值已寫）

1. **PR 策略：** 預設**單一 PR**（每個任務一個 commit，三個 Checkpoint 當審閱點）。分成每個 Phase 一個 PR 的話，main 會停在「半搬」狀態。要不要分？
2. **commit 時機：** 預設實作時每個任務結束在本地 commit；**push 與開 PR 等您說**。可以嗎？
3. **golden 基準的 fixture：** 取主 checkout 的 `reports/2026-10-04.md`，唯讀複製進 worktree 的 `reports/`（不進版控）。可以嗎？
